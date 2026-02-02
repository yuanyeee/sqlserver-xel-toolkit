param(
  [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
  [string[]]$Inputs,

  [Alias('o')]
  [string]$OutDir = "",

  [double]$SlowThresholdSec = 3.0,

  [string]$Start = "",
  [string]$End = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Windows runner equivalent to run.sh

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($OutDir)) {
  $OutDir = Join-Path $RepoRoot 'reports'
}

function Find-DotNet {
  $cmd = Get-Command dotnet -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $candidates = @(
    Join-Path $env:ProgramFiles 'dotnet\dotnet.exe',
    Join-Path ${env:ProgramFiles(x86)} 'dotnet\dotnet.exe'
  )
  foreach ($p in $candidates) {
    if ($p -and (Test-Path $p)) { return $p }
  }
  return $null
}

function Find-Python {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { return @($cmd.Source) }
  $py = Get-Command py -ErrorAction SilentlyContinue
  if ($py) { return @($py.Source, '-3') }
  return $null
}

$DotNetExe = Find-DotNet
if (-not $DotNetExe) {
  throw "dotnet not found. Install .NET SDK (x64) and restart PowerShell: https://dotnet.microsoft.com/download"
}

$PyCmd = Find-Python
if (-not $PyCmd) {
  throw "python not found. Install Python 3 and ensure it's on PATH (or install the Python launcher 'py')."
}

$PyExe = $PyCmd[0]
$PyPrefix = @()
if ($PyCmd.Length -gt 1) { $PyPrefix = $PyCmd[1..($PyCmd.Length-1)] }


function Usage {
  @'
Usage:
  .\run.ps1 <xel path|glob> [more xel ...] [-o OUT_DIR] [-SlowThresholdSec SEC] [-Start "YYYY-mm-dd HH:MM"] [-End "YYYY-mm-dd HH:MM"]

Notes:
  - PowerShell scripts do not accept GNU-style options like --slow-threshold.
'@
}

if (-not $Inputs -or $Inputs.Count -eq 0) {
  Usage
  exit 2
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $OutDir 'tmp') | Out-Null

# keep variable names used by the existing implementation
$inputs = $Inputs
$SlowThreshold = [string]$SlowThresholdSec
$StartJst = $Start
$EndJst = $End

function SafeBase([string]$s) {
  $safe = [regex]::Replace($s, '[^A-Za-z0-9._-]+', '_')
  $safe = $safe.Trim('_')
  if ([string]::IsNullOrWhiteSpace($safe)) { return 'input' }
  return $safe
}

function Hash8([string]$s) {
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($s)
  $sha = [System.Security.Cryptography.SHA256]::Create()
  $hash = $sha.ComputeHash($bytes)
  return ([BitConverter]::ToString($hash).Replace('-', '').Substring(0,8).ToLower())
}

function RunOne([string]$path) {
  $ext = [IO.Path]::GetExtension($path).TrimStart('.')
  $base = [IO.Path]::GetFileNameWithoutExtension($path)

  $safeBase = SafeBase $base
  $fileOut = Join-Path $OutDir $safeBase
  New-Item -ItemType Directory -Force -Path $fileOut | Out-Null

  $ts = (Get-Date).ToString('yyyyMMdd_HHmmss')
  $prefix = "${base}_${ts}"

  Write-Host "==> Processing: $path"

  # CSV/Excel: convert to MD
  if (@('csv','CSV','xlsx','xls','XLSX','XLS') -contains $ext) {
    $runTag = (Get-Date).ToString('yyyyMMdd_HHmmss')
    $ws = $env:XEL_TOOLKIT_WORKSPACE
    if (-not [string]::IsNullOrWhiteSpace($ws)) {
      $inputMd = Join-Path (Join-Path $ws 'inputMD') $safeBase
      New-Item -ItemType Directory -Force -Path $inputMd | Out-Null
      $cmd = @('python', (Join-Path $RepoRoot 'py/csv_excel_to_md.py'), '--in', $path, '--out', $inputMd, '--run-tag', $runTag)
      if ($env:XEL_TOOLKIT_RANGES_JSON) { $cmd += @('--ranges-json', $env:XEL_TOOLKIT_RANGES_JSON) }
      & $PyExe @($PyPrefix + @($cmd[1..($cmd.Count-1)])) | Out-Null
    } else {
      $md = Join-Path $fileOut 'md'
      New-Item -ItemType Directory -Force -Path $md | Out-Null
      $cmd = @('python', (Join-Path $RepoRoot 'py/csv_excel_to_md.py'), '--in', $path, '--out', $md, '--run-tag', $runTag)
      if ($env:XEL_TOOLKIT_RANGES_JSON) { $cmd += @('--ranges-json', $env:XEL_TOOLKIT_RANGES_JSON) }
      & $PyExe @($PyPrefix + @($cmd[1..($cmd.Count-1)])) | Out-Null
    }
    Write-Host "   Done (csv/excel->md): $base"
    return
  }

  # XEL summary always
  & $DotNetExe run --project (Join-Path $RepoRoot 'src/XelDump') -- $path -o $fileOut --max 2000 | Out-Null

  $tmpDir = Join-Path $OutDir 'tmp'

  if ($base -like '*deadlock*') {
    $key = Hash8 $prefix
    $jsonl = Join-Path $tmpDir ("${prefix}_deadlock.jsonl")
    & $DotNetExe run --project (Join-Path $RepoRoot 'src/XelDump') -- $path --export-jsonl $jsonl --filter xml_deadlock_report | Out-Null

    $ws = $env:XEL_TOOLKIT_WORKSPACE
    if (-not [string]::IsNullOrWhiteSpace($ws)) {
      $out = Join-Path (Join-Path $ws 'inputMD') $safeBase
      $cmd = @('python', (Join-Path $RepoRoot 'py/xel_to_md.py'), '--jsonl', $jsonl, '--out', $out, '--key', $key, '--event', 'xml_deadlock_report', '--source', $path)
      if ($StartJst) { $cmd += @('--start', $StartJst) }
      if ($EndJst) { $cmd += @('--end', $EndJst) }
      if ($env:XEL_TOOLKIT_RANGES_JSON) { $cmd += @('--ranges-json', $env:XEL_TOOLKIT_RANGES_JSON) }
      & $PyExe @($PyPrefix + @($cmd[1..($cmd.Count-1)])) | Out-Null
    }

    $cmd2 = @('python', (Join-Path $RepoRoot 'py/generate_reports.py'), '--deadlock-jsonl', $jsonl, '--source-xel', $path, '--prefix', $prefix, '--out', $fileOut)
    if ($StartJst) { $cmd2 += @('--start', $StartJst) }
    if ($EndJst) { $cmd2 += @('--end', $EndJst) }
    if ($env:XEL_TOOLKIT_RANGES_JSON) { $cmd2 += @('--ranges-json', $env:XEL_TOOLKIT_RANGES_JSON) }
    & $PyExe @($PyPrefix + @($cmd2[1..($cmd2.Count-1)])) | Out-Null
  }

  if ($base -like '*Slow_Queries*' -or $base -like '*slow*') {
    $key = Hash8 $prefix
    $jsonl = Join-Path $tmpDir ("${prefix}_slow.jsonl")
    & $DotNetExe run --project (Join-Path $RepoRoot 'src/XelDump') -- $path --export-jsonl $jsonl | Out-Null

    $ws = $env:XEL_TOOLKIT_WORKSPACE
    if (-not [string]::IsNullOrWhiteSpace($ws)) {
      $out = Join-Path (Join-Path $ws 'inputMD') $safeBase
      $cmd = @('python', (Join-Path $RepoRoot 'py/xel_to_md.py'), '--jsonl', $jsonl, '--out', $out, '--key', $key, '--event', 'rpc_completed', '--source', $path)
      if ($StartJst) { $cmd += @('--start', $StartJst) }
      if ($EndJst) { $cmd += @('--end', $EndJst) }
      if ($env:XEL_TOOLKIT_RANGES_JSON) { $cmd += @('--ranges-json', $env:XEL_TOOLKIT_RANGES_JSON) }
      & $PyExe @($PyPrefix + @($cmd[1..($cmd.Count-1)])) | Out-Null
      $cmd = @('python', (Join-Path $RepoRoot 'py/xel_to_md.py'), '--jsonl', $jsonl, '--out', $out, '--key', $key, '--event', 'sql_batch_completed', '--source', $path)
      if ($StartJst) { $cmd += @('--start', $StartJst) }
      if ($EndJst) { $cmd += @('--end', $EndJst) }
      if ($env:XEL_TOOLKIT_RANGES_JSON) { $cmd += @('--ranges-json', $env:XEL_TOOLKIT_RANGES_JSON) }
      & $PyExe @($PyPrefix + @($cmd[1..($cmd.Count-1)])) | Out-Null
    }

    $cmd2 = @('python', (Join-Path $RepoRoot 'py/generate_reports.py'), '--slowquery-jsonl', $jsonl, '--slow-threshold', $SlowThreshold, '--source-xel', $path, '--prefix', $prefix, '--out', $fileOut)
    if ($StartJst) { $cmd2 += @('--start', $StartJst) }
    if ($EndJst) { $cmd2 += @('--end', $EndJst) }
    if ($env:XEL_TOOLKIT_RANGES_JSON) { $cmd2 += @('--ranges-json', $env:XEL_TOOLKIT_RANGES_JSON) }
    & $PyExe @($PyPrefix + @($cmd2[1..($cmd2.Count-1)])) | Out-Null
  }

  if ($base -like '*blocking*') {
    $key = Hash8 $prefix
    $jsonl = Join-Path $tmpDir ("${prefix}_blocking.jsonl")
    & $DotNetExe run --project (Join-Path $RepoRoot 'src/XelDump') -- $path --export-jsonl $jsonl --filter blocked_process_report | Out-Null

    $ws = $env:XEL_TOOLKIT_WORKSPACE
    if (-not [string]::IsNullOrWhiteSpace($ws)) {
      $out = Join-Path (Join-Path $ws 'inputMD') $safeBase
      $cmd = @('python', (Join-Path $RepoRoot 'py/xel_to_md.py'), '--jsonl', $jsonl, '--out', $out, '--key', $key, '--event', 'blocked_process_report', '--source', $path)
      if ($StartJst) { $cmd += @('--start', $StartJst) }
      if ($EndJst) { $cmd += @('--end', $EndJst) }
      if ($env:XEL_TOOLKIT_RANGES_JSON) { $cmd += @('--ranges-json', $env:XEL_TOOLKIT_RANGES_JSON) }
      & $PyExe @($PyPrefix + @($cmd[1..($cmd.Count-1)])) | Out-Null
    }

    $cmd2 = @('python', (Join-Path $RepoRoot 'py/generate_reports.py'), '--blocking-jsonl', $jsonl, '--source-xel', $path, '--prefix', $prefix, '--out', $fileOut)
    if ($StartJst) { $cmd2 += @('--start', $StartJst) }
    if ($EndJst) { $cmd2 += @('--end', $EndJst) }
    if ($env:XEL_TOOLKIT_RANGES_JSON) { $cmd2 += @('--ranges-json', $env:XEL_TOOLKIT_RANGES_JSON) }
    & $PyExe @($PyPrefix + @($cmd2[1..($cmd2.Count-1)])) | Out-Null
  }

  Write-Host "   Done: $prefix"
}

foreach ($inp in $inputs) {
  # Let PowerShell expand wildcards if provided; fall back to literal.
  $expanded = @(Get-ChildItem -LiteralPath $inp -ErrorAction SilentlyContinue)
  if ($expanded.Count -gt 0) {
    foreach ($e in $expanded) { RunOne $e.FullName }
  } else {
    # Try glob expansion
    $glob = @(Get-ChildItem -Path $inp -ErrorAction SilentlyContinue)
    if ($glob.Count -gt 0) {
      foreach ($e in $glob) { RunOne $e.FullName }
    } else {
      RunOne $inp
    }
  }
}

Write-Host "All done. Reports in: $OutDir"
