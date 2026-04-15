# sqlserver-xel-toolkit

🌐 Language: [日本語](./README.md) | **English** | [简体中文](./README.zh-CN.md)

> README files are split by language. Use the links above to switch between Japanese, English, and Chinese.

A toolkit for **offline analysis of SQL Server Extended Events (`.xel`)** on macOS / Windows / Linux, with one-pass generation of Markdown reports, Excel reports, and cross-run aggregation.

> **Note:** `.xel` files often contain sensitive data (SQL text, hostnames, login names). Keep them out of Git history (`.gitignore` excludes them).

---

## Features

| Feature | Description |
|---|---|
| XEL parsing | Auto-detect and extract DeadLock / SlowQuery / Blocking events |
| Markdown output | Export all events to IntegratedTool-compatible Markdown files |
| Excel reports | Generate multi-angle analysis workbooks automatically |
| Time-range filtering | Filter extraction by `ranges.json` |
| Full-text search | Search historical reports with SQLite FTS5 |
| Cross-run aggregation | Aggregate multiple runs / xel files into summary Excel |
| GUI | Workspace management, report list, Markdown preview |

---

## Requirements

- macOS / Windows / Linux
- .NET 8+ (LTS recommended): https://dotnet.microsoft.com/download/dotnet/8.0
- Python 3.9+

---

## Setup

### uv (recommended)

```bash
# macOS / Linux
./scripts/setup_uv.sh
```

```powershell
# Windows (PowerShell)
.\scripts\setup_uv.ps1
```

> If your corporate GPO enforces `AllSigned`, use the `.bat` version:
> ```cmd
> scripts\setup_uv.bat
> ```

If uv is not installed yet: https://astral.sh/uv

### pip / venv

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r py/requirements.txt
```

---

## Launch GUI

### uv (recommended — setup + launch in one command)

```bash
# macOS / Linux
./scripts/run_gui_uv.sh
```

```powershell
# Windows (PowerShell)
.\scripts\run_gui_uv.ps1
```

> If `AllSigned` policy blocks script execution, use:
> ```cmd
> scripts\run_gui_uv.bat
> ```

### pip / venv

```bash
./run_gui.sh
```

---

## GUI workflow

| Tab | Description |
|---|---|
| 📋 Report Viewer | Run list, file list, report preview, full-text search |
| ▶ New Run | Choose `.xel` files and generate reports |
| 📁 inputMD | Browse generated Markdown files |
| 📊 Aggregate & Analyze | Build cross-run aggregated Excel |
| ⏰ Time Range | Manage extraction ranges in `ranges.json` |
| ⚙ Settings & Maintenance | Switch/delete workspaces, maintenance actions |

### Typical daily flow

1. Select or create a workspace from **Workspace → Switch...**.
2. In **▶ New Run**, select `.xel` files and generate output to `runs/<timestamp>/`.
3. Review generated reports in **📋 Report Viewer**.
4. Use the top search box for SQLite FTS5 full-text search.
5. Run cross-day aggregation in **📊 Aggregate & Analyze** when needed.
6. Optionally apply filters in **⏰ Time Range**.

---

## Command-line report generation

```bash
# macOS / Linux example
./run.sh \
  ~/Downloads/blocking*.xel \
  ~/Downloads/deadlock*.xel \
  ~/Downloads/Slow_Queries*.xel \
  -o ./reports \
  --slow-threshold 3
```

```powershell
# Windows (PowerShell)
.\run.ps1 `
  C:\Users\you\Downloads\blocking*.xel `
  C:\Users\you\Downloads\deadlock*.xel `
  C:\Users\you\Downloads\Slow_Queries*.xel `
  -o .\reports -SlowThresholdSec 3
```

### Output files

- `*_deadlock_report.md`, `*_deadlock.xlsx`
- `*_blocking_report.md`, `*_blocking.xlsx`
- `*_slowquery_report.md`, `*_slowquery.xlsx`
- `agg_*_deadlock.xlsx`, `agg_*_blocking.xlsx`, `agg_*_slowquery.xlsx`

---

## Optional: Resolve object names using SQL Server connection

Blocking events can include `database_id` / `object_id` / `index_id`.
If `MSSQL_CONNSTR` is set, the toolkit tries to resolve real object names (`table` / `index`) and append them to reports.

- Not set: fully offline, no error.
- Set but unreachable: warning only, processing continues.

```bash
export MSSQL_CONNSTR='Server=...;Database=master;User Id=...;Password=...;TrustServerCertificate=True;'
./run.sh ~/Downloads/blocking*.xel -o ./reports
```

---

## Inspect raw XEL event structure

```bash
dotnet run --project src/XelDump -- \
  ~/Downloads/deadlock*.xel -o ./output --max 2000
```
