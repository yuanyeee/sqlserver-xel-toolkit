# sqlserver-xel-toolkit

SQL Server Extended Events (`.xel`) を **macOS上でオフライン解析**し、
そのままレポート（Markdown/Excel/集計）まで一括で生成するためのツール群です。

> Note: `.xel` はSQL文やホスト名など機微情報を含む可能性が高いため、**リポジトリにはコミットしません**（`.gitignore` で除外）。

## 目標（B: 一気通貫）
- `.xel` を選ぶ
- イベントを解析（SlowQuery / Deadlock / Blocking を自動判別）
- 既存の IntegratedTool 相当の集計・出力（Markdown/Excel）を生成

## 前提
- macOS
- .NET 6+
- Python 3.8+

## 進捗
- [ ] XEL のイベント一覧・フィールドをスキャンしてサマリ出力
- [ ] Deadlock XEL → レポート生成
- [ ] SlowQuery XEL → レポート生成
- [ ] Blocking XEL → 可能な範囲でレポート生成（セッション構成次第）

## 使い方

### 1) セットアップ（初回のみ）

#### pip/venv
```bash
cd sqlserver-xel-toolkit
python3 -m venv .venv
. .venv/bin/activate
pip install -r py/requirements.txt
```

#### uv（推奨）
```bash
cd sqlserver-xel-toolkit
./scripts/setup_uv.sh
```

Windows（PowerShell）:
```powershell
cd sqlserver-xel-toolkit
.\scripts\setup_uv.ps1
```

## IntegratedTool（サブモジュール）

このリポジトリには `yuanyeee/IntegratedTool` を **git submodule** として同梱しています。

初回チェックアウト後：
```bash
git submodule update --init --recursive
```

### サブモジュールを最新に更新（ローカル作業用）
```bash
./scripts/update_integratedtool.sh
```

### コンパイルなしで起動（uv）
```bash
./scripts/run_integratedtool_uv.sh
```

GUI の `Open IntegratedTool` ボタンから `unified_report_viewer.py` を起動できます。

## GUI（一覧管理 / 全文検索 / 高度フィルタ / MDプレビュー）

> 現在は最小実装（Workspace + Run一覧 + Report一覧 + MDプレビュー + FTS検索）まで。

起動:

#### 既存（pip/venv）
```bash
./run_gui.sh
```

#### uv（コンパイル無し）
```bash
./scripts/run_gui_uv.sh
```

Windows（PowerShell）:
```powershell
.\scripts\run_gui_uv.ps1
```

使い方:
1. `Open Workspace` で作業フォルダを選択（`workspace.db` を作成）
2. `New Run` で `.xel` を選択 → 解析して `runs/<timestamp>/` に出力
3. 生成済みレポートは一覧から選択して右側で Markdown プレビュー
4. 上部検索ボックスで全文検索（SQLite FTS5）

### 2) レポート生成（推奨）

macOS/Linux:
```bash
# 例: 3種類のXELをまとめて処理
./run.sh \
  ~/Downloads/blocking*.xel \
  ~/Downloads/deadlock*.xel \
  ~/Downloads/Slow_Queries*.xel \
  -o ./reports \
  --slow-threshold 3
```

Windows (PowerShell):
```powershell
.\run.ps1 C:\Users\you\Downloads\blocking*.xel C:\Users\you\Downloads\deadlock*.xel C:\Users\you\Downloads\Slow_Queries*.xel -o .\reports --slow-threshold 3
```

出力先（例）:
- `reports/<source>_<timestamp>_deadlock_report.md`
- `reports/<source>_<timestamp>_slowquery.xlsx`
- `reports/<source>_<timestamp>_slowquery_report.md`
- `reports/<source>_<timestamp>_xel_summary.md/json`

### SQL Server 接続によるオブジェクト名解決（任意）

blocking のレポートでは `database_id/object_id/index_id` が取れるため、
環境変数 `MSSQL_CONNSTR` を設定すると **実オブジェクト名（table/index）を解決**してレポートに追記します。

- 未設定の場合：**何もせず**（エラーなし）離線のままレポート生成します
- 設定したが接続できない場合：**警告のみ**で処理継続します

例:
```bash
export MSSQL_CONNSTR='Server=...;Database=master;User Id=...;Password=...;TrustServerCertificate=True;'
./run.sh ~/Downloads/blocking*.xel -o ./reports
```

### 3) XEL のイベント構造だけ見たい場合
```bash
/usr/local/share/dotnet/dotnet run --project src/XelDump -- \
  ~/Downloads/deadlock*.xel -o ./output --max 2000
```
