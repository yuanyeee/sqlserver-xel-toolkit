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

## 使い方（予定）
```bash
# 例: XEL を解析して output/ に出す
./run.sh ~/Downloads/Slow_Queries_0_*.xel
```
