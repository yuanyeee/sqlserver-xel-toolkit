#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found. Install uv first: https://astral.sh/uv" >&2
  exit 2
fi

# Create/update a project venv and install Python deps
uv venv
uv pip install -r py/requirements.txt

echo "Done. Activate with: source .venv/bin/activate"
