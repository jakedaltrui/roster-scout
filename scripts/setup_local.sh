#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VENV_DIR=${ROSTER_SCOUT_VENV:-"$PROJECT_DIR/.venv"}
PYTHON_BIN=${PYTHON_BIN:-python3}

if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r "$PROJECT_DIR/requirements.txt"

echo "Local environment is ready."
echo "Run scripts/set_browserless_token.sh once, then start with scripts/run_local.sh."
