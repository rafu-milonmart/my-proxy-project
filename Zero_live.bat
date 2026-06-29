@echo off
cd /d "%~dp0"
title ZeroLive
setlocal enabledelayedexpansion

set PYTHON_DIR=%~dp0python
set PYTHON=%PYTHON_DIR%\python.exe
set PIP=%PYTHON_DIR%\Scripts\pip.exe

if not exist "%PYTHON%" (
    echo [ERROR] Python not found. Run installer.bat first.
    pause
    exit /b 1
)

REM Check for updates (silently)
echo Checking for updates...
git fetch origin master >nul 2>&1
if !errorlevel! equ 0 (
    for /f %%i in ('git log HEAD..origin/master --oneline 2^>nul') do set UPDATES=%%i
    if defined UPDATES (
        echo.
        echo [UPDATE] New commits found on GitHub:
        git log HEAD..origin/master --oneline 2>nul
        echo.
        choice /c YN /m "Pull updates and restart?"
        if not errorlevel 2 (
            echo Pulling updates...
            git pull origin master
            if !errorlevel! equ 0 (
                echo Updates applied! Restarting...
                "%PIP%" install -r requirements.txt --quiet
            ) else (
                echo [WARNING] Update failed. Starting with current version.
            )
        ) else (
            echo Skipping update.
        )
    ) else (
        echo You are up to date.
    )
) else (
    echo Git not available, skipping update check.
)
echo.

REM Start app
echo Starting ZeroLive...
start "" http://127.0.0.1:9090
set PORT=9090
"%PYTHON%" app.py

if !errorlevel! neq 0 (
    echo.
    echo App exited with error code !errorlevel!.
    pause
)
