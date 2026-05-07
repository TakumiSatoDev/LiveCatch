$ErrorActionPreference = "Stop"

Write-Host "Installing local yt-dlp and ffmpeg into ./tools ..."
Write-Host "This installer does NOT require winget."

$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ToolsDir = Join-Path $BaseDir "tools"
$TempDir = Join-Path $BaseDir "_tmp_install"

New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$YtDlpUrl = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
$YtDlpPath = Join-Path $ToolsDir "yt-dlp.exe"

if (Test-Path $YtDlpPath) {
    Write-Host ""
    Write-Host "yt-dlp already exists. Skipping download:"
    Write-Host "  $YtDlpPath"
} else {
    Write-Host ""
    Write-Host "Downloading yt-dlp..."
    Invoke-WebRequest -Uri $YtDlpUrl -OutFile $YtDlpPath
}

$FfmpegPath = Join-Path $ToolsDir "ffmpeg.exe"
$FfprobePath = Join-Path $ToolsDir "ffprobe.exe"

if (Test-Path $FfmpegPath) {
    Write-Host ""
    Write-Host "ffmpeg already exists. Skipping download:"
    Write-Host "  $FfmpegPath"
} else {
    $FfmpegZipUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    $FfmpegZipPath = Join-Path $TempDir "ffmpeg.zip"
    $FfmpegExtractDir = Join-Path $TempDir "ffmpeg"

    Write-Host ""
    Write-Host "Downloading ffmpeg..."
    Invoke-WebRequest -Uri $FfmpegZipUrl -OutFile $FfmpegZipPath

    Write-Host "Extracting ffmpeg..."
    if (Test-Path $FfmpegExtractDir) {
        Remove-Item $FfmpegExtractDir -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $FfmpegExtractDir | Out-Null
    Expand-Archive -Path $FfmpegZipPath -DestinationPath $FfmpegExtractDir -Force

    $FfmpegExe = Get-ChildItem -Path $FfmpegExtractDir -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    $FfprobeExe = Get-ChildItem -Path $FfmpegExtractDir -Recurse -Filter "ffprobe.exe" | Select-Object -First 1

    if (-not $FfmpegExe) {
        throw "ffmpeg.exe was not found in the downloaded archive."
    }

    Copy-Item $FfmpegExe.FullName -Destination $FfmpegPath -Force

    if ($FfprobeExe) {
        Copy-Item $FfprobeExe.FullName -Destination $FfprobePath -Force
    }
}

Write-Host ""
Write-Host "Cleaning temporary files..."
if (Test-Path $TempDir) {
    Remove-Item $TempDir -Recurse -Force
}

Write-Host ""
Write-Host "Checking dist folder..."

$DistDir = Join-Path $BaseDir "dist"
$DistToolsDir = Join-Path $DistDir "tools"

if (Test-Path $DistDir) {
    Write-Host "dist folder found. Copying tools to dist/tools..."

    New-Item -ItemType Directory -Force -Path $DistToolsDir | Out-Null

    if (Test-Path $YtDlpPath) {
        Copy-Item $YtDlpPath -Destination (Join-Path $DistToolsDir "yt-dlp.exe") -Force
    }
    if (Test-Path $FfmpegPath) {
        Copy-Item $FfmpegPath -Destination (Join-Path $DistToolsDir "ffmpeg.exe") -Force
    }
    if (Test-Path $FfprobePath) {
        Copy-Item $FfprobePath -Destination (Join-Path $DistToolsDir "ffprobe.exe") -Force
    }

    Write-Host "Copied tools to:"
    Write-Host "  $DistToolsDir"
} else {
    Write-Host "dist folder not found. Tools were installed only to:"
    Write-Host "  $ToolsDir"
    Write-Host "After building exe, build_exe.bat will copy tools into dist/tools."
}

Write-Host ""
Write-Host "Done."
Write-Host "Installed tools:"
Write-Host "  $YtDlpPath"
Write-Host "  $FfmpegPath"
Write-Host ""
Write-Host "You can now run LiveCatch.exe or run_app.bat."
pause
