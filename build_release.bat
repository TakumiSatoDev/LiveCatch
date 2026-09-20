@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
    echo Usage: build_release.bat 3.2.1
    exit /b 2
)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_release.ps1 -Version "%~1"
if errorlevel 1 exit /b 1
echo Release installer is in dist\installer.
endlocal
