#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.pipsgox"

stop_one() {
  local name="$1" pidfile="$2"
  if [[ ! -f "$pidfile" ]]; then
    echo "$name not running"
    return
  fi

  local pid
  pid="$(cat "$pidfile" 2>/dev/null || true)"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    echo "$name not running (stale PID)"
    rm -f "$pidfile"
    return
  fi

  echo "Stopping $name (PID $pid)..."
  kill "$pid" 2>/dev/null || true

  for _ in {1..10}; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
  done

  if kill -0 "$pid" 2>/dev/null; then
    echo "$name did not stop cleanly; forcing it."
    kill -9 "$pid" 2>/dev/null || true
  fi

  rm -f "$pidfile"
  echo "$name stopped"
}

stop_one "Frontend" "$RUN_DIR/frontend.pid"
stop_one "Backend" "$RUN_DIR/backend.pid"

echo
echo "Remaining PIPSGOX ports:"
ss -ltnp 2>/dev/null | grep -E ':3001|:8000' || echo "none"
