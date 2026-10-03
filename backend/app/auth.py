from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update

from app.db.database import SessionLocal
from app.db.models import PasswordCredential, Session, User

SESSION_COOKIE = "pipsgox_session"
SESSION_TTL_SECONDS = 60 * 60 * 12


def _require_db():
    if SessionLocal is None:
        raise RuntimeError("PostgreSQL is not configured. Set DATABASE_URL first.")
    return SessionLocal


def _password_hash(password: str, salt: bytes | None = None) -> str:
    if not password or len(password) < 12:
        raise ValueError("Password must be at least 12 characters.")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return "scrypt$16384$8$1$" + salt.hex() + "$" + digest.hex()


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "scrypt":
            return False
        expected = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
        )
        return hmac.compare_digest(expected.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _metadata_hash(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def initialize() -> None:
    _require_db()


def has_user() -> bool:
    with _require_db()() as db:
        return db.scalar(select(User.id).limit(1)) is not None


def verify_user_password(user_id: int, password: str) -> bool:
    if not password:
        return False
    with _require_db()() as db:
        password_hash = db.scalar(
            select(PasswordCredential.password_hash).where(
                PasswordCredential.user_id == user_id
            )
        )
    return password_hash is not None and _verify_password(password, str(password_hash))


def delete_all_users() -> int:
    """Delete all PostgreSQL users and their dependent credentials/sessions."""
    with _require_db()() as db:
        result = db.execute(delete(User))
        db.commit()
        return int(result.rowcount or 0)


def create_user(username: str, password: str) -> int:
    """Create a normal PIPSGOX user and return the new user id."""
    username = username.strip()
    if not username:
        raise ValueError("Username is required.")
    if len(username) < 3:
        raise ValueError("Username must be at least 3 characters.")
    if len(username) > 64:
        raise ValueError("Username must be at most 64 characters.")
    encoded = _password_hash(password)

    with _require_db()() as db:
        existing = db.scalar(select(User.id).where(User.username == username))
        if existing is not None:
            raise ValueError("Username already exists.")

        user = User(username=username)
        db.add(user)
        db.flush()

        db.add(
            PasswordCredential(
                user_id=user.id,
                password_hash=encoded,
            )
        )
        db.commit()
        return int(user.id)


def create_initial_user(username: str, password: str) -> None:
    """Backward-compatible wrapper for older scripts."""
    create_user(username, password)


def create_session_for_user(
    user_id: int,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Create a server-side session for an already authenticated user."""
    now = datetime.now(timezone.utc)
    raw_token = secrets.token_urlsafe(48)
    expires_at = now + timedelta(seconds=SESSION_TTL_SECONDS)

    with _require_db()() as db:
        if db.get(User, user_id) is None:
            raise ValueError("User does not exist.")
        db.execute(
            update(User)
            .where(User.id == user_id)
            .values(last_login_at=now)
        )
        db.add(
            Session(
                user_id=user_id,
                session_token_hash=_token_hash(raw_token),
                expires_at=expires_at,
                last_seen_at=now,
                ip_hash=_metadata_hash(ip_address),
                user_agent=(user_agent or "")[:1024] or None,
            )
        )
        db.commit()

    return raw_token


def authenticate(
    username: str,
    password: str,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> str | None:
    username = username.strip()
    if not username or not password:
        return None

    SessionLocalFactory = _require_db()
    with SessionLocalFactory() as db:
        row = db.execute(
            select(User.id, User.username, PasswordCredential.password_hash)
            .join(
                PasswordCredential,
                PasswordCredential.user_id == User.id,
            )
            .where(User.username == username)
        ).first()

        if row is None or not _verify_password(password, str(row.password_hash)):
            return None

        user_id = int(row.id)
        now = datetime.now(timezone.utc)
        db.execute(
            update(User)
            .where(User.id == user_id)
            .values(last_login_at=now)
        )

        raw_token = secrets.token_urlsafe(48)
        expires_at = now + timedelta(seconds=SESSION_TTL_SECONDS)

        db.add(
            Session(
                user_id=user_id,
                session_token_hash=_token_hash(raw_token),
                expires_at=expires_at,
                last_seen_at=now,
                ip_hash=_metadata_hash(ip_address),
                user_agent=(user_agent or "")[:1024] or None,
            )
        )
        db.commit()

    return raw_token


def get_user(token: str | None) -> dict[str, object] | None:
    if not token:
        return None

    now = datetime.now(timezone.utc)
    token_hash = _token_hash(token)

    with _require_db()() as db:
        row = db.execute(
            select(
                Session.user_id,
                Session.expires_at,
                User.username,
                User.email,
                User.display_name,
            )
            .join(User, User.id == Session.user_id)
            .where(
                Session.session_token_hash == token_hash,
                Session.expires_at > now,
                Session.revoked_at.is_(None),
            )
        ).first()

        if row is None:
            return None

        db.execute(
            update(Session)
            .where(Session.session_token_hash == token_hash)
            .values(last_seen_at=now)
        )
        db.commit()

    return {
        "id": int(row.user_id),
        "username": str(row.username or ""),
        "email": row.email,
        "display_name": row.display_name,
        "expires_at": int(row.expires_at.timestamp()),
    }


def revoke(token: str | None) -> None:
    if not token:
        return

    with _require_db()() as db:
        db.execute(
            delete(Session).where(
                Session.session_token_hash == _token_hash(token)
            )
        )
        db.commit()


def revoke_all_sessions(user_id: int | None = None) -> int:
    with _require_db()() as db:
        statement = delete(Session)
        if user_id is not None:
            statement = statement.where(Session.user_id == user_id)
        result = db.execute(statement)
        db.commit()
        return int(result.rowcount or 0)


def revoke_all(user_id: int) -> int:
    return revoke_all_sessions(user_id)


def cleanup_expired_sessions() -> int:
    """Remove expired sessions from PostgreSQL."""
    with _require_db()() as db:
        result = db.execute(
            delete(Session).where(Session.expires_at <= datetime.now(timezone.utc))
        )
        db.commit()
        return int(result.rowcount or 0)
