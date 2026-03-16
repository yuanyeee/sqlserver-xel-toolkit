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

# Normalize output encoding for piping into Python GUI (avoid mojibake on Japanese paths)
try {
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
  # best-effort
}

# [scriptblock]::Create() 経由（run.bat）で実行された場合、
# $MyInvocation.MyCommand.Path は null になる。
# run.bat が XEL_TOOLKIT_RUNDIR 環境変数にスクリプトディレクトリを設定するので
# それをフォールバックとして使用する。
$_cmdPath = $MyInvocation.MyCommand.Path
if (-not [string]::IsNullOrWhiteSpace($_cmdPath)) {
  $RepoRoot = Split-Path -Parent $_cmdPath
} elseif (-not [string]::IsNullOrWhiteSpace($env:XEL_TOOLKIT_RUNDIR)) {
  $RepoRoot = $env:XEL_TOOLKIT_RUNDIR.TrimEnd('\').TrimEnd('/')
} else {
  $RepoRoot = (Get-Location).Path
}

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
  throw "dotnet が見つかりません。.NET 8 SDK (LTS, x64) をインストールして PowerShell を再起動してください: https://dotnet.microsoft.com/download/dotnet/8.0"
}

# Verify minimum .NET version (8+)
$_dotnetVer = (& $DotNetExe --version 2>&1) -as [string]
if ($_dotnetVer -match '^(\d+)\.') {
  if ([int]$Matches[1] -lt 8) {
    throw "dotnet $_dotnetVer が見つかりましたが、.NET 8 以上が必要です。`nInstall .NET 8 SDK (LTS) from: https://dotnet.microsoft.com/download/dotnet/8.0"
  }
}

$PyCmd = Find-Python
if (-not $PyCmd) {
  throw "python not found. Install Python 3 and ensure it's on PATH (or install the Python launcher 'py')."
}

# Normalize python command (could be a string or string[] depending on environment)
$PyExe = $null
$PyPrefix = @()
if ($PyCmd -is [string]) {
  $PyExe = $PyCmd
} else {
  $PyExe = $PyCmd[0]
  if ($PyCmd.Length -gt 1) { $PyPrefix = $PyCmd[1..($PyCmd.Length-1)] }
}

function Usage {
  @'
Usage:
  .\run.ps1 <xel path|glob> [more xel ...] [-o OUT_DIR] [-SlowThresholdSec SEC] [-Start "YYYY-mm-dd HH:MM"] [-End "YYYY-mm-dd HH:MM"]
'@
}

if (-not $Inputs -or $Inputs.Count -eq 0) {
  Usage
  exit 2
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$tmpDir = Join-Path $OutDir 'tmp'
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

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

function Test-JsonlNonEmpty([string]$p) {
  if (-not (Test-Path $p)) { return $false }
  try {
    $info = Get-Item $p
    if ($info.Length -le 0) { return $false }
    $line = Get-Content -LiteralPath $p -TotalCount 1 -ErrorAction SilentlyContinue
    return -not [string]::IsNullOrWhiteSpace($line)
  } catch {
    return $false
  }
}

function RunOne([string]$path) {
  $ext = [IO.Path]::GetExtension($path).TrimStart('.')
  $base = [IO.Path]::GetFileNameWithoutExtension($path)

  $safeBase = SafeBase $base
  $fileHash = Hash8 (Resolve-Path -LiteralPath $path | Select-Object -ExpandProperty Path)
  $fileOut = Join-Path $OutDir ("${safeBase}_${fileHash}")
  New-Item -ItemType Directory -Force -Path $fileOut | Out-Null

  $ts = (Get-Date).ToString('yyyyMMdd_HHmmss')
  $prefix = "${base}_${fileHash}_${ts}"

  Write-Host "==> Processing: $path"

  # CSV/Excel: convert to MD
  if (@('csv','CSV','xlsx','xls','XLSX','XLS') -contains $ext) {
    $runTag = (Get-Date).ToString('yyyyMMdd_HHmmss')
    $ws = $env:XEL_TOOLKIT_WORKSPACE
    if (-not [string]::IsNullOrWhiteSpace($ws)) {
      $inputmdMode = $env:XEL_TOOLKIT_INPUTMD_MODE
      if ([string]::IsNullOrWhiteSpace($inputmdMode)) { $inputmdMode = 'overwrite' }
      $fileHash = Hash8 (Resolve-Path -LiteralPath $path | Select-Object -ExpandProperty Path)
      $inputMd = Join-Path (Join-Path $ws 'inputMD') ("${safeBase}_${fileHash}")
      if ($inputmdMode -eq 'overwrite' -and (Test-Path $inputMd)) {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $inputMd
      }
      New-Item -ItemType Directory -Force -Path $inputMd | Out-Null
      # NOTE: inputMD is unfiltered; ignore ranges-json for csv/excel export
      $oldRanges = $env:XEL_TOOLKIT_RANGES_JSON
      $env:XEL_TOOLKIT_RANGES_JSON = $null
      $cmd = @('python', (Join-Path $RepoRoot 'py/csv_excel_to_md.py'), '--in', $path, '--out', $inputMd, '--run-tag', $runTag)
      & $PyExe @($PyPrefix + @($cmd[1..($cmd.Count-1)])) | Out-Null
      $env:XEL_TOOLKIT_RANGES_JSON = $oldRanges
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

  $ws = $env:XEL_TOOLKIT_WORKSPACE

  # Deadlock
  $deadlockJsonl = Join-Path $tmpDir ("${prefix}_deadlock.jsonl")
  & $DotNetExe run --project (Join-Path $RepoRoot 'src/XelDump') -- $path --export-jsonl $deadlockJsonl --filter xml_deadlock_report | Out-Null
  if (Test-JsonlNonEmpty $deadlockJsonl) {
    $key = Hash8 $prefix
    if (-not [string]::IsNullOrWhiteSpace($ws)) {
      $inputMdRoot = Join-Path $ws 'inputMD'
      # NOTE: inputMD is unfiltered (always export all events to markdown)
      $oldRanges = $env:XEL_TOOLKIT_RANGES_JSON
      $env:XEL_TOOLKIT_RANGES_JSON = $null
      $cmd = @('python', (Join-Path $RepoRoot 'py/xel_to_md.py'), '--jsonl', $deadlockJsonl, '--out', $inputMdRoot, '--key', $key, '--event', 'xml_deadlock_report', '--event-type', 'DeadLock', '--source', $path)
      & $PyExe @($PyPrefix + @($cmd[1..($cmd.Count-1)]))
      $env:XEL_TOOLKIT_RANGES_JSON = $oldRanges
    }
    $cmd2 = @('python', (Join-Path $RepoRoot 'py/generate_reports.py'), '--deadlock-jsonl', $deadlockJsonl, '--source-xel', $path, '--prefix', $prefix, '--out', $fileOut)
    if ($StartJst) { $cmd2 += @('--start', $StartJst) }
    if ($EndJst) { $cmd2 += @('--end', $EndJst) }
    if ($env:XEL_TOOLKIT_RANGES_JSON) { $cmd2 += @('--ranges-json', $env:XEL_TOOLKIT_RANGES_JSON) }
    & $PyExe @($PyPrefix + @($cmd2[1..($cmd2.Count-1)])) | Out-Null
  }

  # Blocking
  $blockingJsonl = Join-Path $tmpDir ("${prefix}_blocking.jsonl")
  & $DotNetExe run --project (Join-Path $RepoRoot 'src/XelDump') -- $path --export-jsonl $blockingJsonl --filter blocked_process_report | Out-Null
  if (Test-JsonlNonEmpty $blockingJsonl) {
    $key = Hash8 $prefix
    if (-not [string]::IsNullOrWhiteSpace($ws)) {
      $inputMdRoot = Join-Path $ws 'inputMD'
      # NOTE: inputMD is unfiltered (always export all events to markdown)
      $oldRanges = $env:XEL_TOOLKIT_RANGES_JSON
      $env:XEL_TOOLKIT_RANGES_JSON = $null
      $cmd = @('python', (Join-Path $RepoRoot 'py/xel_to_md.py'), '--jsonl', $blockingJsonl, '--out', $inputMdRoot, '--key', $key, '--event', 'blocked_process_report', '--event-type', 'Blocking', '--source', $path)
      & $PyExe @($PyPrefix + @($cmd[1..($cmd.Count-1)]))
      $env:XEL_TOOLKIT_RANGES_JSON = $oldRanges
    }
    $cmd2 = @('python', (Join-Path $RepoRoot 'py/generate_reports.py'), '--blocking-jsonl', $blockingJsonl, '--source-xel', $path, '--prefix', $prefix, '--out', $fileOut)
    if ($StartJst) { $cmd2 += @('--start', $StartJst) }
    if ($EndJst) { $cmd2 += @('--end', $EndJst) }
    if ($env:XEL_TOOLKIT_RANGES_JSON) { $cmd2 += @('--ranges-json', $env:XEL_TOOLKIT_RANGES_JSON) }
    & $PyExe @($PyPrefix + @($cmd2[1..($cmd2.Count-1)])) | Out-Null
  }

  # SlowQuery: rpc_completed + sql_batch_completed
  $slowJsonl = Join-Path $tmpDir ("${prefix}_slow.jsonl")
  if (Test-Path $slowJsonl) { Remove-Item -Force $slowJsonl }

  $slowRpc = Join-Path $tmpDir ("${prefix}_slow_rpc.jsonl")
  $slowBatch = Join-Path $tmpDir ("${prefix}_slow_batch.jsonl")
  & $DotNetExe run --project (Join-Path $RepoRoot 'src/XelDump') -- $path --export-jsonl $slowRpc --filter rpc_completed | Out-Null
  & $DotNetExe run --project (Join-Path $RepoRoot 'src/XelDump') -- $path --export-jsonl $slowBatch --filter sql_batch_completed | Out-Null

  if (Test-Path $slowRpc) { Get-Content -LiteralPath $slowRpc | Add-Content -LiteralPath $slowJsonl }
  if (Test-Path $slowBatch) { Get-Content -LiteralPath $slowBatch | Add-Content -LiteralPath $slowJsonl }

  if (Test-JsonlNonEmpty $slowJsonl) {
    $key = Hash8 $prefix
    if (-not [string]::IsNullOrWhiteSpace($ws)) {
      $inputMdRoot = Join-Path $ws 'inputMD'
      # NOTE: inputMD is unfiltered (always export all events to markdown)
      $oldRanges = $env:XEL_TOOLKIT_RANGES_JSON
      $env:XEL_TOOLKIT_RANGES_JSON = $null

      $cmd = @('python', (Join-Path $RepoRoot 'py/xel_to_md.py'), '--jsonl', $slowJsonl, '--out', $inputMdRoot, '--key', $key, '--event', 'rpc_completed', '--event-type', 'SlowQuery', '--source', $path)
      & $PyExe @($PyPrefix + @($cmd[1..($cmd.Count-1)]))

      $cmd = @('python', (Join-Path $RepoRoot 'py/xel_to_md.py'), '--jsonl', $slowJsonl, '--out', $inputMdRoot, '--key', $key, '--event', 'sql_batch_completed', '--event-type', 'SlowQuery', '--source', $path)
      & $PyExe @($PyPrefix + @($cmd[1..($cmd.Count-1)]))

      $env:XEL_TOOLKIT_RANGES_JSON = $oldRanges
    }

    $cmd2 = @('python', (Join-Path $RepoRoot 'py/generate_reports.py'), '--slowquery-jsonl', $slowJsonl, '--slow-threshold', $SlowThreshold, '--source-xel', $path, '--prefix', $prefix, '--out', $fileOut)
    if ($StartJst) { $cmd2 += @('--start', $StartJst) }
    if ($EndJst) { $cmd2 += @('--end', $EndJst) }
    if ($env:XEL_TOOLKIT_RANGES_JSON) { $cmd2 += @('--ranges-json', $env:XEL_TOOLKIT_RANGES_JSON) }
    & $PyExe @($PyPrefix + @($cmd2[1..($cmd2.Count-1)])) | Out-Null
  }

  if (Test-Path $slowRpc) { Remove-Item -Force $slowRpc -ErrorAction SilentlyContinue }
  if (Test-Path $slowBatch) { Remove-Item -Force $slowBatch -ErrorAction SilentlyContinue }

  Write-Host "   Done: $prefix"
}

foreach ($inp in $Inputs) {
  $expanded = @(Get-ChildItem -LiteralPath $inp -ErrorAction SilentlyContinue)
  if ($expanded.Count -gt 0) {
    foreach ($e in $expanded) { RunOne $e.FullName }
  } else {
    $glob = @(Get-ChildItem -Path $inp -ErrorAction SilentlyContinue)
    if ($glob.Count -gt 0) {
      foreach ($e in $glob) { RunOne $e.FullName }
    } else {
      RunOne $inp
    }
  }
}

Write-Host "All done. Reports in: $OutDir"
