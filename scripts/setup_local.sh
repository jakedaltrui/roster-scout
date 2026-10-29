#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VENV_DIR=${ROSTER_SCOUT_VENV:-"$PROJECT_DIR/.venv"}
PYTHON_BIN=${PYTHON_BIN:-python3}

if [ ! -d "/Applications/Google Chrome.app" ]; then
  echo "Google Chrome must be installed in /Applications for the NCAA fallback." >&2
  exit 1
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r "$PROJECT_DIR/requirements.txt"

echo "Local environment is ready. Start Roster Scout with scripts/run_local.sh"
