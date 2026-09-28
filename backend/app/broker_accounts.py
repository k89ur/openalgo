from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from cryptography.fernet import Fernet
except ImportError:  # pragma: no cover
    Fernet = None  # type: ignore[assignment]

_DB_LOCK = threading.Lock()
_DEFAULT_DB = Path(__file__).resolve().parents[2] / ".pipsgox" / "broker_accounts.db"
_DEFAULT_KEY_FILE = Path(__file__).resolve().parents[2] / ".pipsgox" / "broker_encryption.key"


@dataclass(frozen=True)
class BrokerAccount:
    id: int
    broker: str
    account_name: str
    client_id: str
    status: str
    created_at: str
    updated_at: str


def _db_path() -> Path:
    return Path(os.getenv("PIPSGOX_BROKER_DB", str(_DEFAULT_DB))).expanduser()


def _encryption_key() -> str:
    configured = os.getenv("PIPSGOX_BROKER_ENCRYPTION_KEY", "").strip()
    if configured:
        return configured

    path = Path(
        os.getenv("PIPSGOX_BROKER_ENCRYPTION_KEY_FILE", str(_DEFAULT_KEY_FILE))
    ).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        key = path.read_text(encoding="ascii").strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError("Could not read the broker encryption key file.") from exc

    if Fernet is None:
        raise RuntimeError("Broker credential encryption requires the cryptography package.")

    key = Fernet.generate_key().decode("ascii")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, key.encode("ascii"))
        finally:
            os.close(fd)
    except FileExistsError:
        # Another worker created it first; use the persisted key so every
        # process encrypts/decrypts with the same secret.
        try:
            key = path.read_text(encoding="ascii").strip()
        except OSError as exc:
            raise RuntimeError("Could not read the broker encryption key file.") from exc
    except OSError as exc:
        raise RuntimeError("Could not create the broker encryption key file.") from exc

    if not key:
        raise RuntimeError("Broker encryption key file is empty.")
    return key


def _cipher() -> Any:
    if Fernet is None:
        raise RuntimeError("Broker credential encryption requires the cryptography package.")
    key = _encryption_key()
    try:
        return Fernet(key.encode("ascii"))
    except Exception as exc:
        raise RuntimeError("Broker encryption key is invalid.") from exc


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
connection.execute(
        """
        CREATE TABLE IF NOT EXISTS broker_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker TEXT NOT NULL,
            account_name TEXT NOT NULL,
            client_id TEXT NOT NULL DEFAULT '',
            api_key TEXT NOT NULL DEFAULT '',
            secret_blob TEXT NOT NULL DEFAULT '',
            access_token_blob TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'disconnected',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(broker_accounts)").fetchall()}
    if "api_key" not in columns:
        connection.execute("ALTER TABLE broker_accounts ADD COLUMN api_key TEXT NOT NULL DEFAULT ''")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS broker_oauth_states (
            state TEXT PRIMARY KEY,
            account_id INTEGER NOT NULL,
            broker TEXT NOT NULL,
            session_hash TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (account_id) REFERENCES broker_accounts(id) ON DELETE CASCADE
        )
        """
    )
    connection.commit()
    return connection


def create_oauth_state(
    state: str,
    account_id: int,
    broker: str,
    session_hash: str,
    expires_at: int,
) -> None:
    if not state or not session_hash:
        raise ValueError("OAuth state is required.")
    with _DB_LOCK:
        connection = _connect()
        connection.execute(
            "DELETE FROM broker_oauth_states WHERE expires_at <= ?",
            (__import__("time").time(),),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO broker_oauth_states
                (state, account_id, broker, session_hash, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (state, account_id, broker, session_hash, int(expires_at)),
        )
        connection.commit()
        connection.close()


def consume_oauth_state(state: str) -> tuple[int, str, str] | None:
    if not state:
        return None
    with _DB_LOCK:
        connection = _connect()
        row = connection.execute(
            """
            SELECT account_id, broker, session_hash, expires_at
            FROM broker_oauth_states
            WHERE state = ?
            """,
            (state,),
        ).fetchone()
        if row is None:
            connection.close()
            return None

        connection.execute("DELETE FROM broker_oauth_states WHERE state = ?", (state,))
        connection.commit()
        connection.close()

    if int(row["expires_at"]) <= int(__import__("time").time()):
        return None
    return int(row["account_id"]), str(row["broker"]), str(row["session_hash"])


def consume_latest_oauth_state_for_session(
    broker: str, session_hash: str
) -> tuple[int, str, str] | None:
    """Consume the latest pending OAuth state bound to this app session."""
    if not broker or not session_hash:
        return None
    now = int(__import__("time").time())
    with _DB_LOCK:
        connection = _connect()
        connection.execute(
            "DELETE FROM broker_oauth_states WHERE expires_at <= ?",
            (now,),
        )
        row = connection.execute(
            """
            SELECT state, account_id, broker, session_hash, expires_at
            FROM broker_oauth_states
            WHERE broker = ? AND session_hash = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (broker.strip().lower(), session_hash),
        ).fetchone()
        if row is None:
            connection.close()
            return None
        connection.execute(
            "DELETE FROM broker_oauth_states WHERE state = ?",
            (row["state"],),
        )
        connection.commit()
        connection.close()
    return int(row["account_id"]), str(row["broker"]), str(row["session_hash"])


def claim_order_correlation(account_id: int, correlation_id: str) -> bool:
    value = correlation_id.strip()
    if not value or len(value) > 64:
        return False
    with _DB_LOCK:
        connection = _connect()
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS order_idempotency (
                account_id INTEGER NOT NULL,
                correlation_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (account_id, correlation_id),
                FOREIGN KEY (account_id) REFERENCES broker_accounts(id) ON DELETE CASCADE
            )
            """
        )
        cursor = connection.execute(
            "INSERT OR IGNORE INTO order_idempotency (account_id, correlation_id) VALUES (?, ?)",
            (account_id, value),
        )
        connection.commit()
        connection.close()
    return cursor.rowcount == 1


def initialize() -> None:
    with _DB_LOCK:
        connection = _connect()
        connection.close()


def list_accounts() -> list[BrokerAccount]:
    with _DB_LOCK:
        connection = _connect()
        rows = connection.execute(
            """
            SELECT id, broker, account_name, client_id, status, created_at, updated_at
            FROM broker_accounts ORDER BY id
            """
        ).fetchall()
        connection.close()
    return [BrokerAccount(**dict(row)) for row in rows]


def create_account(broker: str, account_name: str, client_id: str, api_secret: str, api_key: str = "") -> BrokerAccount:
    broker = broker.strip().lower()
    account_name = account_name.strip()
    client_id = client_id.strip()
    api_secret = api_secret.strip()
    api_key = api_key.strip()
    if not broker or not account_name:
        raise ValueError("Broker and account name are required.")
    if not api_secret:
        raise ValueError("API secret is required.")

    cipher = _cipher()
    secret_blob = cipher.encrypt(api_secret.encode("utf-8")).decode("ascii")

    with _DB_LOCK:
        connection = _connect()
        cursor = connection.execute(
            """
            INSERT INTO broker_accounts (broker, account_name, client_id, api_key, secret_blob)
            VALUES (?, ?, ?, ?, ?)
            """,
            (broker, account_name, client_id, cipher.encrypt(api_key.encode("utf-8")).decode("ascii") if api_key else "", secret_blob),
        )
        connection.commit()
        row = connection.execute(
            """
            SELECT id, broker, account_name, client_id, status, created_at, updated_at
            FROM broker_accounts WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
        connection.close()
    return BrokerAccount(**dict(row))


def reset_all() -> dict[str, int]:
    """Clear all broker configuration owned by this single-owner installation."""
    with _DB_LOCK:
        connection = _connect()
        counts: dict[str, int] = {}
        for table in ("broker_oauth_states", "order_idempotency", "broker_accounts"):
            cursor = connection.execute("DELETE FROM " + table)
            counts[table] = cursor.rowcount
        connection.commit()
        connection.close()
    return {
        "broker_accounts": counts["broker_accounts"],
        "oauth_states": counts["broker_oauth_states"],
        "order_correlations": counts["order_idempotency"],
    }


def delete_account(account_id: int) -> bool:
    with _DB_LOCK:
        connection = _connect()
        cursor = connection.execute("DELETE FROM broker_accounts WHERE id = ?", (account_id,))
        connection.commit()
        connection.close()
    return cursor.rowcount > 0


def mask_client_id(client_id: str) -> str:
    value = client_id.strip()
    return "••••" if len(value) <= 4 else f"••••{value[-4:]}"


def generate_encryption_key() -> str:
    if Fernet is None:
        raise RuntimeError("Install the cryptography package first.")
    return Fernet.generate_key().decode("ascii")


def get_account_credentials(account_id: int) -> tuple[BrokerAccount, str, str, str]:
    cipher = _cipher()
    with _DB_LOCK:
        connection = _connect()
        row = connection.execute(
            """
            SELECT id, broker, account_name, client_id, api_key, secret_blob, status, created_at, updated_at
            FROM broker_accounts WHERE id = ?
            """,
            (account_id,),
        ).fetchone()
        connection.close()

    if row is None:
        raise ValueError("Broker account not found.")

    try:
        secret = cipher.decrypt(str(row["secret_blob"]).encode("ascii")).decode("utf-8")
        api_key_blob = str(row["api_key"] or "")
        api_key = cipher.decrypt(api_key_blob.encode("ascii")).decode("utf-8") if api_key_blob else ""
    except Exception as exc:
        raise RuntimeError("Could not decrypt broker account credentials.") from exc

    return (
        BrokerAccount(
            id=int(row["id"]),
            broker=str(row["broker"]),
            account_name=str(row["account_name"]),
            client_id=str(row["client_id"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        ),
        str(row["client_id"]),
        api_key,
        secret,
    )


def set_access_token(account_id: int, access_token: str, status: str = "connected") -> None:
    token = access_token.strip()
    if not token:
        raise ValueError("Access token is required.")
    cipher = _cipher()
    token_blob = cipher.encrypt(token.encode("utf-8")).decode("ascii")

    with _DB_LOCK:
        connection = _connect()
        cursor = connection.execute(
            """
            UPDATE broker_accounts
            SET access_token_blob = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (token_blob, status, account_id),
        )
        connection.commit()
        connection.close()
    if cursor.rowcount == 0:
        raise ValueError("Broker account not found.")


def get_access_token(account_id: int) -> str:
    cipher = _cipher()
    with _DB_LOCK:
        connection = _connect()
        row = connection.execute(
            "SELECT access_token_blob FROM broker_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        connection.close()

    if row is None:
        raise ValueError("Broker account not found.")
    blob = str(row["access_token_blob"] or "")
    if not blob:
        return ""
    try:
        return cipher.decrypt(blob.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise RuntimeError("Could not decrypt broker access token.") from exc


def set_status(account_id: int, status: str) -> None:
    with _DB_LOCK:
        connection = _connect()
        cursor = connection.execute(
            """
            UPDATE broker_accounts
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, account_id),
        )
        connection.commit()
        connection.close()
    if cursor.rowcount == 0:
        raise ValueError("Broker account not found.")


def disconnect_all() -> int:
    """Invalidate every stored broker access token and mark accounts disconnected."""
    with _DB_LOCK:
        connection = _connect()
        cursor = connection.execute(
            """
            UPDATE broker_accounts
            SET access_token_blob = '', status = 'disconnected', updated_at = CURRENT_TIMESTAMP
            WHERE access_token_blob <> ''
            """
        )
        connection.commit()
        connection.close()
    return cursor.rowcount
