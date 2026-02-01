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
```bash
cd sqlserver-xel-toolkit
python3 -m venv .venv
. .venv/bin/activate
pip install -r py/requirements.txt
```

### 2) レポート生成（推奨）
```bash
# 例: 3種類のXELをまとめて処理
./run.sh \
  ~/Downloads/blocking*.xel \
  ~/Downloads/deadlock*.xel \
  ~/Downloads/Slow_Queries*.xel \
  -o ./reports \
  --slow-threshold 3
```

出力先（例）:
- `reports/<source>_<timestamp>_deadlock_report.md`
- `reports/<source>_<timestamp>_slowquery.xlsx`
- `reports/<source>_<timestamp>_slowquery_report.md`
- `reports/<source>_<timestamp>_xel_summary.md/json`

> blocking のレポート生成はこれから実装します（blocked_process_report は検出できています）。

### 3) XEL のイベント構造だけ見たい場合
```bash
/usr/local/share/dotnet/dotnet run --project src/XelDump -- \
  ~/Downloads/deadlock*.xel -o ./output --max 2000
```
