Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location (Split-Path $PSScriptRoot)

Write-Host "Updating integratedtool submodule..."

git submodule update --init --recursive integratedtool

Push-Location integratedtool
try {
  git fetch origin
  # Ensure main exists
  $hasMain = git show-ref --verify --quiet refs/heads/main; $LASTEXITCODE -eq 0
  if (-not $hasMain) {
    git checkout -b main origin/main
  } else {
    git checkout main
  }
  git pull --ff-only origin main
} finally {
  Pop-Location
}

Write-Host "Done. integratedtool is now at:"
Push-Location integratedtool
try { git log -1 --oneline } finally { Pop-Location }
