@echo off
cd /d "%~dp0"
title ZeroLive Installer
setlocal enabledelayedexpansion

echo ============================================
echo   ZeroLive - Local Stream Player Installer
echo ============================================
echo.

set INSTALL_DIR=C:\Zero_live

REM Check if already installed
if /I "%~dp0"=="%INSTALL_DIR%\" goto :already_there

REM Step 1: Check / Install Python
echo [1/6] Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   Python not found. Installing via winget...
    echo   (This requires an internet connection.)
    echo.
    winget install --id Python.Python.3.12 --silent --accept-package-agreements
    if !errorlevel! neq 0 (
        echo [ERROR] Winget install failed.
        echo.
        echo Try manually: winget install Python.Python.3.12
        echo Or download from https://www.python.org/downloads/
        pause
        exit /b 1
    )
    echo   Python installed successfully.
    REM Refresh PATH so we can find Python right away
    for /f "tokens=*" %%a in ('powershell -Command "[Environment]::GetEnvironmentVariable('Path','User')"') do set "PATH=%%a;%PATH%"
    REM Try common install locations if still not found
    python --version >nul 2>&1
    if !errorlevel! neq 0 (
        for %%p in ("%LocalAppData%\Programs\Python\Python312" "%ProgramFiles%\Python312" "%LocalAppData%\Microsoft\WindowsApps") do (
            if exist "%%~p\python.exe" set "PATH=%%~p;%PATH%"
        )
    )
) else (
    echo   Python found.
)
echo.

REM Show Python version
python --version
echo.

REM Step 2: Copy files to C:\Zero_live
echo [2/6] Copying files to %INSTALL_DIR%...
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
xcopy /E /Y /Q "%~dp0." "%INSTALL_DIR%\" >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Failed to copy files. Try running as Administrator.
    pause
    exit /b 1
)
echo   Files copied.
echo.

:already_there
cd /d "%INSTALL_DIR%"

REM Step 3: Create virtual environment
echo [3/6] Creating virtual environment...
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

REM Step 4: Install requirements
echo [4/6] Installing dependencies...
call .venv\Scripts\activate.bat
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo.

REM Step 5: Create desktop shortcuts
echo [5/6] Creating desktop shortcuts...

set RUN_SHORTCUT=%USERPROFILE%\Desktop\ZeroLive.lnk
set UNINSTALL_SHORTCUT=%USERPROFILE%\Desktop\ZeroLive Uninstall.lnk

if not exist "%RUN_SHORTCUT%" (
    powershell -Command ^
        $WS = New-Object -ComObject WScript.Shell; ^
        $SC = $WS.CreateShortcut('%RUN_SHORTCUT%'); ^
        $SC.TargetPath = '%INSTALL_DIR%\run.bat'; ^
        $SC.WorkingDirectory = '%INSTALL_DIR%'; ^
        $SC.Description = 'ZeroLive - Free Sports Streaming'; ^
        $SC.Save()
    if !errorlevel! equ 0 ( echo   "ZeroLive" shortcut created on desktop. ) else ( echo   [WARN] Could not create run shortcut. )
) else (
    echo   Run shortcut already exists.
)

if not exist "%UNINSTALL_SHORTCUT%" (
    powershell -Command ^
        $WS = New-Object -ComObject WScript.Shell; ^
        $SC = $WS.CreateShortcut('%UNINSTALL_SHORTCUT%'); ^
        $SC.TargetPath = '%INSTALL_DIR%\uninstall.bat'; ^
        $SC.WorkingDirectory = '%INSTALL_DIR%'; ^
        $SC.Description = 'Remove ZeroLive'; ^
        $SC.Save()
    if !errorlevel! equ 0 ( echo   "ZeroLive Uninstall" shortcut created on desktop. ) else ( echo   [WARN] Could not create uninstall shortcut. )
) else (
    echo   Uninstall shortcut already exists.
)
echo.

REM Step 6: Add firewall rule (optional, for local network access)
echo [6/6] Adding firewall rule for local network access...
netsh advfirewall firewall add rule name="ZeroLive" dir=in action=allow program="%INSTALL_DIR%\.venv\Scripts\python.exe" profile=private enable=yes >nul 2>&1
if !errorlevel! equ 0 ( echo   Firewall rule added. ) else ( echo   [SKIP] Could not add firewall rule (may need admin). )
echo.

echo ============================================
echo   Installation complete!
echo.
echo   - Double-click "ZeroLive" on your desktop to start
echo   - "ZeroLive Uninstall" to remove
echo   - App runs at http://127.0.0.1:9090
echo ============================================
echo.
pause
