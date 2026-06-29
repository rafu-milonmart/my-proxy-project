@echo off
title ZeroLive Uninstaller
echo ============================================
echo   ZeroLive - Uninstall
echo ============================================
echo.
echo This will remove ZeroLive and all its files.
echo.

choice /c YN /m "Are you sure?"
if errorlevel 2 exit /b

set INSTALL_DIR=C:\Zero_live

REM Remove desktop shortcuts
echo Removing desktop shortcuts...
if exist "%USERPROFILE%\Desktop\ZeroLive.lnk" (
    del "%USERPROFILE%\Desktop\ZeroLive.lnk" >nul 2>&1
)
if exist "%USERPROFILE%\Desktop\ZeroLive Uninstall.lnk" (
    del "%USERPROFILE%\Desktop\ZeroLive Uninstall.lnk" >nul 2>&1
)

REM Remove install directory
echo Removing %INSTALL_DIR%...
if exist "%INSTALL_DIR%" (
    rmdir /S /Q "%INSTALL_DIR%" >nul 2>&1
)

echo.
echo ZeroLive has been uninstalled.
echo.
pause
