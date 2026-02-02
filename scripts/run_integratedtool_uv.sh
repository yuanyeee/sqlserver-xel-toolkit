#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../integratedtool"

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found. Install uv first: https://astral.sh/uv" >&2
  exit 2
fi

uv run unified_report_viewer.py
