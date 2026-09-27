from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.Lock()
_DEFAULT_LOG = Path(__file__).resolve().parents[2] / ".pipsgox" / "security_audit.log"


def _log_path() -> Path:
    return Path(os.getenv("PIPSGOX_SECURITY_AUDIT_LOG", str(_DEFAULT_LOG))).expanduser()


def record(event: str, *, username: str = "", account_id: int | None = None, broker: str = "", success: bool = True) -> None:
    entry = {
        "time": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "username": username,
        "account_id": account_id,
        "broker": broker,
        "success": success,
    }
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, separators=(",", ":")) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
