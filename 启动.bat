@echo off
chcp 65001 >nul 2>nul
setlocal EnableExtensions
title CustomerAgent
cd /d "%~dp0"

echo ============================================================
echo  CustomerAgent
echo  workdir : %CD%
echo ============================================================

rem ---- 1) locate python -------------------------------------------
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY if exist "python.exe" set "PY=python.exe"
if not defined PY powershell -NoProfile -Command "if (Get-Command python -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>nul && set "PY=python"
if not defined PY powershell -NoProfile -Command "if (Get-Command py -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>nul && set "PY=py -3"
if not defined PY powershell -NoProfile -Command "if (Get-Command uv -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>nul && set "PY=uv run python"
if not defined PY goto :nopython
echo  python  : %PY%

rem ---- 2) stop old instance (same app.py) -------------------------
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*app.py*' }; if ($p) { $p | ForEach-Object { Write-Host ('  stopping old pid ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } } else { Write-Host '  no running instance' }"
timeout /t 2 /nobreak >nul 2>nul

rem ---- 3) run -----------------------------------------------------
if not exist "logs" mkdir "logs"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH:mm:ss"`) do set "TS=%%T"
if not defined TS set "TS=unknown"
echo [%TS%] start %PY% app.py>>"logs\launch_stdout.log"

%PY% app.py
set "RC=%ERRORLEVEL%"

echo.
echo ------------------------------------------------------------
if "%RC%"=="0" (echo  app exited normally) else (echo  [ERROR] app exited with code %RC%)
echo  app log  : %CD%\logs\app.log
echo  expect   : first line = runtime identity ^(source dir / DB path^)
echo ------------------------------------------------------------
pause
exit /b %RC%

:nopython
echo.
echo [ERROR] python not found in this folder.
echo         expected : .venv\Scripts\python.exe  (or python / py / uv in PATH)
echo         fix      : copy the .venv folder here, or install Python 3.11+
echo.
pause
exit /b 1
