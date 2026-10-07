@echo off
chcp 65001 > nul
cd /d %~dp0
where py > nul 2>&1
if errorlevel 1 (
  echo Python が見つかりません。https://www.python.org/downloads/windows/ から Python 3.11 以降をインストールしてください。
  echo インストール時に「Add python.exe to PATH」にチェックを入れてください。
  pause
  exit /b 1
)
if not exist .venv (
  echo 初回セットアップ中です（数分かかります）...
  py -3 -m venv .venv || goto :err
  .venv\Scripts\python -m pip install --upgrade pip > nul
  .venv\Scripts\python -m pip install -r requirements.txt || goto :err
)
.venv\Scripts\python run.py %*
goto :eof
:err
echo セットアップに失敗しました。ネットワーク接続を確認してください。
pause
