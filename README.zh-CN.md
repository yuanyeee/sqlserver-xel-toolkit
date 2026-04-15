# sqlserver-xel-toolkit

🌐 语言: [日本語](./README.md) | [English](./README.en.md) | **简体中文**

> README 已按语言拆分维护。请使用上方链接在日语、英语、中文之间切换。

这是一个可在 macOS / Windows / Linux 上对 SQL Server Extended Events (`.xel`) 进行**离线分析**的工具集，可一次性生成 Markdown 报告、Excel 报告以及跨批次聚合分析结果。

> **注意：**`.xel` 通常包含 SQL 文本、主机名等敏感信息，请勿提交到仓库（`.gitignore` 已排除）。

---

## 功能

| 功能 | 说明 |
|---|---|
| XEL 解析 | 自动识别并提取 DeadLock / SlowQuery / Blocking 事件 |
| Markdown 输出 | 导出 IntegratedTool 兼容的 Markdown 文件 |
| Excel 报告 | 自动生成多维分析工作簿 |
| 时间范围过滤 | 通过 `ranges.json` 控制抽取时间段 |
| 全文检索 | 使用 SQLite FTS5 检索历史报告 |
| 跨批次聚合 | 合并多个 Run / XEL 进行汇总分析 |
| GUI | 工作区管理、报告列表、Markdown 预览 |

---

## 环境要求

- macOS / Windows / Linux
- .NET 8+（建议 LTS）：https://dotnet.microsoft.com/download/dotnet/8.0
- Python 3.9+

---

## 安装

### uv（推荐）

```bash
# macOS / Linux
./scripts/setup_uv.sh
```

```powershell
# Windows (PowerShell)
.\scripts\setup_uv.ps1
```

> 若公司策略启用 `AllSigned` 导致脚本签名限制，请使用：
> ```cmd
> scripts\setup_uv.bat
> ```

若尚未安装 uv，请先安装：https://astral.sh/uv

### pip / venv

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r py/requirements.txt
```

---

## 启动 GUI

### uv（推荐：安装 + 启动一条命令）

```bash
# macOS / Linux
./scripts/run_gui_uv.sh
```

```powershell
# Windows (PowerShell)
.\scripts\run_gui_uv.ps1
```

> 若 `AllSigned` 限制执行，请使用：
> ```cmd
> scripts\run_gui_uv.bat
> ```

### pip / venv

```bash
./run_gui.sh
```

---

## GUI 使用流程

| 标签页 | 说明 |
|---|---|
| 📋 报告浏览 | Run 列表、文件列表、报告预览、全文检索 |
| ▶ 新建执行 | 选择 `.xel` 文件并生成报告 |
| 📁 inputMD | 浏览已生成的 Markdown |
| 📊 聚合分析 | 生成跨 Run 的聚合 Excel |
| ⏰ 时间范围 | 在 `ranges.json` 中管理过滤时间段 |
| ⚙ 设置与维护 | 工作区切换/删除、维护操作 |

### 日常推荐流程

1. 在 **Workspace → Switch...** 中选择或新建工作区。
2. 在 **▶ 新建执行** 选择 `.xel`，输出到 `runs/<timestamp>/`。
3. 在 **📋 报告浏览** 查看生成结果。
4. 使用顶部搜索框进行 SQLite FTS5 全文检索。
5. 需要跨天统计时，在 **📊 聚合分析** 执行汇总。
6. 需要时在 **⏰ 时间范围** 设置过滤条件。

---

## 命令行生成报告

```bash
# macOS / Linux 示例
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

### 输出文件

- `*_deadlock_report.md`, `*_deadlock.xlsx`
- `*_blocking_report.md`, `*_blocking.xlsx`
- `*_slowquery_report.md`, `*_slowquery.xlsx`
- `agg_*_deadlock.xlsx`, `agg_*_blocking.xlsx`, `agg_*_slowquery.xlsx`

---

## 可选：连接 SQL Server 解析对象名

Blocking 事件中可能包含 `database_id` / `object_id` / `index_id`。
设置环境变量 `MSSQL_CONNSTR` 后，工具会尝试解析真实对象名（`table` / `index`）并写入报告。

- 未设置：完全离线执行，不报错。
- 已设置但连接失败：仅告警，处理继续。

```bash
export MSSQL_CONNSTR='Server=...;Database=master;User Id=...;Password=...;TrustServerCertificate=True;'
./run.sh ~/Downloads/blocking*.xel -o ./reports
```

---

## 查看 XEL 原始事件结构

```bash
dotnet run --project src/XelDump -- \
  ~/Downloads/deadlock*.xel -o ./output --max 2000
```
