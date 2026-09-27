from __future__ import annotations

import hashlib
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


def _cipher() -> Any:
    if Fernet is None:
        raise RuntimeError("Broker credential encryption requires the cryptography package.")
    key = os.getenv("PIPSGOX_BROKER_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "PIPSGOX_BROKER_ENCRYPTION_KEY is not configured. "
            "Generate a Fernet key before saving broker credentials."
        )
    try:
        return Fernet(key.encode("ascii"))
    except Exception as exc:
        raise RuntimeError("PIPSGOX_BROKER_ENCRYPTION_KEY is invalid.") from exc


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS broker_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker TEXT NOT NULL,
            account_name TEXT NOT NULL,
            client_id TEXT NOT NULL DEFAULT '',
            secret_blob TEXT NOT NULL DEFAULT '',
            access_token_blob TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'disconnected',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.commit()
    return connection


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


def create_account(broker: str, account_name: str, client_id: str, api_secret: str) -> BrokerAccount:
    broker = broker.strip().lower()
    account_name = account_name.strip()
    client_id = client_id.strip()
    api_secret = api_secret.strip()
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
            INSERT INTO broker_accounts (broker, account_name, client_id, secret_blob)
            VALUES (?, ?, ?, ?)
            """,
            (broker, account_name, client_id, secret_blob),
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
