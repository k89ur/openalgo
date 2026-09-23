#!/usr/bin/env bash
set +e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.pipsgox"

echo
echo "PIPSGOX DOCTOR"
echo "=============="
echo "Time: $(date)"
echo
echo "[1] Git"
git -C "$ROOT" status --short --branch
echo
echo "[2] Runtime"
printf "Python: "; python --version 2>&1
printf "Node:   "; node --version 2>&1
printf "npm:    "; npm --version 2>&1
echo
echo "[3] Scripts"
for f in scripts/run-pipsgox.sh scripts/stop-pipsgox.sh scripts/check-pipsgox.sh scripts/pipsgox-doctor.sh; do if [[ -x "$ROOT/$f" ]]; then echo "✓ executable  $f"; else echo "⚠ not executable  $f"; fi; done
echo
echo "[4] Ports"
ss -ltnp 2>/dev/null | grep -E ':3001|:8000|:5173' || echo "No PIPSGOX ports listening"
echo
echo "[4b] Port ownership"
for port in 3001 8000; do
  echo "--- :$port ---"
  ss -ltnp 2>/dev/null | grep -E ":$port([[:space:]]|$)" || echo "free"
done
echo
echo "[5] PIPSGOX health"
if curl -fsS --max-time 3 http://127.0.0.1:8000/health; then echo; else echo "✗ backend health unavailable"; fi
echo
echo "[6] FYERS status"
if curl -fsS --max-time 3 http://127.0.0.1:8000/api/fyers/status; then echo; else echo "✗ FYERS status unavailable"; fi
echo
echo "[7] FYERS configuration presence"
if [[ -f "$ROOT/backend/.env" ]]; then echo "✓ backend/.env exists"; for key in FYERS_CLIENT_ID FYERS_SECRET_KEY FYERS_REDIRECT_URI FYERS_ACCESS_TOKEN; do if grep -Eq "^${key}=" "$ROOT/backend/.env"; then value="$(grep -E "^${key}=" "$ROOT/backend/.env" | tail -1 | cut -d= -f2-)"; [[ -n "$value" ]] && echo "✓ $key is set" || echo "⚠ $key is empty"; else echo "⚠ $key is missing"; fi; done; else echo "✗ backend/.env missing"; fi
echo
echo "[8] Process files"
for f in "$RUN_DIR/backend.pid" "$RUN_DIR/frontend.pid"; do if [[ -f "$f" ]]; then pid="$(cat "$f" 2>/dev/null)"; if kill -0 "$pid" 2>/dev/null; then echo "✓ $(basename "$f"): PID $pid running"; else echo "⚠ $(basename "$f"): stale PID $pid"; fi; else echo "— $(basename "$f"): absent"; fi; done
echo
echo "[9] Logs"
for f in "$RUN_DIR/backend.log" "$RUN_DIR/frontend.log"; do
  echo "--- $f ---"
  [[ -f "$f" ]] && tail -n 25 "$f" || echo "log missing"
done
echo
echo "[9b] Recent errors"
for f in "$RUN_DIR/backend.log" "$RUN_DIR/frontend.log"; do
  echo "--- $f errors ---"
  if [[ -f "$f" ]]; then
    grep -Ei 'error|exception|traceback|failed|fatal|cannot|refused' "$f" | tail -n 15 || echo "none found"
  else
    echo "log missing"
  fi
done
echo
echo "[10] Untracked/generated files"
git -C "$ROOT" status --short | grep '^??' || echo "No untracked files"
echo
echo "Doctor complete."
