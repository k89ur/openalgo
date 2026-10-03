from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db.database import SessionLocal
from app.db.models import EmailVerificationToken, User
from app.email_service import send_verification_email

TOKEN_TTL_HOURS = 24
RESEND_COOLDOWN_SECONDS = 60


def _require_db():
    if SessionLocal is None:
        raise RuntimeError("PostgreSQL is not configured. Set DATABASE_URL first.")
    return SessionLocal


def _normalize_email(email: str) -> str:
    value = str(email).strip().lower()
    if len(value) > 320 or "@" not in value or value.startswith("@") or value.endswith("@"):
        raise ValueError("Enter a valid email address.")
    return value


def _hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _new_token() -> tuple[str, bytes]:
    token = secrets.token_urlsafe(32)
    return token, _hash_token(token)


def status(user_id: int) -> dict[str, object]:
    with _require_db()() as db:
        user = db.get(User, int(user_id))
        if user is None:
            raise ValueError("User does not exist.")
        return {
            "email": user.email,
            "verified": bool(user.email_verified),
        }


def set_email_and_send(user_id: int, email: str) -> dict[str, object]:
    email = _normalize_email(email)
    now = datetime.now(timezone.utc)

    with _require_db()() as db:
        user = db.get(User, int(user_id))
        if user is None:
            raise ValueError("User does not exist.")

        existing = db.scalar(
            select(User.id).where(
                User.email == email,
                User.id != int(user_id),
            )
        )
        if existing is not None:
            raise ValueError("That email address is already in use.")

        user.email = email
        user.email_verified = False
        db.execute(delete(EmailVerificationToken).where(
            EmailVerificationToken.user_id == int(user_id)
        ))

        raw_token, token_hash = _new_token()
        db.add(
            EmailVerificationToken(
                user_id=int(user_id),
                token_hash=token_hash,
                expires_at=now + timedelta(hours=TOKEN_TTL_HOURS),
                sent_at=now,
            )
        )
        db.commit()

    try:
        send_verification_email(email=email, token=raw_token)
    except Exception:
        # Do not leave a valid token behind when delivery failed.
        with _require_db()() as db:
            db.execute(delete(EmailVerificationToken).where(
                EmailVerificationToken.token_hash == token_hash
            ))
            db.commit()
        raise

    return {"email": email, "verified": False, "sent": True}


def resend(user_id: int) -> dict[str, object]:
    now = datetime.now(timezone.utc)

    with _require_db()() as db:
        user = db.get(User, int(user_id))
        if user is None:
            raise ValueError("User does not exist.")
        if not user.email:
            raise ValueError("Add an email address before requesting verification.")
        if user.email_verified:
            return {"email": user.email, "verified": True, "sent": False}

        latest = db.scalar(
            select(EmailVerificationToken)
            .where(EmailVerificationToken.user_id == int(user_id))
            .order_by(EmailVerificationToken.created_at.desc())
            .limit(1)
        )
        if latest is not None and latest.sent_at:
            elapsed = (now - latest.sent_at).total_seconds()
            if elapsed < RESEND_COOLDOWN_SECONDS:
                remaining = max(1, int(RESEND_COOLDOWN_SECONDS - elapsed))
                raise ValueError(f"Please wait {remaining} seconds before requesting another email.")

        db.execute(delete(EmailVerificationToken).where(
            EmailVerificationToken.user_id == int(user_id)
        ))
        raw_token, token_hash = _new_token()
        db.add(
            EmailVerificationToken(
                user_id=int(user_id),
                token_hash=token_hash,
                expires_at=now + timedelta(hours=TOKEN_TTL_HOURS),
                sent_at=now,
            )
        )
        email = str(user.email)
        db.commit()

    try:
        send_verification_email(email=email, token=raw_token)
    except Exception:
        with _require_db()() as db:
            db.execute(delete(EmailVerificationToken).where(
                EmailVerificationToken.token_hash == token_hash
            ))
            db.commit()
        raise

    return {"email": email, "verified": False, "sent": True}


def verify(token: str) -> dict[str, object]:
    token = str(token).strip()
    if not token or len(token) > 256:
        raise ValueError("Invalid verification link.")

    now = datetime.now(timezone.utc)
    token_hash = _hash_token(token)

    with _require_db()() as db:
        row = db.execute(
            select(EmailVerificationToken, User)
            .join(User, User.id == EmailVerificationToken.user_id)
            .where(EmailVerificationToken.token_hash == token_hash)
            .with_for_update()
        ).first()

        if row is None:
            raise ValueError("This verification link is invalid or has expired.")

        verification = row[0]
        user = row[1]

        if verification.used_at is not None or verification.expires_at <= now:
            raise ValueError("This verification link is invalid or has expired.")

        user.email_verified = True
        verification.used_at = now
        db.execute(delete(EmailVerificationToken).where(
            EmailVerificationToken.user_id == int(user.id),
            EmailVerificationToken.id != int(verification.id),
        ))
        db.commit()

        return {
            "verified": True,
            "email": user.email,
        }
