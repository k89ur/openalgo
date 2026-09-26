from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = ROOT / ".pipsgox"
START_SCRIPT = ROOT / "scripts" / "run-pipsgox.sh"
STOP_SCRIPT = ROOT / "scripts" / "stop-pipsgox.sh"


def _pid_running(name: str) -> bool:
    pid_file = RUN_DIR / f"{name}.pid"
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ValueError, OSError):
        return False


def status() -> dict[str, object]:
    return {
        "backend": _pid_running("backend"),
        "frontend": _pid_running("frontend"),
        "backend_pid_file": str(RUN_DIR / "backend.pid"),
        "frontend_pid_file": str(RUN_DIR / "frontend.pid"),
    }


def tail_log(name: str, lines: int = 80) -> str:
    safe_name = "backend" if name == "backend" else "frontend"
    path = RUN_DIR / f"{safe_name}.log"
    if not path.exists():
        return "Log file not found."
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        return "\n".join(content.splitlines()[-max(1, min(lines, 200)):])
    except OSError as exc:
        return f"Could not read log: {exc}"


def start() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        ["bash", str(START_SCRIPT)],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def stop() -> None:
    subprocess.Popen(
        ["bash", str(STOP_SCRIPT)],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def restart() -> None:
    # The current backend process is one of the processes being restarted.
    # Run both scripts from a detached session so the restart survives the
    # HTTP request/process that triggered it.
    command = (
        f"sleep 1; bash {STOP_SCRIPT!s}; "
        f"sleep 1; bash {START_SCRIPT!s}"
    )
    subprocess.Popen(
        ["bash", "-lc", command],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
