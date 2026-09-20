@echo off
setlocal
cd /d "%~dp0"
echo LiveCatch v3 development build. Use the same Python environment as run_app.bat.
python -m pip install -r requirements-dev.txt
if errorlevel 1 exit /b 1
python -m pytest -q
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm --clean --onefile --console --name LiveCatchWorker --collect-all yt_dlp --collect-all yt_dlp_ejs livecatch_worker.py
if errorlevel 1 exit /b 1
python -m PyInstaller --noconfirm --clean --onefile --windowed --name LiveCatch --collect-all yt_dlp --collect-all yt_dlp_ejs --collect-all pystray --hidden-import pystray._win32 --collect-all PIL livecatch.py
if errorlevel 1 exit /b 1
start /wait "" dist\LiveCatch.exe --ui-encoding-smoke
if errorlevel 1 (
    echo Packaged LiveCatch UI encoding smoke test failed.
    exit /b 1
)
if not exist dist\tools mkdir dist\tools
for %%F in (ffmpeg.exe ffprobe.exe deno.exe) do (
    if exist tools\%%F copy /Y tools\%%F dist\tools\%%F >nul
)
echo Keep LiveCatch.exe, LiveCatchWorker.exe and tools together.
echo ffmpeg AND ffprobe are required. Deno is recommended for YouTube.
echo CUDA requires a suitable FFmpeg build and NVIDIA driver; run Tools / GPU check.
echo Build is not a GPU or real-stream validation. See docs/VALIDATION.md.
endlocal
