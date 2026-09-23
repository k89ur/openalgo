#!/usr/bin/env bash
set -euo pipefail

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

start() {
  local name="$1" pidfile="$2" logfile="$3" cmd="$4" port="$5" url="$6"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "$name already running (PID $(cat "$pidfile"))"
  else
    rm -f "$pidfile"
    echo "Starting $name..."
    env PIPSGOX_WEB_URL="$WEB_URL" FYERS_REDIRECT_URI="$FYERS_CALLBACK" bash -lc "cd '$ROOT' && $cmd" >"$logfile" 2>&1 &
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
echo
echo "Web: $WEB_URL"
echo "API: $API_URL"
echo "FYERS callback: $FYERS_CALLBACK"
FYERS_STATUS="$(curl -fsS --max-time 3 http://127.0.0.1:8000/api/fyers/status || true)"
if grep -q '"configured":true' <<<"$FYERS_STATUS"; then
  if grep -q '"connected":true' <<<"$FYERS_STATUS"; then echo "FYERS: connected"; else echo "FYERS: login required — PIPSGOX will open the FYERS login automatically."; fi
else echo "FYERS: credentials not configured in backend/.env"; fi
