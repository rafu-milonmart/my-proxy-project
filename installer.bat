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

REM Step 1: Install Python
echo [1/5] Setting up Python...
if exist "%PYTHON_DIR%\python.exe" goto :python_done

echo   Downloading Python %PYTHON_VERSION%...
set PYTHON_ZIP=%TEMP%\python-%PYTHON_VERSION%-embed-amd64.zip
if not exist "!PYTHON_ZIP!" (
    powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/%PYTHON_VERSION%/python-%PYTHON_VERSION%-embed-amd64.zip' -OutFile '!PYTHON_ZIP!'}"
    if !errorlevel! neq 0 (
        echo [ERROR] Failed to download Python. Check your internet.
        pause
        exit /b 1
    )
)
echo   Extracting...
if not exist "%PYTHON_DIR%" mkdir "%PYTHON_DIR%"
powershell -Command "Expand-Archive -Path '!PYTHON_ZIP!' -DestinationPath '%PYTHON_DIR%' -Force"
if !errorlevel! neq 0 (
    echo [ERROR] Failed to extract Python.
    pause
    exit /b 1
)

REM Enable site-packages in embedded Python (required for pip)
for %%f in ("%PYTHON_DIR%\python*._pth") do (
    powershell -Command "(Get-Content '%%f') -replace '#import site','import site' | Set-Content '%%f'"
)

REM Download and install pip via get-pip.py
echo   Installing pip...
set GET_PIP=%TEMP%\get-pip.py
powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '!GET_PIP!'}"
if !errorlevel! equ 0 (
    "%PYTHON_DIR%\python.exe" "!GET_PIP!" --no-setuptools --no-wheel
    if !errorlevel! neq 0 (
        echo   [WARN] get-pip.py had issues. Trying --trusted-host...
        "%PYTHON_DIR%\python.exe" "!GET_PIP!" --no-setuptools --no-wheel --trusted-host pypi.org --trusted-host files.pythonhosted.org
    )
)
REM Try pip via -m, fallback to direct path
"%PYTHON_DIR%\python.exe" -m pip --version >nul 2>&1
if !errorlevel! neq 0 (
    echo   [WARN] python -m pip not working, trying direct pip path...
    if exist "%PYTHON_DIR%\Scripts\pip.exe" (
        echo   Found pip at Scripts\pip.exe
    ) else (
        echo [ERROR] pip.exe not found in Scripts directory.
        dir "%PYTHON_DIR%"
        dir "%PYTHON_DIR%\Scripts" 2>nul
        pause
        exit /b 1
    )
)
echo   Python + pip ready.
:python_done
echo.

REM Show Python version
%PYTHON_DIR%\python.exe --version
%PYTHON_DIR%\python.exe -m pip --version
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
REM Write version file for update check
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'https://api.github.com/repos/rafu-milonmart/my-proxy-project/commits/master' -UseBasicParsing | ConvertFrom-Json; Write-Output $r.sha } catch { Write-Output '0' }" > "%INSTALL_DIR%\version.txt"
echo.
REM Cleanup temp files
del "%TEMP%\zero_live.zip" >nul 2>&1
rmdir /S /Q "%TEMP%\zero_live_extracted" >nul 2>&1
echo.

cd /d "%INSTALL_DIR%"

REM Use portable Python
set PYTHON=%INSTALL_DIR%\python\python.exe

REM Step 3: Install dependencies
echo [3/5] Installing dependencies...
if not exist "requirements.txt" (
    echo [ERROR] requirements.txt not found! GitHub download may have failed.
    dir "%INSTALL_DIR%"
    pause
    exit /b 1
)
"%PYTHON_DIR%\Scripts\pip.exe" install -r requirements.txt
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
    powershell -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%RUN_SHORTCUT%');$s.TargetPath='%INSTALL_DIR%\Zero_live.bat';$s.WorkingDirectory='%INSTALL_DIR%';$s.Description='ZeroLive - Free Sports Streaming';$s.Save()"
    if !errorlevel! equ 0 ( echo   "ZeroLive" shortcut created on desktop. ) else ( echo   [WARN] Could not create run shortcut. )
) else (
    echo   Run shortcut already exists.
)

if not exist "%UNINSTALL_SHORTCUT%" (
    powershell -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%UNINSTALL_SHORTCUT%');$s.TargetPath='%INSTALL_DIR%\uninstall.bat';$s.WorkingDirectory='%INSTALL_DIR%';$s.Description='Remove ZeroLive';$s.Save()"
    if !errorlevel! equ 0 ( echo   "ZeroLive Uninstall" shortcut created on desktop. ) else ( echo   [WARN] Could not create uninstall shortcut. )
) else (
    echo   Uninstall shortcut already exists.
)
echo.

REM Step 5: Firewall rule + README
echo [5/5] Configuring firewall and README...
netsh advfirewall firewall add rule name="ZeroLive" dir=in action=allow program="%INSTALL_DIR%\python\python.exe" profile=private enable=yes >nul 2>&1
if !errorlevel! equ 0 ( echo   Firewall rule added. ) else ( echo   [SKIP] Could not add firewall rule - may need admin. )
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
echo - If the app doesn't start, run Zero_live.bat manually
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
