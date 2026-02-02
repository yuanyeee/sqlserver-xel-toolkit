#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "Updating integratedtool submodule..."

git submodule update --init --recursive integratedtool

# Move submodule to latest main (local-only unless you commit parent repo)
(
  cd integratedtool
  git fetch origin
  git checkout main >/dev/null 2>&1 || git checkout -b main origin/main
  git pull --ff-only origin main
)

echo "Done. integratedtool is now at:"
(cd integratedtool && git log -1 --oneline)
