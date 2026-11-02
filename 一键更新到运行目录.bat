@echo off
chcp 65001 >nul 2>nul
setlocal EnableExtensions
title CustomerAgent - update source into run folder
cd /d "%~dp0"
set "SRC=%CD%"

echo ============================================================
echo  update source : %SRC%
echo  (this folder is a DEPLOY PACKAGE; it has no .venv / no database)
echo ============================================================

rem ---- 1) target: 1st arg, else sibling Customer-Agent, else ask ----
set "DST=%~1"
if not defined DST if exist "..\Customer-Agent\app.py" set "DST=..\Customer-Agent"
if not defined DST if exist "..\CustomerAgent\app.py" set "DST=..\CustomerAgent"
if not defined DST if exist "..\Customer-Agent-main\app.py" set "DST=..\Customer-Agent-main"
if defined DST for %%i in ("%DST%") do set "DST=%%~fi"
if not defined DST (
  echo.
  echo  cannot auto-detect the run folder ^(the one with .venv + temp\channel_shop.db + app.py^).
  set /p DST= full path of that folder: 
)
if not exist "%DST%\app.py" (
  echo [ERROR] "%DST%\app.py" not found - that is not the run folder.
  pause & exit /b 1
)
echo  target        : %DST%
if not exist "%DST%\.venv\Scripts\python.exe" echo  [WARN] target has no .venv\Scripts\python.exe - start it after fixing deps

rem ---- 2) backup current source -------------------------------------
for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"`) do set "TS=%%T"
if not defined TS set "TS=backup"
set "BK=%DST%\temp\backup_%TS%"
echo  backup        : %BK%
for %%D in (Agent Channel core database Message ui utils) do (
  if exist "%DST%\%%D" robocopy "%DST%\%%D" "%BK%\%%D" /E /NFL /NDL /NJH /NJS /XF *.pyc >nul
)
if exist "%DST%\app.py" copy /y "%DST%\app.py" "%BK%\app.py" >nul 2>nul
if exist "%DST%\config.py" copy /y "%DST%\config.py" "%BK%\config.py" >nul 2>nul

rem ---- 3) copy new source -------------------------------------------
set "RC=0"
for %%D in (Agent Channel core database Message ui utils) do (
  if exist "%SRC%\%%D" (
    robocopy "%SRC%\%%D" "%DST%\%%D" /E /NFL /NDL /NJH /NJS /XF *.pyc >nul
    if errorlevel 8 set "RC=1"
  )
)
copy /y "%SRC%\app.py" "%DST%\app.py" >nul
copy /y "%SRC%\config.py" "%DST%\config.py" >nul
copy /y "%SRC%\启动.bat" "%DST%\启动.bat" >nul
if exist "%SRC%\config.json" copy /y "%SRC%\config.json" "%DST%\config.json" >nul
if not "%RC%"=="0" (
  echo [ERROR] robocopy failed - close the running app first, then retry.
  pause & exit /b 1
)

rem ---- 4) show what is now deployed ---------------------------------
echo.
for /f "usebackq tokens=*" %%L in (`findstr /r /c:"^BUILD_TAG" "%DST%\utils\build_tag.py"`) do set "TAGLINE=%%L"
if defined TAGLINE echo  deployed tag  : %TAGLINE%
findstr /c:"ORDER_DETAIL_PAGE_SIZE" "%DST%\Channel\pinduoduo\utils\API\order_api.py" >nul && echo  pageSize fix  : OK
if exist "%DST%\Channel\pinduoduo\anti_content.py" echo  anti-content  : OK
if not exist "%DST%\temp\channel_shop.db" echo  [WARN] no temp\channel_shop.db in target - app will open EMPTY
if not exist "%DST%\user_data" echo  [WARN] no user_data in target - log the accounts in once

rem ---- 5) start -----------------------------------------------------
echo.
echo  starting: %DST%\启动.bat
pushd "%DST%"
call "启动.bat"
popd
endlocal
exit /b 0
