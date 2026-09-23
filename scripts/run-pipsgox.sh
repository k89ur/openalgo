#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.pipsgox"
mkdir -p "$RUN_DIR"

CODESPACE_DOMAIN="${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
if [[ -n "${CODESPACE_NAME:-}" ]]; then
  WEB_URL="https://${CODESPACE_NAME}-3001.${CODESPACE_DOMAIN}"
  API_URL="https://${CODESPACE_NAME}-8000.${CODESPACE_DOMAIN}"
else
  WEB_URL="http://127.0.0.1:3001"
  API_URL="http://127.0.0.1:8000"
fi
FYERS_CALLBACK="${API_URL}/auth/fyers/callback"

log_tail() {
  local file="$1"
  echo
  echo "----- LAST LOG OUTPUT: $file -----"
  if [[ -f "$file" ]]; then
    tail -n 40 "$file"
  else
    echo "Log file not found."
  fi
  echo "-----------------------------------"
}

port_owner() {
  local port="$1"
  ss -ltnp 2>/dev/null | grep -E ":$port([[:space:]]|$)" || true
}

check_port_free() {
  local name="$1" port="$2"
  if port_owner "$port" | grep -q "LISTEN"; then
    echo "✗ $name port :$port is already occupied."
    port_owner "$port"
    return 1
  fi
  return 0
}

start_backend() {
  local pidfile="$RUN_DIR/backend.pid"
  local logfile="$RUN_DIR/backend.log"

  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile" 2>/dev/null)" 2>/dev/null; then
    echo "✓ Backend already running (PID $(cat "$pidfile"))"
    return 0
  fi

  rm -f "$pidfile"
  : > "$logfile"
  echo "Starting Backend..."

  (
    cd "$ROOT/backend" || exit 1
    exec env PIPSGOX_WEB_URL="$WEB_URL" FYERS_REDIRECT_URI="$FYERS_CALLBACK"       python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
  ) >"$logfile" 2>&1 &
  local pid=$!
  echo "$pid" > "$pidfile"

  for _ in {1..30}; do
    if curl -fsS --max-time 2 http://127.0.0.1:8000/health >/dev/null 2>&1; then
      echo "✓ Backend :8000 (PID $pid)"
      return 0
    fi

    if ! kill -0 "$pid" 2>/dev/null; then
      echo "✗ Backend exited during startup (PID $pid)."
      log_tail "$logfile"
      rm -f "$pidfile"
      return 1
    fi
    sleep 1
  done

  echo "✗ Backend did not become ready within 30 seconds."
  log_tail "$logfile"
  return 1
}

start_frontend() {
  local pidfile="$RUN_DIR/frontend.pid"
  local logfile="$RUN_DIR/frontend.log"

  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile" 2>/dev/null)" 2>/dev/null; then
    echo "✓ Frontend already running (PID $(cat "$pidfile"))"
    return 0
  fi

  rm -f "$pidfile"
  : > "$logfile"
  echo "Starting Frontend..."

  (
    cd "$ROOT/frontend" || exit 1
    exec npm run dev -- --host 0.0.0.0 --port 3001
  ) >"$logfile" 2>&1 &
  local pid=$!
  echo "$pid" > "$pidfile"

  for _ in {1..30}; do
    if curl -fsS --max-time 2 http://127.0.0.1:3001/ >/dev/null 2>&1; then
      echo "✓ Frontend :3001 (PID $pid)"
      return 0
    fi

    if ! kill -0 "$pid" 2>/dev/null; then
      echo "✗ Frontend exited during startup (PID $pid)."
      log_tail "$logfile"
      rm -f "$pidfile"
      return 1
    fi
    sleep 1
  done

  echo "✗ Frontend did not become ready within 30 seconds."
  log_tail "$logfile"
  return 1
}

echo
echo "PIPSGOX DEV SERVER"
echo "=================="
echo "Web: $WEB_URL"
echo "API: $API_URL"
echo "FYERS callback: $FYERS_CALLBACK"
echo

if ! check_port_free "Backend" 8000; then
  echo "Use ./scripts/stop-pipsgox.sh first, or inspect with ./scripts/pipsgox-doctor.sh."
  exit 1
fi

if ! check_port_free "Frontend" 3001; then
  echo "Use ./scripts/stop-pipsgox.sh first, or inspect with ./scripts/pipsgox-doctor.sh."
  exit 1
fi

if ! start_backend; then
  echo
  echo "PIPSGOX START FAILED: backend"
  echo "Run: ./scripts/pipsgox-doctor.sh"
  exit 1
fi

if ! start_frontend; then
  echo
  echo "PIPSGOX START FAILED: frontend"
  echo "Run: ./scripts/pipsgox-doctor.sh"
  exit 1
fi

echo
echo "Health:"
curl -fsS http://127.0.0.1:8000/health || true
echo
FYERS_STATUS="$(curl -fsS --max-time 3 http://127.0.0.1:8000/api/fyers/status || true)"
if grep -q '"configured":true' <<<"$FYERS_STATUS"; then
  if grep -q '"connected":true' <<<"$FYERS_STATUS"; then
    echo "FYERS: connected"
  else
    echo "FYERS: login required — the web app will open FYERS login automatically."
  fi
else
  echo "FYERS: credentials not configured in backend/.env"
fi
echo
echo "PIPSGOX is running."
