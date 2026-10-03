from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app import auth
from app.db.database import SessionLocal
from app.db.models import PasswordCredential, PasswordResetToken, Session, User
from app.email_service import send_password_reset_email

TOKEN_TTL_MINUTES = 30
REQUEST_COOLDOWN_SECONDS = 60


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


def request_reset(email: str) -> dict[str, object]:
    """Create and email a reset token when the account is eligible.

    The caller must return the same user-facing response whether or not the
    address belongs to an account, preventing account enumeration.
    """
    normalized = _normalize_email(email)
    now = datetime.now(timezone.utc)

    with _require_db()() as db:
        user = db.scalar(
            select(User)
            .where(
                User.email == normalized,
                User.email_verified.is_(True),
                User.status == "active",
            )
        )

        if user is None:
            return {"sent": False}

        latest = db.scalar(
            select(PasswordResetToken)
            .where(PasswordResetToken.user_id == int(user.id))
            .order_by(PasswordResetToken.requested_at.desc())
            .limit(1)
        )
        if latest is not None:
            elapsed = (now - latest.requested_at).total_seconds()
            if elapsed < REQUEST_COOLDOWN_SECONDS:
                return {"sent": False}

        db.execute(
            delete(PasswordResetToken).where(
                PasswordResetToken.user_id == int(user.id)
            )
        )

        raw_token, token_hash = _new_token()
        db.add(
            PasswordResetToken(
                user_id=int(user.id),
                token_hash=token_hash,
                expires_at=now + timedelta(minutes=TOKEN_TTL_MINUTES),
                requested_at=now,
            )
        )
        db.commit()

        user_email = str(user.email)

    try:
        send_password_reset_email(email=user_email, token=raw_token)
    except Exception:
        with _require_db()() as db:
            db.execute(
                delete(PasswordResetToken).where(
                    PasswordResetToken.token_hash == token_hash
                )
            )
            db.commit()
        raise

    return {"sent": True}


def confirm_reset(token: str, new_password: str) -> dict[str, object]:
    """Consume a valid reset token, replace the password, and revoke sessions."""
    token = str(token).strip()
    if not token or len(token) > 256:
        raise ValueError("Invalid password reset link.")

    token_hash = _hash_token(token)
    now = datetime.now(timezone.utc)

    with _require_db()() as db:
        row = db.execute(
            select(PasswordResetToken, User)
            .join(User, User.id == PasswordResetToken.user_id)
            .where(PasswordResetToken.token_hash == token_hash)
            .with_for_update()
        ).first()

        if row is None:
            raise ValueError("This password reset link is invalid or has expired.")

        reset = row[0]
        user = row[1]

        if reset.used_at is not None or reset.expires_at <= now or user.status != "active":
            raise ValueError("This password reset link is invalid or has expired.")

        encoded_password = auth._password_hash(new_password)

        credential = db.scalar(
            select(PasswordCredential)
            .where(PasswordCredential.user_id == int(user.id))
            .with_for_update()
        )
        if credential is None:
            credential = PasswordCredential(
                user_id=int(user.id),
                password_hash=encoded_password,
                password_changed_at=now,
            )
            db.add(credential)
        else:
            credential.password_hash = encoded_password
            credential.password_changed_at = now

        reset.used_at = now
        db.execute(
            delete(PasswordResetToken).where(
                PasswordResetToken.user_id == int(user.id),
                PasswordResetToken.id != int(reset.id),
            )
        )

        db.execute(
            delete(Session).where(Session.user_id == int(user.id))
        )
        db.commit()

        return {
            "reset": True,
            "username": str(user.username or ""),
        }


def create_dev_token(identifier: str) -> str:
    """Create a local-development reset token without sending email.

    This function is intended only for a localhost development route that is
    separately disabled by configuration in production.
    """
    value = str(identifier).strip().lower()
    if not value:
        raise ValueError("Email address is required.")

    now = datetime.now(timezone.utc)
    with _require_db()() as db:
        user = db.scalar(
            select(User).where(
                User.status == "active",
                (User.email == value) | (User.username == value),
            )
        )
        if user is None:
            raise ValueError("No matching active account.")

        db.execute(
            delete(PasswordResetToken).where(
                PasswordResetToken.user_id == int(user.id)
            )
        )
        raw_token, token_hash = _new_token()
        db.add(
            PasswordResetToken(
                user_id=int(user.id),
                token_hash=token_hash,
                expires_at=now + timedelta(minutes=TOKEN_TTL_MINUTES),
                requested_at=now,
            )
        )
        db.commit()
        return raw_token
