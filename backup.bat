@echo off
chcp 65001 > nul
cd /d %~dp0
rem データフォルダを日付付きでバックアップします（タスクスケジューラで毎日実行する運用を推奨）
set DEST=%~dp0backup
if not "%~1"=="" set DEST=%~1
if not exist "%DEST%" mkdir "%DEST%"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmm"') do set TS=%%i
.venv\Scripts\python -c "import sqlite3,sys; s=sqlite3.connect('data/ghms.sqlite3'); d=sqlite3.connect(sys.argv[1]); s.backup(d); d.close(); s.close()" "%DEST%\ghms_%TS%.sqlite3"
if errorlevel 1 ( echo バックアップに失敗しました & pause & exit /b 1 )
echo バックアップしました: %DEST%\ghms_%TS%.sqlite3
