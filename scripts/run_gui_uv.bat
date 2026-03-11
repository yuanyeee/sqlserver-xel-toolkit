@echo off
:: GPO AllSigned 環境でも動作する run_gui_uv.ps1 の .bat 版
:: バッチファイルは PowerShell ExecutionPolicy の対象外です。
setlocal
pushd "%~dp0.."

where uv >nul 2>&1
if errorlevel 1 (
    echo uv not found. Install from https://astral.sh/uv and restart the terminal.
    exit /b 1
)

uv venv
uv pip install -r py\requirements.txt
uv run -m py.ui.app
popd
