#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"

cd "$REPO_ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

. .venv/bin/activate

# Ensure modern packaging tooling so Qt wheels resolve correctly on macOS
python -m pip install -q --upgrade pip setuptools wheel

# Prefer binary wheels (avoid source builds)
python -m pip install -q --only-binary=:all: -r py/requirements.txt

python3 -m py.ui.app
