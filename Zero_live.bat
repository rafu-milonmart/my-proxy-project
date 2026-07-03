@echo off
cd /d "%~dp0"
title ZeroLive
setlocal enabledelayedexpansion

set PYTHONUNBUFFERED=1

set PYTHON_DIR=%~dp0python
set PYTHON=%PYTHON_DIR%\python.exe
set PIP=%PYTHON_DIR%\Scripts\pip.exe

if not exist "%PYTHON%" (
    echo [ERROR] Python not found. Run installer.bat first.
    pause
    exit /b 1
)

REM Check for updates via GitHub API (no git needed)
echo Checking for updates...
set VERSION_FILE=%~dp0version.txt
set GITHUB_API=https://api.github.com/repos/rafu-milonmart/my-proxy-project/commits/master

for /f %%a in ('powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri '%GITHUB_API%' -UseBasicParsing | ConvertFrom-Json; Write-Output $r.sha } catch { Write-Output '__FAIL__' }"') do set LATEST_SHA=%%a

if not "!LATEST_SHA!"=="__FAIL__" if not "!LATEST_SHA!"=="" (
    set LOCAL_SHA=
    if exist "!VERSION_FILE!" set /p LOCAL_SHA=<"!VERSION_FILE!"
    if not "!LATEST_SHA!"=="!LOCAL_SHA!" (
        echo.
        echo [UPDATE] New version found! Downloading...
        powershell -NoProfile -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $z = '%TEMP%\zero_live_update.zip'; Invoke-WebRequest -Uri 'https://github.com/rafu-milonmart/my-proxy-project/archive/master.zip' -OutFile $z; Expand-Archive -Path $z -DestinationPath '%TEMP%\zero_live_update' -Force }"
        robocopy "%TEMP%\zero_live_update\my-proxy-project-master" "%~dp0" /E /XF "Zero_live.bat" /XD "python"
        if !errorlevel! lss 8 (
            echo !LATEST_SHA! > "!VERSION_FILE!"
            if exist "%PIP%" (
                "%PIP%" install -r requirements.txt --quiet
            ) else (
                "%PYTHON%" -m pip install -r requirements.txt --quiet
            )
            echo Updated to latest version.
        ) else (
            echo [WARN] Update copy failed (robocopy exit !errorlevel!). Skipping.
        )
        del "%TEMP%\zero_live_update.zip" >nul 2>&1
        rmdir /S /Q "%TEMP%\zero_live_update" >nul 2>&1
    ) else (
        echo You are up to date.
    )
) else (
    echo Could not check for updates.
)
echo.

REM Start app (loop so in-app restart works)
:RESTART
echo Starting ZeroLive...
start /b "" http://127.0.0.1:9090
set PORT=9090
"%PYTHON%" app.py
echo App exited (code !errorlevel!). Restarting in 3s...
timeout /t 3 /nobreak >nul
goto RESTART
