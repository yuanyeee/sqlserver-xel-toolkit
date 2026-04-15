# sqlserver-xel-toolkit

🌐 Language: **日本語** | [English](./README.en.md) | [简体中文](./README.zh-CN.md)

> このリポジトリの README は言語ごとに分割しています。上記リンクから各言語版へ移動できます。

SQL Server Extended Events (`.xel`) を **macOS / Windows / Linux 上でオフライン解析**し、
Markdown / Excel レポートと横断集計まで一括生成するツール群です。

> **Note:** `.xel` は SQL 文やホスト名など機微情報を含む可能性が高いため、**リポジトリにはコミットしません**（`.gitignore` で除外）。

---

## 機能概要

| 機能 | 説明 |
|---|---|
| XEL 解析 | DeadLock / SlowQuery / Blocking イベントを自動判別・抽出 |
| Markdown 生成 | 全イベントを IntegratedTool 互換の MD ファイルに出力 |
| Excel 集計 | 多角度分析 XLSX を自動生成 |
| 時間範囲フィルタ | `ranges.json` で指定時間帯のみ抽出・表示 |
| 全文検索 | SQLite FTS5 による過去レポートの全文検索 |
| 横断集計 | 複数 Run / XEL をまとめた集計・分析 Excel を生成 |
| GUI | ワークスペース管理・レポート一覧・MD プレビュー |

---

## 前提

- macOS / Windows / Linux
- .NET 8+（LTS 推奨。ダウンロード: https://dotnet.microsoft.com/download/dotnet/8.0 ）
- Python 3.9+

---

## セットアップ

### uv（推奨）

```bash
# macOS / Linux
./scripts/setup_uv.sh
```

```powershell
# Windows (PowerShell)
.\scripts\setup_uv.ps1
```

> **企業GPO (AllSigned) で署名エラーが出る場合は `.bat` 版を使用してください**
> ```cmd
> scripts\setup_uv.bat
> ```

uv が未インストールの場合は先にインストールしてください: https://astral.sh/uv

### pip / venv

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r py/requirements.txt
```

---

## GUI 起動

### uv（推奨 — セットアップと起動を一括で実行）

```bash
# macOS / Linux
./scripts/run_gui_uv.sh
```

```powershell
# Windows (PowerShell)
.\scripts\run_gui_uv.ps1
```

> **企業GPO (AllSigned) で署名エラーが出る場合は `.bat` 版を使用してください**
> ```cmd
> scripts\run_gui_uv.bat
> ```

### pip / venv

```bash
./run_gui.sh
```

---

## GUI の使い方

GUI は以下のタブで構成されています。

| タブ | 説明 |
|---|---|
| 📋 レポート閲覧 | 実行一覧・ファイル一覧・レポートプレビュー・全文検索 |
| ▶ 新規実行 | XEL ファイルを選択して解析・レポート生成 |
| 📁 inputMD | 生成済み Markdown の閲覧 |
| 📊 集計・分析 | 複数 Run をまとめた横断集計 Excel を生成 |
| ⏰ 時間範囲 | 抽出対象の時間帯を `ranges.json` で管理 |
| ⚙ 設定・管理 | ワークスペースの切り替え・削除・メンテナンス |

### 基本フロー

1. **メニュー「ワークスペース」→「切り替え…」** でワークフォルダを選択または新規作成
   - 「切り替え…」ダイアログの **「削除…」ボタン**で不要なワークスペースをリスト削除・フォルダ削除できます
2. **「▶ 新規実行」タブ**で `.xel` を選択 → 解析して `runs/<timestamp>/` に出力
3. **「📋 レポート閲覧」タブ**で生成済みレポートを選択してプレビュー
4. 上部検索ボックスで全文検索（SQLite FTS5）
5. **「📊 集計・分析」タブ**で複数日の横断集計 Excel を生成
6. **「⏰ 時間範囲」タブ**で時間帯フィルタを設定すると、指定した時間帯のイベントのみを対象にできます

---

## 日次 XEL 更新フロー

```
1. 新しい XEL をダウンロード
2. 「▶ 新規実行」タブで XEL を選択 → 自動解析・レポート生成
3. 「📋 レポート閲覧」タブで本日分を確認（時間範囲フィルタで絞り込み可）
4. 必要に応じて「📊 集計・分析」タブで複数日の横断集計を実行
```

---

## コマンドライン レポート生成

```bash
# macOS / Linux（例: 3種類の XEL をまとめて処理）
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

> **企業GPO (AllSigned) で署名エラーが出る場合は `.bat` 版を使用してください**
> ```cmd
> run.bat C:\Users\you\Downloads\blocking*.xel ... -o .\reports -SlowThresholdSec 3
> ```

### 出力ファイル

| ファイル | 内容 |
|---|---|
| `*_deadlock_report.md` | Deadlock の Markdown レポート |
| `*_deadlock.xlsx` | Deadlock 集計 Excel |
| `*_blocking_report.md` | Blocking の Markdown レポート |
| `*_blocking.xlsx` | Blocking 集計 Excel |
| `*_slowquery_report.md` | SlowQuery の Markdown レポート |
| `*_slowquery.xlsx` | SlowQuery 集計 Excel |
| `agg_*_deadlock.xlsx` | 横断集計 Deadlock Excel（「📊 集計・分析」タブで生成） |
| `agg_*_blocking.xlsx` | 横断集計 Blocking Excel |
| `agg_*_slowquery.xlsx` | 横断集計 SlowQuery Excel |

---

## 集計 Excel のシート一覧

### DeadLock (`*_deadlock.xlsx`)

| シート | 内容 |
|---|---|
| Events | 全 DeadLock イベント一覧 |
| Objects | 関与オブジェクト出現頻度 |
| Hostnames | ホスト名別集計 |
| Logins | ログイン名別集計 |
| IsolationLevels | 分離レベル別集計 |
| Processes | 全プロセス詳細 |
| VictimSQL_Fingerprint | 被害 SQL のフィンガープリント集計 |

### SlowQuery (`*_slowquery.xlsx`)

| シート | 内容 |
|---|---|
| Events | 全 SlowQuery イベント一覧 ※ |
| Top50_Duration | 実行時間 Top50 |
| ByDatabase | DB 別集計（件数 / 平均 / 最大） |
| ByApp | クライアントアプリ別集計 |
| ByContext | コンテキスト別集計 |
| ByUser | ユーザー別集計 |
| ByObject | オブジェクト別集計 |
| ByHostname | ホスト名別集計 |
| Table_Guess | SQL から推定テーブル一覧 |
| SQL_Fingerprint | SQL フィンガープリント集計（件数 / 平均 / 最大） |

> ※ **Events シートの列構成**
>
> `event` / `timestamp` / `duration_us` / `duration_sec` / `cpu_time` / `logical_reads` / `physical_reads` / `writes` / `row_count` / `database_name` / `username` / `client_app_name` / `client_hostname` / `session_id` / `object_name` / `context_raw` / `context_key` / `context_module` / `context_function` / `context_process` / `sql_text`
>
> `rpc_completed` イベントで XEL に `statement` フィールドが存在する場合は、末尾に **`statement` 列**が追加されます。

### Blocking (`*_blocking.xlsx`)

| シート | 内容 |
|---|---|
| Events | 全 Blocking イベント一覧 |
| Top100_Duration | ブロック時間 Top100 |
| ByBlockedSpid | 被ブロック SPID 別集計 |
| ByBlockingSpid | ブロッカー SPID 別集計 |
| ByDatabase | DB 別集計 |
| ByLockMode | ロックモード別集計 |
| Blocked_Table_Guess | 被ブロック SQL の推定テーブル |
| Blocking_Table_Guess | ブロッカー SQL の推定テーブル |
| Blocked_Context | 被ブロック SQL のコンテキスト集計 |
| Blocking_Context | ブロッカー SQL のコンテキスト集計 |
| Blocked_SQL_Fingerprint | 被ブロック SQL フィンガープリント |
| Blocking_SQL_Fingerprint | ブロッカー SQL フィンガープリント |

---

## SQL Server 接続によるオブジェクト名解決（任意）

Blocking レポートでは `database_id` / `object_id` / `index_id` が取得できます。
環境変数 `MSSQL_CONNSTR` を設定すると **実オブジェクト名（table / index）を解決**してレポートに追記します。

- **未設定**: エラーなしでオフラインのままレポート生成
- **設定済みで接続不可**: 警告のみで処理継続

```bash
export MSSQL_CONNSTR='Server=...;Database=master;User Id=...;Password=...;TrustServerCertificate=True;'
./run.sh ~/Downloads/blocking*.xel -o ./reports
```

---

## XEL のイベント構造を確認したい場合

```bash
dotnet run --project src/XelDump -- \
  ~/Downloads/deadlock*.xel -o ./output --max 2000
```
