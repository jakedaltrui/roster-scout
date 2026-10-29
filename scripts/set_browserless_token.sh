#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TOKEN_FILE=${BROWSERLESS_TOKEN_FILE:-"$PROJECT_DIR/.browserless-token"}

printf "Paste your Browserless API token (input is hidden): " >&2
trap 'stty echo 2>/dev/null || true; printf "\n" >&2' EXIT INT TERM
stty -echo
IFS= read -r TOKEN
stty echo
trap - EXIT INT TERM
printf "\n" >&2

if [ -z "$TOKEN" ]; then
  echo "No token was entered; nothing was saved." >&2
  exit 1
fi

umask 077
printf '%s\n' "$TOKEN" > "$TOKEN_FILE"
echo "Browserless token saved locally. It is ignored by Git."
