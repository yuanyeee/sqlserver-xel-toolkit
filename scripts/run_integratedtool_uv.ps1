Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location (Join-Path (Split-Path $PSScriptRoot) 'integratedtool')

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
  throw "uv not found. Install from https://astral.sh/uv and restart the terminal."
}

uv run unified_report_viewer.py
