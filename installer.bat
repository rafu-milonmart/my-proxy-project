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

REM Always download fresh from GitHub (installer runs from Downloads folder)
set GITHUB_REPO=https://github.com/rafu-milonmart/my-proxy-project
set ZIP_URL=%GITHUB_REPO%/archive/master.zip

REM Step 1: Download portable Python
echo [1/5] Setting up portable Python...
if not exist "%PYTHON_DIR%\python.exe" (
    echo   Downloading Python %PYTHON_VERSION% embedded version...
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
    REM Enable site-packages in embedded Python (required for pip)
    set "PTH_FILE=%PYTHON_DIR%\python._pth"
    if exist "!PTH_FILE!" (
        powershell -Command "(Get-Content '!PTH_FILE!') -replace '#import site','import site' | Set-Content '!PTH_FILE!'"
    )
    REM Install pip via ensurepip (bundled in python3.zip, no download needed)
    echo   Installing pip...
    %PYTHON_DIR%\python.exe -m ensurepip --upgrade
    if !errorlevel! neq 0 (
        echo   [WARN] ensurepip had issues. Trying get-pip.py fallback...
        powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%TEMP%\get-pip.py'}"
        %PYTHON_DIR%\python.exe "%TEMP%\get-pip.py"
        if !errorlevel! neq 0 (
            echo [ERROR] Could not install pip. Python may be corrupt.
            pause
            exit /b 1
        )
    )
    %PYTHON_DIR%\python.exe -m pip --version
    echo   pip ready.
    echo   Portable Python ready.
) else (
    echo   Portable Python already exists.
)
echo.

REM Show Python version
%PYTHON_DIR%\python.exe --version
echo.

REM Step 2: Download app files from GitHub
echo [2/5] Downloading app files from GitHub...
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://github.com/rafu-milonmart/my-proxy-project/archive/master.zip' -OutFile '%TEMP%\zero_live.zip'}"
if !errorlevel! neq 0 (
    echo [ERROR] Failed to download app files. Check your internet.
    pause
    exit /b 1
)
echo   Extracting...
powershell -Command "Expand-Archive -Path '%TEMP%\zero_live.zip' -DestinationPath '%TEMP%\zero_live_extracted' -Force"
if exist "%TEMP%\zero_live_extracted\my-proxy-project-master" (
    xcopy /E /Y /Q "%TEMP%\zero_live_extracted\my-proxy-project-master\." "%INSTALL_DIR%\" >nul 2>&1
) else (
    xcopy /E /Y /Q "%TEMP%\zero_live_extracted\." "%INSTALL_DIR%\" >nul 2>&1
)
echo   App files downloaded.
REM Cleanup temp files
del "%TEMP%\zero_live.zip" >nul 2>&1
rmdir /S /Q "%TEMP%\zero_live_extracted" >nul 2>&1
echo.

cd /d "%INSTALL_DIR%"

REM Use portable Python
set PYTHON=%INSTALL_DIR%\python\python.exe

REM Step 3: Install dependencies directly (embedded Python is already isolated)
echo [3/5] Installing dependencies...
if not exist "requirements.txt" (
    echo [ERROR] requirements.txt not found! GitHub download may have failed.
    dir "%INSTALL_DIR%"
    pause
    exit /b 1
)
%PYTHON% -m pip install -r requirements.txt
if !errorlevel! neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo   Dependencies installed.
echo.

REM Step 4: Create desktop shortcuts
echo [4/5] Creating desktop shortcuts...

set RUN_SHORTCUT=%USERPROFILE%\Desktop\ZeroLive.lnk
set UNINSTALL_SHORTCUT=%USERPROFILE%\Desktop\ZeroLive Uninstall.lnk

if not exist "%RUN_SHORTCUT%" (
    set VBS=%TEMP%\zl_run.vbs
    >"%VBS%" echo Set WshShell = WScript.CreateObject("WScript.Shell")
    >>"%VBS%" echo Set Shortcut = WshShell.CreateShortcut("%RUN_SHORTCUT%")
    >>"%VBS%" echo Shortcut.TargetPath = "%INSTALL_DIR%\run.bat"
    >>"%VBS%" echo Shortcut.WorkingDirectory = "%INSTALL_DIR%"
    >>"%VBS%" echo Shortcut.Description = "ZeroLive - Free Sports Streaming"
    >>"%VBS%" echo Shortcut.Save
    cscript //nologo "%VBS%"
    if !errorlevel! equ 0 ( echo   "ZeroLive" shortcut created on desktop. ) else ( echo   [WARN] Could not create run shortcut. )
    del "%VBS%" >nul 2>&1
) else (
    echo   Run shortcut already exists.
)

if not exist "%UNINSTALL_SHORTCUT%" (
    set VBS=%TEMP%\zl_uninst.vbs
    >"%VBS%" echo Set WshShell = WScript.CreateObject("WScript.Shell")
    >>"%VBS%" echo Set Shortcut = WshShell.CreateShortcut("%UNINSTALL_SHORTCUT%")
    >>"%VBS%" echo Shortcut.TargetPath = "%INSTALL_DIR%\uninstall.bat"
    >>"%VBS%" echo Shortcut.WorkingDirectory = "%INSTALL_DIR%"
    >>"%VBS%" echo Shortcut.Description = "Remove ZeroLive"
    >>"%VBS%" echo Shortcut.Save
    cscript //nologo "%VBS%"
    if !errorlevel! equ 0 ( echo   "ZeroLive Uninstall" shortcut created on desktop. ) else ( echo   [WARN] Could not create uninstall shortcut. )
    del "%VBS%" >nul 2>&1
) else (
    echo   Uninstall shortcut already exists.
)
echo.

REM Step 5: Firewall rule + README
echo [5/5] Configuring firewall and README...
netsh advfirewall firewall add rule name="ZeroLive" dir=in action=allow program="%INSTALL_DIR%\python\python.exe" profile=private enable=yes >nul 2>&1
if !errorlevel! equ 0 ( echo   Firewall rule added. ) else ( echo   [SKIP] Could not add firewall rule (may need admin). )
echo.

REM README
set README_FILE=%INSTALL_DIR%\readme.txt
(
echo =============================================
echo   ZeroLive - Free Sports Streaming
echo =============================================
echo.
echo HOW TO USE:
echo 1. Double-click "ZeroLive" on your desktop
echo 2. Your browser opens to http://127.0.0.1:9090
echo 3. Select a live match and enjoy!
echo.
echo CONTROLS:
echo   Space  - Play/Pause
echo   F      - Fullscreen
echo   M      - Mute/Unmute
echo   I      - Stream info overlay
echo   S      - Speed menu
echo   Arrows - Seek / Volume
echo.
echo TROUBLESHOOTING:
echo - If the app doesn't start, run run.bat manually
echo - Firewall alert is normal - allow access
echo - For VLC: use the M3U link from the app
echo.
echo UNINSTALL:
echo Double-click "ZeroLive Uninstall" on your desktop
echo.
echo Version: 1.0
echo =============================================
) > "%README_FILE%"
echo   README created.

echo ============================================
echo   Installation complete!
echo.
echo   - Double-click "ZeroLive" on your desktop to start
echo   - "ZeroLive Uninstall" to remove
echo   - App runs at http://127.0.0.1:9090
echo ============================================
echo.
start notepad "%README_FILE%"
echo.
pause
