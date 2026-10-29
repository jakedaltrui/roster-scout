#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VENV_DIR=${ROSTER_SCOUT_VENV:-"$PROJECT_DIR/.venv"}

if [ ! -x "$VENV_DIR/bin/gunicorn" ]; then
  echo "Local environment is not installed. Run scripts/setup_local.sh first." >&2
  exit 1
fi

TOKEN_FILE=${BROWSERLESS_TOKEN_FILE:-"$PROJECT_DIR/.browserless-token"}
if [ -z "${BROWSERLESS_TOKEN:-}" ] && [ -r "$TOKEN_FILE" ]; then
  IFS= read -r BROWSERLESS_TOKEN < "$TOKEN_FILE" || true
  export BROWSERLESS_TOKEN
fi
if [ -z "${BROWSERLESS_TOKEN:-}" ]; then
  echo "Browserless is not configured locally." >&2
  echo "Run scripts/set_browserless_token.sh, then start Roster Scout again." >&2
  exit 1
fi

export PORT=${PORT:-5050}
export ROSTER_SCOUT_CACHE_DIR=${ROSTER_SCOUT_CACHE_DIR:-"$PROJECT_DIR/.cache"}
export NCAA_BROWSER_PROVIDER=browserless
export NCAA_SITE_UNBLOCKER=0

cd "$PROJECT_DIR"
exec "$VENV_DIR/bin/gunicorn" \
  --bind "127.0.0.1:$PORT" \
  --workers 1 \
  --threads 4 \
  --timeout 180 \
  app:app
