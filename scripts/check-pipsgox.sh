#!/usr/bin/env bash
set +e

echo
echo "PIPSGOX RUNTIME STATUS"
echo "======================"
echo "Time: $(date)"
echo

backend_ok=0
frontend_ok=0
fyers_ok=0

echo "[1] Backend"
HEALTH="$(curl -fsS --max-time 3 http://127.0.0.1:8000/health 2>/dev/null)"
if [[ $? -eq 0 ]]; then
  backend_ok=1
  echo "STATUS: PASS — Backend :8000"
  echo "$HEALTH"
else
  echo "STATUS: FAIL — Backend :8000 is not reachable"
fi

echo
echo "[2] FYERS API"
FYERS_STATUS="$(curl -fsS --max-time 3 http://127.0.0.1:8000/api/fyers/status 2>/dev/null)"
if [[ $? -eq 0 ]]; then
  if grep -q '"connected":true' <<<"$FYERS_STATUS"; then
    fyers_ok=1
    echo "STATUS: PASS — FYERS connected"
  elif grep -q '"configured":true' <<<"$FYERS_STATUS"; then
    echo "STATUS: WARN — FYERS configured but login/session is not connected"
  else
    echo "STATUS: WARN — FYERS credentials are not configured"
  fi
  echo "$FYERS_STATUS"
else
  echo "STATUS: FAIL — FYERS status endpoint unavailable"
fi

echo
echo "[3] Frontend"
if curl -fsS --max-time 3 http://127.0.0.1:3001/ >/dev/null 2>&1; then
  frontend_ok=1
  echo "STATUS: PASS — Frontend :3001"
else
  echo "STATUS: FAIL — Frontend :3001 is not reachable"
fi

echo
echo "[4] Overall"
if [[ "$backend_ok" -eq 1 && "$frontend_ok" -eq 1 ]]; then
  echo "STATUS: PASS — PIPSGOX core services are running"
else
  echo "STATUS: FAIL — One or more core services are down"
fi

echo
echo "[5] Ports"
ss -ltnp 2>/dev/null | grep -E ':3001|:8000' || echo "No PIPSGOX ports listening"
