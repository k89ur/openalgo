#!/usr/bin/env bash
set -euo pipefail

cd /workspaces/openalgo-runtime

python -m pip install --upgrade pip
pip install uv

if [ ! -f .env ]; then
  cp .sample.env .env
fi

echo "OpenAlgo source ready at /workspaces/openalgo-runtime"
echo "Start with:"
echo "  cd /workspaces/openalgo-runtime"
echo "  uv run app.py"
