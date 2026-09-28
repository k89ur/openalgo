from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

_DB_LOCK = threading.Lock()
_DEFAULT_DB = Path(__file__).resolve().parents[2] / ".pipsgox" / "auth.db"
SESSION_COOKIE = "pipsgox_session"
SESSION_TTL_SECONDS = 60 * 60 * 12


def _db_path() -> Path:
    return Path(os.getenv("PIPSGOX_AUTH_DB", str(_DEFAULT_DB))).expanduser()


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
connection.execute("""
        CREATE TABLE IF NOT EXISTS auth_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES auth_users(id) ON DELETE CASCADE
        )
    """)
    connection.commit()
    return connection


def _password_hash(password: str, salt: bytes | None = None) -> str:
    if not password or len(password) < 12:
        raise ValueError("Password must be at least 12 characters.")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
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
            n=int(n), r=int(r), p=int(p), dklen=32,
        )
        return hmac.compare_digest(expected.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def initialize() -> None:
    with _DB_LOCK:
        connection = _connect()
        connection.execute("PRAGMA foreign_keys = ON")
        connection.close()


def has_user() -> bool:
    with _DB_LOCK:
        connection = _connect()
        row = connection.execute("SELECT 1 FROM auth_users LIMIT 1").fetchone()
        connection.close()
    return row is not None


def verify_user_password(user_id: int, password: str) -> bool:
    if not password:
        return False
    with _DB_LOCK:
        connection = _connect()
        row = connection.execute(
            "SELECT password_hash FROM auth_users WHERE id = ?",
            (user_id,),
        ).fetchone()
        connection.close()
    return row is not None and _verify_password(password, str(row["password_hash"]))


def delete_all_users() -> int:
    """Delete all owner accounts and their sessions."""
    with _DB_LOCK:
        connection = _connect()
        cursor = connection.execute("DELETE FROM auth_users")
        connection.commit()
        connection.close()
    return cursor.rowcount


def create_initial_user(username: str, password: str) -> None:
    username = username.strip()
    if not username:
        raise ValueError("Username is required.")
    encoded = _password_hash(password)
    with _DB_LOCK:
        connection = _connect()
        try:
            connection.execute(
                "INSERT INTO auth_users (username, password_hash) VALUES (?, ?)",
                (username, encoded),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("Username already exists.") from exc
        finally:
            connection.close()


def authenticate(username: str, password: str) -> str | None:
    username = username.strip()
    with _DB_LOCK:
        connection = _connect()
        row = connection.execute(
            "SELECT id, password_hash FROM auth_users WHERE username = ?",
            (username,),
        ).fetchone()
        connection.close()

    if row is None or not _verify_password(password, str(row["password_hash"])):
        return None

    raw_token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expires_at = int(time.time()) + SESSION_TTL_SECONDS

    with _DB_LOCK:
        connection = _connect()
        connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (int(time.time()),))
        connection.execute(
            "INSERT INTO auth_sessions (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
            (int(row["id"]), token_hash, expires_at),
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
            """SELECT u.id, u.username, s.expires_at
               FROM auth_sessions s JOIN auth_users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.expires_at > ?""",
            (token_hash, now),
        ).fetchone()
        connection.close()
    if row is None:
        return None
    return {"id": int(row["id"]), "username": str(row["username"]), "expires_at": int(row["expires_at"])}


def revoke(token: str | None) -> None:
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _DB_LOCK:
        connection = _connect()
        connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
        connection.commit()
        connection.close()


def revoke_all(user_id: int) -> int:
    with _DB_LOCK:
        connection = _connect()
        cursor = connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
        connection.commit()
        connection.close()
    return cursor.rowcount
