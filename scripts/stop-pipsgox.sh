#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "\${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.pipsgox"

port_pids() {
  local port="$1"
  ss -ltnp 2>/dev/null \
    | grep -E ":$port([[:space:]]|$)" \
    | grep -oE 'pid=[0-9]+' \
    | cut -d= -f2 \
    | sort -u
}

process_matches_service() {
  local pid="$1" service="$2" cmd=""
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
  case "$service" in
    frontend) grep -Eq 'npm run dev|vite|node.*3001' <<<"$cmd" ;;
    backend) grep -Eq 'uvicorn.*app\.main:app|python.*uvicorn.*8000' <<<"$cmd" ;;
    *) return 1 ;;
  esac
}

stop_one() {
  local name="$1" pidfile="$2" port="$3" service="$4"
  local pids="" pid found=0

  if [[ -f "$pidfile" ]]; then
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      pids="$pid"
    fi
  fi

  if [[ -z "$pids" ]]; then
    pids="$(port_pids "$port" || true)"
  fi

  if [[ -z "$pids" ]]; then
    echo "$name not running"
    rm -f "$pidfile"
    return
  fi

  for pid in $pids; do
    if ! kill -0 "$pid" 2>/dev/null; then
      continue
    fi
    if ! process_matches_service "$pid" "$service"; then
      echo "WARNING: :$port is occupied by an unknown process (PID $pid); leaving it untouched."
      found=1
      continue
    fi
    found=1
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
  done

  rm -f "$pidfile"
  if [[ "$found" -eq 1 ]]; then
    echo "$name stopped"
  fi
}

stop_one "Frontend" "$RUN_DIR/frontend.pid" 3001 frontend
stop_one "Backend" "$RUN_DIR/backend.pid" 8000 backend

echo
echo "Remaining PIPSGOX ports:"
ss -ltnp 2>/dev/null | grep -E ':3001|:8000' || echo "none"
