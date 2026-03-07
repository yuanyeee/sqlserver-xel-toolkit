# sqlserver-xel-toolkit

SQL Server Extended Events (`.xel`) を **macOS / Windows 上でオフライン解析**し、
そのままレポート（Markdown / Excel / 集計）まで一括で生成するためのツール群です。

IntegratedTool の集計・分析機能を内蔵しています（サブモジュール不要）。

> Note: `.xel` はSQL文やホスト名など機微情報を含む可能性が高いため、**リポジトリにはコミットしません**（`.gitignore` で除外）。

---

## 機能概要

| 機能 | 説明 |
|---|---|
| XEL 解析 | DeadLock / SlowQuery / Blocking イベントを自動判別・抽出 |
| Markdown 生成 | 全イベントを IntegratedTool 互換の MD ファイルに出力 |
| Excel 集計 | IntegratedTool 相当の多角度分析 XLSX を自動生成 |
| 時間範囲フィルタ | ranges.json で指定時間帯のみ抽出・表示 |
| 全文検索 | SQLite FTS5 による過去レポートの全文検索 |
| 統合集計 | 複数 Run / XEL をまとめた横断集計・分析 Excel を生成 |
| GUI | ワークスペース管理・レポート一覧・MD プレビュー |

---

## 前提

- macOS / Windows / Linux
- .NET 10+ (or match `TargetFramework` in `src/*/*.csproj`)
- Python 3.9+

---

## セットアップ（初回のみ）

### uv（推奨）

```bash
cd sqlserver-xel-toolkit
./scripts/setup_uv.sh
```

Windows（PowerShell）:
```powershell
cd sqlserver-xel-toolkit
.\scripts\setup_uv.ps1
```

### pip/venv

```bash
cd sqlserver-xel-toolkit
python3 -m venv .venv
. .venv/bin/activate
pip install -r py/requirements.txt
```

---

## GUI 起動

### uv（コンパイル無し）
```bash
./scripts/run_gui_uv.sh
```

Windows（PowerShell）:
```powershell
.\scripts\run_gui_uv.ps1
```

### 既存（pip/venv）
```bash
./run_gui.sh
```

### GUI の使い方

1. **ワークスペース…** でワークフォルダを選択（`workspace.db` を作成）
2. **新規実行** で `.xel` を選択 → 解析して `runs/<timestamp>/` に出力
3. 生成済みレポートは一覧から選択して右側で Markdown プレビュー
4. 上部検索ボックスで全文検索（SQLite FTS5）
5. **集計・分析** ボタンで IntegratedTool 相当の横断集計 Excel を生成

---

## 日次 XEL 更新フロー

毎日 XEL ファイルが更新される場合の推奨フロー：

```
1. 新しい XEL をダウンロード
2. GUI「新規実行」で XEL を選択 → 自動解析・レポート生成
3. レポート一覧から本日分を確認（時間範囲フィルタで絞り込み可）
4. 必要に応じて「集計・分析」で複数日の横断集計を実行
```

**時間範囲フィルタ** (`時間範囲 > 編集…`) を使うと、
指定した時間帯のイベントのみをレポート・検索対象にできます。

---

## コマンドライン レポート生成

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
.\run.ps1 C:\Users\you\Downloads\blocking*.xel C:\Users\you\Downloads\deadlock*.xel C:\Users\you\Downloads\Slow_Queries*.xel -o .\reports -SlowThresholdSec 3
```

### 出力ファイル（例）

| ファイル | 内容 |
|---|---|
| `*_deadlock_report.md` | Deadlock の Markdown レポート |
| `*_deadlock.xlsx` | Deadlock 集計 Excel（Objects/Hostnames/Logins/IsolationLevels/Processes/Fingerprint）|
| `*_blocking_report.md` | Blocking の Markdown レポート |
| `*_blocking.xlsx` | Blocking 集計 Excel（SPID別/DB別/LockMode/Table_Guess/Context/Fingerprint）|
| `*_slowquery_report.md` | SlowQuery の Markdown レポート |
| `*_slowquery.xlsx` | SlowQuery 集計 Excel（Top50/ByDB/ByApp/ByContext/Table_Guess/Fingerprint）|
| `agg_*_deadlock.xlsx` | 横断集計 Deadlock Excel（「集計・分析」機能で生成）|
| `agg_*_blocking.xlsx` | 横断集計 Blocking Excel |
| `agg_*_slowquery.xlsx` | 横断集計 SlowQuery Excel |

---

## 集計 Excel の分析シート一覧

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
| Events | 全 SlowQuery イベント一覧 |
| Top50_Duration | 実行時間 Top50 |
| ByDatabase | DB 別集計（件数/平均/最大） |
| ByApp | クライアントアプリ別集計 |
| ByContext | コンテキスト別集計 |
| ByUser | ユーザー別集計 |
| ByObject | オブジェクト別集計 |
| ByHostname | ホスト名別集計 |
| Table_Guess | SQL から推定テーブル一覧 |
| SQL_Fingerprint | SQL フィンガープリント集計（件数/平均/最大） |

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

blocking のレポートでは `database_id/object_id/index_id` が取れるため、
環境変数 `MSSQL_CONNSTR` を設定すると **実オブジェクト名（table/index）を解決**してレポートに追記します。

- 未設定の場合：**何もせず**（エラーなし）離線のままレポート生成します
- 設定したが接続できない場合：**警告のみ**で処理継続します

```bash
export MSSQL_CONNSTR='Server=...;Database=master;User Id=...;Password=...;TrustServerCertificate=True;'
./run.sh ~/Downloads/blocking*.xel -o ./reports
```

---

## XEL のイベント構造を確認したい場合

```bash
/usr/local/share/dotnet/dotnet run --project src/XelDump -- \
  ~/Downloads/deadlock*.xel -o ./output --max 2000
```
