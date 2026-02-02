#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found. Install uv first: https://astral.sh/uv" >&2
  exit 2
fi

uv venv
uv pip install -r py/requirements.txt

# Run GUI
uv run -m py.ui.app
