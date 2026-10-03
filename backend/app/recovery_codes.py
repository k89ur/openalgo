from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import delete, func, select

from app.db.database import SessionLocal
from app.db.models import RecoveryCode, TotpCredential, TotpLoginChallenge, User


RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_LENGTH = 12
RECOVERY_CODE_ATTEMPTS = 5
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _require_db():
    if SessionLocal is None:
        raise RuntimeError("PostgreSQL is not configured. Set DATABASE_URL first.")
    return SessionLocal


def _normalize(code: str) -> str:
    return "".join(str(code).strip().upper().replace("-", "").split())


def _hash(code: str) -> str:
    return hashlib.sha256(_normalize(code).encode("utf-8")).hexdigest()


def _generate_code() -> str:
    raw = "".join(
        secrets.choice(_RECOVERY_ALPHABET)
        for _ in range(RECOVERY_CODE_LENGTH)
    )
    return raw[:4] + "-" + raw[4:8] + "-" + raw[8:]


def _totp_enabled(db, user_id: int) -> bool:
    return bool(
        db.scalar(
            select(TotpCredential.id).where(
                TotpCredential.user_id == int(user_id),
                TotpCredential.enabled.is_(True),
            )
        )
    )


def status(user_id: int) -> dict[str, int]:
    with _require_db()() as db:
        count = db.scalar(
            select(func.count(RecoveryCode.id)).where(
                RecoveryCode.user_id == int(user_id),
                RecoveryCode.used_at.is_(None),
            )
        )
        return {"remaining": int(count or 0)}


def generate(user_id: int, totp_code: str, totp_verifier) -> list[str]:
    """Replace all recovery codes after verifying the current TOTP code.

    Plaintext codes are returned exactly once and are never persisted.
    """
    with _require_db()() as db:
        if not _totp_enabled(db, user_id):
            raise ValueError(
                "Enable the authenticator app before generating recovery codes."
            )

        if not totp_verifier(int(user_id), totp_code):
            raise ValueError("Invalid authenticator code.")

        db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == int(user_id)))

        generated: list[str] = []
        for _ in range(RECOVERY_CODE_COUNT):
            code = _generate_code()
            generated.append(code)
            db.add(
                RecoveryCode(
                    user_id=int(user_id),
                    code_hash=_hash(code),
                )
            )
        db.commit()
        return generated


def verify_login_code(
    challenge: str,
    code: str,
) -> tuple[int, dict[str, object]]:
    """Verify and consume a recovery code and its password-login challenge atomically."""
    if not challenge or len(challenge) > 128:
        raise ValueError("Invalid authentication challenge.")

    normalized = _normalize(code)
    if len(normalized) != RECOVERY_CODE_LENGTH:
        raise ValueError("Invalid recovery code.")

    now = datetime.now(timezone.utc)
    code_hash = _hash(normalized)

    with _require_db()() as db:
        row = db.execute(
            select(
                TotpLoginChallenge,
                User.id,
                User.username,
                User.email,
                User.display_name,
            )
            .join(User, User.id == TotpLoginChallenge.user_id)
            .where(TotpLoginChallenge.challenge_hash == _hash(challenge))
            .with_for_update()
        ).first()

        if row is None:
            raise ValueError("Authentication challenge expired. Sign in again.")

        login_challenge = row[0]

        if login_challenge.expires_at <= now:
            db.delete(login_challenge)
            db.commit()
            raise ValueError("Authentication challenge expired. Sign in again.")

        if login_challenge.attempts >= RECOVERY_CODE_ATTEMPTS:
            db.delete(login_challenge)
            db.commit()
            raise ValueError("Too many recovery attempts. Sign in again.")

        if not _totp_enabled(db, int(login_challenge.user_id)):
            db.delete(login_challenge)
            db.commit()
            raise ValueError("Authenticator setup is no longer enabled.")

        recovery = db.scalar(
            select(RecoveryCode)
            .where(
                RecoveryCode.user_id == int(login_challenge.user_id),
                RecoveryCode.code_hash == code_hash,
                RecoveryCode.used_at.is_(None),
            )
            .with_for_update()
        )

        if recovery is None:
            login_challenge.attempts = int(login_challenge.attempts) + 1
            if login_challenge.attempts >= RECOVERY_CODE_ATTEMPTS:
                db.delete(login_challenge)
            db.commit()
            raise ValueError("Invalid recovery code.")

        recovery.used_at = now
        db.delete(login_challenge)
        db.commit()

        return int(row.id), {
            "id": int(row.id),
            "username": str(row.username or ""),
            "email": row.email,
            "display_name": row.display_name,
        }


def invalidate_all(user_id: int) -> int:
    """Invalidate existing recovery codes, e.g. when TOTP is disabled."""
    with _require_db()() as db:
        result = db.execute(
            delete(RecoveryCode).where(RecoveryCode.user_id == int(user_id))
        )
        db.commit()
        return int(result.rowcount or 0)
