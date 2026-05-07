@echo off
setlocal
cd /d "%~dp0"

echo ================================
echo LiveCatch v1.1.1 Build Script
echo ================================
echo.

if not exist "%~dp0tools\yt-dlp.exe" (
    echo tools\yt-dlp.exe was not found.
    echo Running install_tools.ps1 first...
    echo.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_tools.ps1"

    if not exist "%~dp0tools\yt-dlp.exe" (
        echo.
        echo ERROR: yt-dlp.exe was still not found after running install_tools.ps1.
        echo Build stopped.
        pause
        exit /b 1
    )
)

if not exist "%~dp0tools\ffmpeg.exe" (
    echo tools\ffmpeg.exe was not found.
    echo Running install_tools.ps1 first...
    echo.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_tools.ps1"

    if not exist "%~dp0tools\ffmpeg.exe" (
        echo.
        echo ERROR: ffmpeg.exe was still not found after running install_tools.ps1.
        echo Build stopped.
        pause
        exit /b 1
    )
)

echo.
echo Installing PyInstaller...
python -m pip install --upgrade pip
python -m pip install pyinstaller

echo.
echo Building LiveCatch.exe...
pyinstaller --clean --onefile --windowed --name LiveCatch livecatch.py

echo.
echo Copying tools to dist\tools...

if not exist "%~dp0dist\tools" mkdir "%~dp0dist\tools"

copy /Y "%~dp0tools\yt-dlp.exe" "%~dp0dist\tools\yt-dlp.exe"
copy /Y "%~dp0tools\ffmpeg.exe" "%~dp0dist\tools\ffmpeg.exe"

if exist "%~dp0tools\ffprobe.exe" (
    copy /Y "%~dp0tools\ffprobe.exe" "%~dp0dist\tools\ffprobe.exe"
)

echo.
echo ================================
echo Build complete.
echo ================================
echo EXE:
echo   %~dp0dist\LiveCatch.exe
echo.
echo Tools:
echo   %~dp0dist\tools
echo.
pause
endlocal
