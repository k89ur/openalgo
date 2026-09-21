#!/usr/bin/env bash
set -euo pipefail

cd /workspaces/openalgo-runtime

python -m pip install --upgrade pip
pip install uv

if [ ! -f .env ]; then
  cp .sample.env .env
fi

# OpenAlgo's upstream Dhan adapter already supports a direct access token,
# but the generic env validation and credential-management API still require
# BROKER_API_KEY in client_id:::api_key form. Patch those two Dhan-only checks
# so Codespace users can configure:
#   BROKER_API_KEY    = <Dhan Client ID>
#   BROKER_API_SECRET = <Dhan Access Token>
# without changing the behaviour of other brokers.
python - <<'PY'
from pathlib import Path

# 1) Startup validation: allow a plain Dhan Client ID in BROKER_API_KEY.
env_check = Path("/workspaces/openalgo-runtime/utils/env_check.py")
text = env_check.read_text(encoding="utf-8")
old = '''    # Validate dhan API key format
    elif broker_name == "dhan":
        if ":::" not in broker_api_key or broker_api_key.count(":::") != 1:
            print("\\nError: Invalid Dhan API key format detected!")
            print("The BROKER_API_KEY for Dhan must be in the format:")
            print("  BROKER_API_KEY = 'client_id:::api_key'")
            print("\\nExample:")
            print("  BROKER_API_KEY = '1234567890:::your_dhan_apikey'")
            print("  BROKER_API_SECRET = 'your_dhan_apisecret'")
            print("\\nFor detailed instructions, please refer to:")
            print("  https://docs.openalgo.in/connect-brokers/brokers/dhan")
            sys.exit(1)
'''
new = '''    # Validate dhan configuration.
    # Dhan also supports direct access-token configuration in Codespace/custom
    # deployments: BROKER_API_KEY = client ID and BROKER_API_SECRET = access token.
    elif broker_name == "dhan":
        if not broker_api_key:
            print("\\nError: Dhan Client ID is required in BROKER_API_KEY.")
            sys.exit(1)
'''
if old not in text:
    raise SystemExit("env_check.py Dhan validation block not found")
env_check.write_text(text.replace(old, new, 1), encoding="utf-8")

# 2) Broker credentials API: permit a plain Dhan Client ID when saving config.
credentials = Path("/workspaces/openalgo-runtime/blueprints/broker_credentials.py")
text = credentials.read_text(encoding="utf-8")
old = '''            elif broker_name == "dhan" and broker_api_key:
                if ":::" not in broker_api_key or broker_api_key.count(":::") != 1:
                    return jsonify(
                        {
                            "status": "error",
                            "message": "Dhan API key must be in format: 'client_id:::api_key'",
                        }
                    ), 400
'''
new = '''            elif broker_name == "dhan" and broker_api_key:
                # Dhan also supports direct access-token configuration:
                # broker_api_key = client ID; broker_api_secret = access token.
                pass
'''
if old not in text:
    raise SystemExit("broker_credentials.py Dhan validation block not found")
credentials.write_text(text.replace(old, new, 1), encoding="utf-8")

print("Dhan access-token compatibility patch applied.")
PY

echo "OpenAlgo source ready at /workspaces/openalgo-runtime"
echo "Dhan direct-token config:"
echo "  BROKER_API_KEY    = <Dhan Client ID>"
echo "  BROKER_API_SECRET = <Dhan Access Token>"
echo "Start with:"
echo "  cd /workspaces/openalgo-runtime"
echo "  uv run app.py"
