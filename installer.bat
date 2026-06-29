@echo off
cd /d "%~dp0"
title ZeroLive Installer
setlocal enabledelayedexpansion

echo ============================================
echo   ZeroLive - Local Stream Player Installer
echo ============================================
echo.

REM Destination
set INSTALL_DIR=C:\Zero_live

REM Check if already installed
if /I "%~dp0"=="%INSTALL_DIR%\" goto :already_there

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

echo [1/5] Python found:
python --version
echo.

REM Copy files to C:\Zero_live
echo [2/5] Copying files to %INSTALL_DIR%...
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
xcopy /E /Y /Q "%~dp0." "%INSTALL_DIR%\" >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Failed to copy files. Try running as Administrator.
    pause
    exit /b 1
)
echo   Files copied successfully.
echo.

:already_there
cd /d "%INSTALL_DIR%"

REM Create virtual environment
echo [3/5] Creating virtual environment...
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
echo [4/5] Installing dependencies...
call .venv\Scripts\activate.bat
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo.

REM Create desktop shortcuts
echo [5/5] Creating desktop shortcuts...

set RUN_SHORTCUT=%USERPROFILE%\Desktop\ZeroLive.lnk
set UNINSTALL_SHORTCUT=%USERPROFILE%\Desktop\ZeroLive Uninstall.lnk

REM Run shortcut
if not exist "%RUN_SHORTCUT%" (
    powershell -Command ^
        $WS = New-Object -ComObject WScript.Shell; ^
        $SC = $WS.CreateShortcut('%RUN_SHORTCUT%'); ^
        $SC.TargetPath = '%INSTALL_DIR%\run.bat'; ^
        $SC.WorkingDirectory = '%INSTALL_DIR%'; ^
        $SC.Description = 'ZeroLive - Free Sports Streaming'; ^
        $SC.Save()
    if !errorlevel! equ 0 ( echo   Run shortcut created on desktop. ) else ( echo   [WARN] Could not create run shortcut. )
) else (
    echo   Run shortcut already exists.
)

REM Uninstall shortcut
if not exist "%UNINSTALL_SHORTCUT%" (
    powershell -Command ^
        $WS = New-Object -ComObject WScript.Shell; ^
        $SC = $WS.CreateShortcut('%UNINSTALL_SHORTCUT%'); ^
        $SC.TargetPath = '%INSTALL_DIR%\uninstall.bat'; ^
        $SC.WorkingDirectory = '%INSTALL_DIR%'; ^
        $SC.Description = 'Remove ZeroLive'; ^
        $SC.Save()
    if !errorlevel! equ 0 ( echo   Uninstall shortcut created on desktop. ) else ( echo   [WARN] Could not create uninstall shortcut. )
) else (
    echo   Uninstall shortcut already exists.
)
echo.

echo ============================================
echo   Installation complete!
echo.
echo   Double-click "ZeroLive" on your desktop to start.
echo   "ZeroLive Uninstall" to remove.
echo ============================================
echo.
pause
