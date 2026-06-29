@echo off
cd /d "%~dp0"
title ZeroLive Installer
echo ============================================
echo   ZeroLive - Local Stream Player Installer
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo.
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo [1/3] Python found: 
python --version
echo.

REM Create virtual environment
echo [2/3] Creating virtual environment...
if not exist ".venv" (
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo   Virtual environment created.
) else (
    echo   Virtual environment already exists.
)
echo.

REM Install requirements
echo [3/3] Installing dependencies...
call .venv\Scripts\activate.bat
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo.

REM Create desktop shortcut
echo [4/4] Creating desktop shortcut...
set SHORTCUT_PATH=%USERPROFILE%\Desktop\ZeroLive.lnk
if not exist "%SHORTCUT_PATH%" (
    powershell -Command ^
        $WS = New-Object -ComObject WScript.Shell; ^
        $SC = $WS.CreateShortcut('%SHORTCUT_PATH%'); ^
        $SC.TargetPath = '%~dp0run.bat'; ^
        $SC.WorkingDirectory = '%~dp0'; ^
        $SC.Description = 'ZeroLive - Free Sports Streaming'; ^
        $SC.Save()
    if %errorlevel% equ 0 (
        echo   Shortcut created on your desktop.
    ) else (
        echo   [WARN] Could not create shortcut.
    )
) else (
    echo   Shortcut already exists on desktop.
)
echo.

echo ============================================
echo   Installation complete!
echo.
echo   Double-click the "ZeroLive" shortcut on your desktop to start.
echo ============================================
echo.
pause
