#!/usr/bin/env bash
# =============================================================================
#  OpsPilot — Local Development Server Runner
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${SCRIPT_DIR}"

if [[ -f .env ]]; then
  echo "Loading environment from .env..."
  set -a; source .env; set +a
fi

echo "Starting OpsPilot Web Command Center in development mode..."
PYTHONPATH=src .venv/bin/python -m uvicorn opspilot.web.app:create_web_app \
  --factory \
  --host "${OPSPILOT_WEB_HOST:-0.0.0.0}" \
  --port "${OPSPILOT_WEB_PORT:-8080}" \
  --reload
