#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.pipsgox"
for item in "Frontend:$RUN_DIR/frontend.pid" "Backend:$RUN_DIR/backend.pid"; do
  name="${item%%:*}"; pidfile="${item#*:}"
  if [[ -f "$pidfile" ]]; then
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null || true; echo "$name stopped"; else echo "$name not running"; fi
    rm -f "$pidfile"
  else echo "$name not running"; fi
done
