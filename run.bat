@echo off
:: GPO AllSigned 環境でも動作する run.ps1 ラッパー
:: [scriptblock]::Create() で .ps1 を文字列として読み込み実行するため
:: PowerShell ExecutionPolicy / 企業GPO の署名要件を回避できます。
::
:: 使い方:
::   run.bat <xelファイル|glob> [追加xel...] [-o 出力先] [-SlowThresholdSec 秒] [-Start "YYYY-mm-dd HH:MM"] [-End "YYYY-mm-dd HH:MM"]

:: スクリプトのディレクトリを環境変数で渡す（[scriptblock]::Create() 経由では
:: $MyInvocation.MyCommand.Path が null になるため、$RepoRoot の代替として使用）
set "XEL_TOOLKIT_RUNDIR=%~dp0"

powershell -NoProfile -Command ^
  "& ([scriptblock]::Create([System.IO.File]::ReadAllText('%~dp0run.ps1', [System.Text.Encoding]::UTF8)))" %*
exit /b %errorlevel%
