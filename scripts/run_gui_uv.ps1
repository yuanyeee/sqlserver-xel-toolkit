Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location (Split-Path $PSScriptRoot)

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
  throw "uv not found. Install from https://astral.sh/uv and restart the terminal."
}

uv venv
uv pip install -r py/requirements.txt

uv run -m py.ui.app
