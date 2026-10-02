from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from sqlalchemy import select, update

from app.db.database import SessionLocal
from app.db.models import PasswordCredential, User

_DB_LOCK = threading.Lock()
_DEFAULT_DB = Path(__file__).resolve().parents[2] / ".pipsgox" / "auth.db"
SESSION_COOKIE = "pipsgox_session"
SESSION_TTL_SECONDS = 60 * 60 * 12


def _db_path() -> Path:
    return Path(os.getenv("PIPSGOX_AUTH_DB", str(_DEFAULT_DB))).expanduser()


def _connect() -> sqlite3.Connection:
    """Temporary SQLite store for sessions during the auth migration.

    User accounts and password hashes now live in PostgreSQL. Sessions will
    move to PostgreSQL in the dedicated sessions phase.
    """
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.commit()
    return connection


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


def initialize() -> None:
    if SessionLocal is None:
        raise RuntimeError("PostgreSQL is not configured. Set DATABASE_URL first.")
    with _DB_LOCK:
        connection = _connect()
        connection.close()


def has_user() -> bool:
    if SessionLocal is None:
        raise RuntimeError("PostgreSQL is not configured. Set DATABASE_URL first.")
    with SessionLocal() as db:
        return db.scalar(select(User.id).limit(1)) is not None


def verify_user_password(user_id: int, password: str) -> bool:
    if not password or SessionLocal is None:
        return False
    with SessionLocal() as db:
        password_hash = db.scalar(
            select(PasswordCredential.password_hash).where(
                PasswordCredential.user_id == user_id
            )
        )
    return password_hash is not None and _verify_password(password, str(password_hash))


def delete_all_users() -> int:
    """Delete all PostgreSQL users and their password credentials.

    Session rows are cleared as part of the transitional SQLite session store.
    """
    if SessionLocal is None:
        raise RuntimeError("PostgreSQL is not configured. Set DATABASE_URL first.")
    with SessionLocal() as db:
        count = db.query(User).delete()
        db.commit()
    revoke_all_sessions()
    return int(count)


def create_initial_user(username: str, password: str) -> None:
    if SessionLocal is None:
        raise RuntimeError("PostgreSQL is not configured. Set DATABASE_URL first.")
    username = username.strip()
    if not username:
        raise ValueError("Username is required.")
    encoded = _password_hash(password)

    with SessionLocal() as db:
        existing = db.scalar(select(User.id).where(User.username == username))
        if existing is not None:
            raise ValueError("Username already exists.")

        user = User(username=username)
        db.add(user)
        db.flush()

        credential = PasswordCredential(
            user_id=user.id,
            password_hash=encoded,
        )
        db.add(credential)
        db.commit()


def authenticate(username: str, password: str) -> str | None:
    if SessionLocal is None:
        raise RuntimeError("PostgreSQL is not configured. Set DATABASE_URL first.")

    username = username.strip()
    with SessionLocal() as db:
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
        db.execute(
            update(User)
            .where(User.id == user_id)
            .values(last_login_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        )
        db.commit()

    raw_token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expires_at = int(time.time()) + SESSION_TTL_SECONDS

    with _DB_LOCK:
        connection = _connect()
        connection.execute(
            "DELETE FROM auth_sessions WHERE expires_at <= ?",
            (int(time.time()),),
        )
        connection.execute(
            "INSERT INTO auth_sessions (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
            (user_id, token_hash, expires_at),
        )
        connection.commit()
        connection.close()
    return raw_token


def get_user(token: str | None) -> dict[str, object] | None:
    if not token:
        return None

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = int(time.time())

    with _DB_LOCK:
        connection = _connect()
        row = connection.execute(
            """
            SELECT user_id, expires_at
            FROM auth_sessions
            WHERE token_hash = ? AND expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
        connection.close()

    if row is None or SessionLocal is None:
        return None

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.id == int(row["user_id"])))

    if user is None:
        revoke(token)
        return None

    return {
        "id": int(user.id),
        "username": str(user.username or ""),
        "email": user.email,
        "display_name": user.display_name,
        "expires_at": int(row["expires_at"]),
    }


def revoke(token: str | None) -> None:
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _DB_LOCK:
        connection = _connect()
        connection.execute(
            "DELETE FROM auth_sessions WHERE token_hash = ?",
            (token_hash,),
        )
        connection.commit()
        connection.close()


def revoke_all_sessions(user_id: int | None = None) -> int:
    with _DB_LOCK:
        connection = _connect()
        if user_id is None:
            cursor = connection.execute("DELETE FROM auth_sessions")
        else:
            cursor = connection.execute(
                "DELETE FROM auth_sessions WHERE user_id = ?",
                (user_id,),
            )
        connection.commit()
        connection.close()
    return cursor.rowcount


def revoke_all(user_id: int) -> int:
    return revoke_all_sessions(user_id)
