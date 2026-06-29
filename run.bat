@echo off
cd /d "%~dp0"
title ZeroLive

REM Activate venv
if not exist ".venv" (
    echo [ERROR] Virtual environment not found. Run installer.bat first.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat

REM Check for updates (silently)
echo Checking for updates...
git fetch origin master >nul 2>&1
for /f %%i in ('git log HEAD..origin/master --oneline 2^>nul') do set UPDATES=%%i
if defined UPDATES (
    echo.
    echo [UPDATE] New commits found on GitHub:
    git log HEAD..origin/master --oneline 2>nul
    echo.
    choice /c YN /m "Pull updates and restart?"
    if errorlevel 2 (
        echo Skipping update.
    ) else (
        echo Pulling updates...
        git pull origin master
        if %errorlevel% equ 0 (
            echo Updates applied! Restarting...
            call .venv\Scripts\activate.bat
            pip install -r requirements.txt --quiet
        ) else (
            echo [WARNING] Update failed. Starting with current version.
        )
    )
) else (
    echo You are up to date.
)
echo.

REM Start app
echo Starting ZeroLive on http://127.0.0.1:9090
start "" http://127.0.0.1:9090
set PORT=9090
python app.py

REM If app exits, hold window open on error
if %errorlevel% neq 0 (
    echo.
    echo App exited with error code %errorlevel%.
    pause
)
