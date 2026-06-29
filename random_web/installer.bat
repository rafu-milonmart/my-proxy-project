@echo off
cd /d "%~dp0"
title ZeroLive Installer
setlocal enabledelayedexpansion

echo ============================================
echo   ZeroLive - Local Stream Player Installer
echo ============================================
echo.

set INSTALL_DIR=C:\Zero_live
set PYTHON_DIR=%INSTALL_DIR%\python
set PYTHON_VERSION=3.12.5

REM Check if already installed
if /I "%~dp0"=="%INSTALL_DIR%\" goto :already_there

REM Step 1: Download portable Python
echo [1/6] Setting up portable Python...
if not exist "%PYTHON_DIR%\python.exe" (
    echo   Downloading Python %PYTHON_VERSION% (embedded)...
    if not exist "%TEMP%\python-%PYTHON_VERSION%-embed-amd64.zip" (
        powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/%PYTHON_VERSION%/python-%PYTHON_VERSION%-embed-amd64.zip' -OutFile '%TEMP%\python-%PYTHON_VERSION%-embed-amd64.zip'}"
        if !errorlevel! neq 0 (
            echo [ERROR] Failed to download Python. Check your internet.
            pause
            exit /b 1
        )
    )
    echo   Extracting...
    if not exist "%PYTHON_DIR%" mkdir "%PYTHON_DIR%"
    powershell -Command "Expand-Archive -Path '%TEMP%\python-%PYTHON_VERSION%-embed-amd64.zip' -DestinationPath '%PYTHON_DIR%' -Force"
    if !errorlevel! neq 0 (
        echo [ERROR] Failed to extract Python.
        pause
        exit /b 1
    )
    REM Enable site-packages in embedded Python
    set "PTH_FILE=%PYTHON_DIR%\python._pth"
    if exist "!PTH_FILE!" (
        powershell -Command "(Get-Content '!PTH_FILE!') -replace '#import site','import site' | Set-Content '!PTH_FILE!'"
    )
    REM Download and install pip
    echo   Installing pip...
    powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%TEMP%\get-pip.py'}"
    %PYTHON_DIR%\python.exe "%TEMP%\get-pip.py" --quiet >nul 2>&1
    if !errorlevel! neq 0 ( echo   [WARN] pip install had issues. ) else ( echo   pip installed. )
    echo   Portable Python ready.
) else (
    echo   Portable Python already exists.
)
echo.

REM Show Python version
%PYTHON_DIR%\python.exe --version
echo.

REM Step 2: Copy app files to C:\Zero_live
echo [2/6] Copying app files to %INSTALL_DIR%...
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

REM Use portable Python
set PYTHON=%INSTALL_DIR%\python\python.exe

REM Step 3: Create virtual environment
echo [3/6] Creating virtual environment...
if not exist ".venv" (
    %PYTHON% -m venv .venv
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

REM Step 6: Firewall rule
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
