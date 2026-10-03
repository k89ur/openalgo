from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.db.models import RecoveryCode, TotpCredential, User


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


def _totp_enabled(db: Session, user_id: int) -> bool:
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
            raise ValueError("Enable the authenticator app before generating recovery codes.")

        credential = db.scalar(
            select(TotpCredential).where(
                TotpCredential.user_id == int(user_id),
                TotpCredential.enabled.is_(True),
            )
        )
        if credential is None:
            raise ValueError("Enable the authenticator app before generating recovery codes.")

        from app.totp_service import _decrypt_secret  # local import avoids module-cycle coupling
        secret = _decrypt_secret(str(credential.secret_encrypted))
        if not totp_verifier(secret, totp_code):
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
    *,
    challenge_user_id: int,
    attempts: int,
    now: datetime | None = None,
) -> tuple[int, dict[str, object]] | None:
    """Consume one recovery code for a TOTP login challenge.

    Returns None when the code does not match. The caller owns challenge
    attempt counting so TOTP and recovery-code paths share one limit.
    """
    normalized = _normalize(code)
    if len(normalized) != RECOVERY_CODE_LENGTH:
        return None

    now = now or datetime.now(timezone.utc)
    code_hash = _hash(normalized)

    with _require_db()() as db:
        row = db.execute(
            select(RecoveryCode, User.username, User.email, User.display_name)
            .join(User, User.id == RecoveryCode.user_id)
            .where(
                RecoveryCode.user_id == int(challenge_user_id),
                RecoveryCode.code_hash == code_hash,
                RecoveryCode.used_at.is_(None),
            )
            .with_for_update()
        ).first()
        if row is None:
            return None

        recovery = row[0]
        if not hmac.compare_digest(str(recovery.code_hash), code_hash):
            return None

        recovery.used_at = now
        db.commit()

        return int(challenge_user_id), {
            "id": int(challenge_user_id),
            "username": str(row.username or ""),
            "email": row.email,
            "display_name": row.display_name,
        }


def mark_all_unused_used(user_id: int) -> int:
    """Invalidate existing recovery codes, e.g. when TOTP is disabled."""
    with _require_db()() as db:
        result = db.execute(
            delete(RecoveryCode).where(RecoveryCode.user_id == int(user_id))
        )
        db.commit()
        return int(result.rowcount or 0)
