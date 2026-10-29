#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VENV_DIR=${ROSTER_SCOUT_VENV:-"$PROJECT_DIR/.venv"}

if [ ! -x "$VENV_DIR/bin/gunicorn" ]; then
  echo "Local environment is not installed. Run scripts/setup_local.sh first." >&2
  exit 1
fi

if [ ! -d "/Applications/Google Chrome.app" ]; then
  echo "Google Chrome must be installed in /Applications for the NCAA fallback." >&2
  exit 1
fi

export PORT=${PORT:-5050}
export ROSTER_SCOUT_CACHE_DIR=${ROSTER_SCOUT_CACHE_DIR:-"$PROJECT_DIR/.cache"}
export NCAA_BROWSER_CHANNEL=${NCAA_BROWSER_CHANNEL:-chrome}
export NCAA_BROWSER_HEADLESS=0
export NCAA_BROWSER_FALLBACK=1

cd "$PROJECT_DIR"
exec "$VENV_DIR/bin/gunicorn" \
  --bind "127.0.0.1:$PORT" \
  --workers 1 \
  --threads 4 \
  --timeout 180 \
  app:app
