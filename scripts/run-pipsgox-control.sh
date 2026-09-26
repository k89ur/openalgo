#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "\${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.pipsgox"
PIDFILE="$RUN_DIR/control.pid"
LOGFILE="$RUN_DIR/control.log"
PORT="\${PIPSGOX_CONTROL_PORT:-9000}"
mkdir -p "$RUN_DIR"
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  echo "STATUS: PASS — Dev Control already running (PID $(cat "$PIDFILE"))"; exit 0
fi
rm -f "$PIDFILE"; : > "$LOGFILE"
echo "ACTION: Starting PIPSGOX Dev Control on :$PORT"
(cd "$ROOT" || exit 1; exec env PIPSGOX_CONTROL_PORT="$PORT" python backend/dev_server.py) >"$LOGFILE" 2>&1 &
PID=$!; echo "$PID" > "$PIDFILE"
for _ in {1..15}; do
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1; then
    echo "STATUS: PASS — Dev Control :$PORT (PID $PID)"
    if [[ -n "\${CODESPACE_NAME:-}" ]]; then
      DOMAIN="\${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
      echo "WEB: https://\${CODESPACE_NAME}-\${PORT}.\${DOMAIN}"
    else echo "WEB: http://127.0.0.1:$PORT"; fi
    exit 0
  fi
  if ! kill -0 "$PID" 2>/dev/null; then echo "STATUS: FAIL — Dev Control exited during startup."; tail -n 80 "$LOGFILE"; rm -f "$PIDFILE"; exit 1; fi
  sleep 1
done
echo "STATUS: FAIL — Dev Control did not become ready."; tail -n 80 "$LOGFILE"; exit 1
