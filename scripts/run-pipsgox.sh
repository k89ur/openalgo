#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.pipsgox"
mkdir -p "$RUN_DIR"
start() {
  local name="$1" pidfile="$2" logfile="$3" cmd="$4" port="$5" url="$6"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "$name already running (PID $(cat "$pidfile"))"
  else
    rm -f "$pidfile"
    echo "Starting $name..."
    bash -lc "cd '$ROOT' && $cmd" >"$logfile" 2>&1 &
    echo $! >"$pidfile"
  fi
  for _ in {1..30}; do
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then echo "✓ $name :$port"; return 0; fi
    sleep 1
  done
  echo "✗ $name did not become ready; see $logfile"
  return 1
}
echo
echo "PIPSGOX DEV SERVER"
echo "=================="
start "Backend" "$RUN_DIR/backend.pid" "$RUN_DIR/backend.log" "cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000" "8000" "http://127.0.0.1:8000/health"
start "Frontend" "$RUN_DIR/frontend.pid" "$RUN_DIR/frontend.log" "cd frontend && npm run dev -- --host 0.0.0.0 --port 3001" "3001" "http://127.0.0.1:3001/"
echo
echo "Health:"
curl -fsS http://127.0.0.1:8000/health || true
echo
echo "Web: http://127.0.0.1:3001"
echo "API: http://127.0.0.1:8000"
