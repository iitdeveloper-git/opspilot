#!/usr/bin/env bash
# =============================================================================
#  OpsPilot — Local & Remote Healthcheck Probe
# =============================================================================
set -euo pipefail

PORT="${OPSPILOT_WEB_PORT:-8080}"
HOST="${OPSPILOT_WEB_HOST:-127.0.0.1}"
URL="http://${HOST}:${PORT}/"

echo "Checking OpsPilot Command Center at ${URL}..."

if curl -s -f -o /dev/null "${URL}"; then
  echo "✅ OpsPilot is healthy and responding (HTTP 200)."
  exit 0
else
  echo "❌ OpsPilot healthcheck failed (cannot connect or non-200 response)."
  exit 1
fi
