@echo off
chcp 65001 > nul
cd /d %~dp0
rem データを日付付きでバックアップします（タスクスケジューラで毎日実行する運用を推奨）
rem   backup.bat            … このフォルダの backup に保存
rem   backup.bat D:\GHMSバックアップ … 指定のフォルダに保存
set "DEST=%~dp0backup"
if not "%~1"=="" set "DEST=%~1"

rem インストール版（GHMS.exe）があれば、その --backup を使う（データは C:\ProgramData\GHMS\data）
set "EXE="
if exist "%~dp0GHMS.exe" set "EXE=%~dp0GHMS.exe"
if not defined EXE if exist "%ProgramFiles%\GHMS\GHMS.exe" set "EXE=%ProgramFiles%\GHMS\GHMS.exe"
if not defined EXE goto :zip
"%EXE%" --backup "%DEST%"
if errorlevel 1 goto :fail
echo バックアップしました: %DEST%
goto :eof

:zip
rem 開発・ZIP版：このフォルダの data\ghms.sqlite3 をバックアップする
set "DB=%~dp0data\ghms.sqlite3"
if not exist "%DB%" goto :nodb
if not exist ".venv\Scripts\python.exe" goto :novenv
if not exist "%DEST%" mkdir "%DEST%"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmm"') do set TS=%%i
.venv\Scripts\python -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()" "%DB%" "%DEST%\ghms_%TS%.sqlite3"
if errorlevel 1 goto :fail
echo バックアップしました: %DEST%\ghms_%TS%.sqlite3
goto :eof

:nodb
echo データが見つかりません: %DB%
echo インストール版のときは、GHMS をインストールしたPCで実行してください。
pause
exit /b 1

:novenv
echo Python の環境（.venv）がありません。先に start.bat を一度実行してください。
pause
exit /b 1

:fail
echo バックアップに失敗しました。
pause
exit /b 1
