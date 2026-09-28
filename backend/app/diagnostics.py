from __future__ import annotations

import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

_DB_LOCK = threading.Lock()
_DEFAULT_DB = Path(__file__).resolve().parents[2] / ".pipsgox" / "diagnostics.db"

_SECRET_PATTERNS = (
    (re.compile(r"(?i)(access[_ -]?token|api[_ -]?secret|secret|password|authorization)\s*[:=]\s*[^\s,;]+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
)


def _db_path() -> Path:
    return Path(os.getenv("PIPSGOX_DIAGNOSTICS_DB", str(_DEFAULT_DB))).expanduser()


def sanitize(value: object) -> str:
    text = str(value or "")
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:12000]


def initialize() -> None:
    with _DB_LOCK:
        connection = _connect()
        connection.close()


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS diagnostic_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            broker TEXT NOT NULL DEFAULT '',
            severity TEXT NOT NULL,
            category TEXT NOT NULL,
            component TEXT NOT NULL,
            service TEXT NOT NULL DEFAULT '',
            error_code TEXT NOT NULL,
            symbol TEXT NOT NULL DEFAULT '',
            http_status INTEGER,
            provider_code TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL,
            technical_detail TEXT NOT NULL DEFAULT '',
            resolved INTEGER NOT NULL DEFAULT 0,
            occurrence_count INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_diagnostic_events_account ON diagnostic_events(account_id, last_seen DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_diagnostic_events_active ON diagnostic_events(resolved, last_seen DESC)"
    )
    connection.commit()
    return connection


def record(
    *,
    severity: str,
    category: str,
    component: str,
    error_code: str,
    message: str,
    account_id: int | None = None,
    broker: str = "",
    service: str = "",
    symbol: str = "",
    http_status: int | None = None,
    provider_code: str = "",
    technical_detail: str = "",
) -> dict[str, Any]:
    severity = severity.upper()
    message = sanitize(message)
    technical_detail = sanitize(technical_detail)
    broker = sanitize(broker)
    service = sanitize(service)
    symbol = sanitize(symbol)
    provider_code = sanitize(provider_code)
    fingerprint = (
        account_id, broker, severity, category, component, service,
        error_code, symbol, provider_code, message,
    )

    with _DB_LOCK:
        connection = _connect()
        row = connection.execute(
            """
            SELECT id FROM diagnostic_events
            WHERE account_id IS ? AND broker = ? AND severity = ?
              AND category = ? AND component = ? AND service = ?
              AND error_code = ? AND symbol = ? AND provider_code = ?
              AND message = ? AND resolved = 0
            ORDER BY id DESC LIMIT 1
            """,
            fingerprint,
        ).fetchone()
        if row:
            event_id = int(row["id"])
            connection.execute(
                """
                UPDATE diagnostic_events
                SET occurrence_count = occurrence_count + 1,
                    last_seen = CURRENT_TIMESTAMP,
                    technical_detail = ?,
                    http_status = ?
                WHERE id = ?
                """,
                (technical_detail, http_status, event_id),
            )
        else:
            cursor = connection.execute(
                """
                INSERT INTO diagnostic_events
                    (account_id, broker, severity, category, component, service,
                     error_code, symbol, http_status, provider_code, message,
                     technical_detail, resolved)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    account_id, broker, severity, category, component, service,
                    error_code, symbol, http_status, provider_code, message,
                    technical_detail,
                ),
            )
            event_id = int(cursor.lastrowid)
        connection.commit()
        result = connection.execute(
            "SELECT * FROM diagnostic_events WHERE id = ?", (event_id,)
        ).fetchone()
        connection.close()

    return _serialize(result)


def resolve(
    *,
    error_code: str,
    account_id: int | None = None,
    symbol: str = "",
) -> int:
    with _DB_LOCK:
        connection = _connect()
        cursor = connection.execute(
            """
            UPDATE diagnostic_events
            SET resolved = 1, last_seen = CURRENT_TIMESTAMP
            WHERE error_code = ? AND account_id IS ? AND symbol = ? AND resolved = 0
            """,
            (error_code, account_id, sanitize(symbol)),
        )
        connection.commit()
        connection.close()
    return cursor.rowcount


def list_events(
    *,
    account_id: int | None = None,
    limit: int = 100,
    include_resolved: bool = True,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    with _DB_LOCK:
        connection = _connect()
        where = []
        params: list[Any] = []
        if account_id is not None:
            where.append("account_id = ?")
            params.append(account_id)
        if not include_resolved:
            where.append("resolved = 0")
        sql = "SELECT * FROM diagnostic_events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY last_seen DESC LIMIT ?"
        params.append(limit)
        rows = connection.execute(sql, params).fetchall()
        connection.close()
    return [_serialize(row) for row in rows]


def summary(account_id: int | None = None) -> dict[str, Any]:
    with _DB_LOCK:
        connection = _connect()
        where = "WHERE account_id = ?" if account_id is not None else ""
        params = (account_id,) if account_id is not None else ()
        rows = connection.execute(
            f"""
            SELECT severity, COUNT(*) AS events, COALESCE(SUM(occurrence_count), 0) AS occurrences
            FROM diagnostic_events
            {where}
            AND resolved = 0
            GROUP BY severity
            """ if where else
            """
            SELECT severity, COUNT(*) AS events, COALESCE(SUM(occurrence_count), 0) AS occurrences
            FROM diagnostic_events
            WHERE resolved = 0
            GROUP BY severity
            """,
            params,
        ).fetchall()
        connection.close()

    counts = {str(row["severity"]): {"events": int(row["events"]), "occurrences": int(row["occurrences"])} for row in rows}
    critical = counts.get("CRITICAL", {"events": 0, "occurrences": 0})["events"]
    errors = counts.get("ERROR", {"events": 0, "occurrences": 0})["events"]
    warnings = counts.get("WARNING", {"events": 0, "occurrences": 0})["events"]
    status = "FAILED" if critical or errors else "DEGRADED" if warnings else "HEALTHY"
    return {
        "status": status,
        "critical": critical,
        "errors": errors,
        "warnings": warnings,
        "counts": counts,
    }


def _serialize(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    result = dict(row)
    result["resolved"] = bool(result.get("resolved"))
    return result
