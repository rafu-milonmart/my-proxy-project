@echo off
cd /d "%~dp0"
title ZeroLive
setlocal enabledelayedexpansion

set PYTHONUNBUFFERED=1

set PYTHON_DIR=%~dp0python
set PYTHON=%PYTHON_DIR%\python.exe
set PIP=%PYTHON_DIR%\Scripts\pip.exe

if not exist "%PYTHON%" (
    echo [ERROR] Python not found. Run installer first.
    pause
    exit /b 1
)

REM Clean stale temp files from previous update attempts
del "%TEMP%\zero_live_update.zip" >nul 2>&1
rmdir /S /Q "%TEMP%\zero_live_update" >nul 2>&1

REM Check for updates via GitHub API (no git needed)
echo Checking for updates...
set VERSION_FILE=%~dp0version.txt
set GITHUB_API=https://api.github.com/repos/rafu-milonmart/my-proxy-project/commits/master

set LATEST_SHA=__FAIL__
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri '%GITHUB_API%' -UseBasicParsing; Write-Output ($r.Content | ConvertFrom-Json).sha } catch { Write-Output '__FAIL__' }" > "%TEMP%\zero_live_sha.txt"
set /p LATEST_SHA=<"%TEMP%\zero_live_sha.txt"

if "!LATEST_SHA!"=="__FAIL__" goto :NO_UPDATE
if "!LATEST_SHA!"=="" goto :NO_UPDATE
set LOCAL_SHA=
if exist "!VERSION_FILE!" set /p LOCAL_SHA=<"!VERSION_FILE!"
if "!LATEST_SHA!"=="!LOCAL_SHA!" goto :UP_TO_DATE

echo(
echo [UPDATE] New version found! Downloading...
powershell -NoProfile -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $z = '%TEMP%\zero_live_update.zip'; Invoke-WebRequest -Uri 'https://github.com/rafu-milonmart/my-proxy-project/archive/master.zip' -OutFile $z; Expand-Archive -Path $z -DestinationPath '%TEMP%\zero_live_update' -Force }"
echo [UPDATE] Applying update...
set "PROJ_DIR=%~dp0"
set "PROJ_DIR=!PROJ_DIR:~0,-1!"
set "UPDATE_SRC=%TEMP%\zero_live_update\my-proxy-project-master"

REM Exclude Zero_live.bat so we don't overwrite ourselves while running
robocopy "!UPDATE_SRC!" "!PROJ_DIR!" /E /XD "python" /XF "version.txt" "Zero_live.bat" /IS /IT
set RC=!ERRORLEVEL!

if !RC! LSS 8 (
    echo(!LATEST_SHA!>"!VERSION_FILE!"
    "!PYTHON!" -m pip install -r "!PROJ_DIR!\requirements.txt" --quiet
    REM Save new bat so it gets applied on next restart
    if exist "!UPDATE_SRC!\Zero_live.bat" copy /Y "!UPDATE_SRC!\Zero_live.bat" "!PROJ_DIR!\Zero_live.new.bat" >nul 2>&1
    echo [UPDATE] Update complete.
) else (
    echo [UPDATE] Copy failed, skipping.
)
del "%TEMP%\zero_live_update.zip" >nul 2>&1
rmdir /S /Q "%TEMP%\zero_live_update" >nul 2>&1
echo [UPDATE] Restarting...
goto :AFTER_UPDATE

:UP_TO_DATE
echo You are up to date.
goto :AFTER_UPDATE

:NO_UPDATE
echo Could not check for updates.

:AFTER_UPDATE
echo(

REM Start app (loop so in-app restart works)
set BROWSER_OPENED=0
:RESTART
set PORT=9090
"%PYTHON%" app.py
if "%BROWSER_OPENED%"=="0" (
  set BROWSER_OPENED=1
  timeout /t 2 /nobreak >nul
  start /b "" http://127.0.0.1:9090
)
REM Apply new bat if one was saved during update
if exist "%~dp0Zero_live.new.bat" (
    copy /Y "%~dp0Zero_live.new.bat" "%~dp0Zero_live.bat" >nul 2>&1
    del "%~dp0Zero_live.new.bat" >nul 2>&1
    echo [UPDATE] Batch file updated.
)
echo App exited (code !errorlevel!). Restarting in 3s...
timeout /t 3 /nobreak >nul
goto RESTART
