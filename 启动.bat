@echo off
chcp 65001 >nul 2>nul
setlocal EnableExtensions
title CustomerAgent
cd /d "%~dp0"

echo ============================================================
echo  CustomerAgent
echo  workdir : %CD%
echo ============================================================

rem ---- 1) pick an interpreter that can actually import PyQt6 --------
rem 部署包故意不带 .venv（几百 MB），所以这里不只看本目录：
rem 邻居目录里已有的 .venv 也能直接用 —— 代码用新的，依赖用现成的。
set "PY="
call :try ".venv\Scripts\python.exe"
if not defined PY call :try "..\Customer-Agent\.venv\Scripts\python.exe"
if not defined PY call :try "..\CustomerAgent\.venv\Scripts\python.exe"
if not defined PY call :try "..\Customer-Agent-main\.venv\Scripts\python.exe"
if not defined PY call :try "python"
if not defined PY call :try "py -3"
if not defined PY call :try "uv run python"
if not defined PY goto :nopython
echo  python  : %PY%

rem ---- 2) stop old instance (same app.py) ----------------------------
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*app.py*' }; if ($p) { $p | ForEach-Object { Write-Host ('  stopping old pid ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } } else { Write-Host '  no running instance' }"
timeout /t 2 /nobreak >nul 2>nul

rem ---- 2.5) 数据体检：没有库/登录态就别指望能干活 --------------------
if not exist "temp\channel_shop.db" (
  echo.
  echo  [WARN] temp\channel_shop.db NOT found in this folder.
  echo         accounts / orders / knowledge are stored there - without it the app opens EMPTY.
  echo         fix: copy temp\channel_shop.db here, or run this source inside the folder
  echo              that already has it ^(e.g. C:\Users\Administrator\Desktop\Customer-Agent^).
  echo.
)
if not exist "user_data" (
  echo.
  echo  [WARN] user_data NOT found - the browser login state is missing.
  echo         order query / report needs it: log the account in once from the UI.
  echo.
)

rem ---- 3) run --------------------------------------------------------

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

:try
rem %%1 = candidate command; 成功则设置 PY 并返回
set "CAND=%~1"
if "%CAND%"=="" exit /b 1
echo %CAND% | find "\" >nul
if not errorlevel 1 if not exist "%CAND%" exit /b 1
%CAND% -c "import PyQt6" >nul 2>nul
if errorlevel 1 exit /b 1
set "PY=%CAND%"
exit /b 0

:nopython
echo.
echo [ERROR] no usable Python found for this folder.
echo         need an interpreter that can "import PyQt6" (Qt GUI deps).
echo.
echo  Option A ^(recommended^): run from the folder that already has .venv
echo            e.g. copy this folder's source over C:\Users\Administrator\Desktop\Customer-Agent
echo            then start it there ^(that folder has .venv^).
echo  Option B: put .venv here, or run "uv sync" in this folder to create it.
echo.
pause
exit /b 1
