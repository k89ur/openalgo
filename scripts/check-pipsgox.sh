#!/usr/bin/env bash
set -euo pipefail
echo "PIPSGOX runtime check"
echo "====================="
if curl -fsS --max-time 3 http://127.0.0.1:8000/health >/tmp/pipsgox-health.json 2>/dev/null; then
  echo "✓ Backend :8000"; cat /tmp/pipsgox-health.json; echo
else echo "✗ Backend :8000"; fi
if curl -fsS --max-time 3 http://127.0.0.1:3001/ >/dev/null 2>&1; then echo "✓ Frontend :3001"; else echo "✗ Frontend :3001"; fi
