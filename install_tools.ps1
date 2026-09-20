param(
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"

Write-Host "Installing local yt-dlp and ffmpeg into ./tools ..."
Write-Host "This installer does NOT require winget."

$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ToolsDir = Join-Path $BaseDir "tools"
$TempDir = Join-Path $BaseDir "_tmp_install"

New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Download-FileWithRetry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    $temporary = "$Destination.download"
    if (Test-Path $temporary) {
        Remove-Item $temporary -Force
    }

    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & $curl.Source --location --fail --show-error --retry 5 --retry-delay 5 `
            --retry-all-errors --connect-timeout 30 --max-time 900 --output $temporary $Uri
        if ($LASTEXITCODE -ne 0) {
            throw "Download failed with curl (exit code $LASTEXITCODE): $Uri"
        }
    } else {
        $downloaded = $false
        for ($attempt = 1; $attempt -le 5; $attempt++) {
            try {
                Invoke-WebRequest -Uri $Uri -OutFile $temporary -TimeoutSec 900
                $downloaded = $true
                break
            } catch {
                if ($attempt -eq 5) {
                    throw
                }
                Write-Warning "Download attempt $attempt failed; retrying: $($_.Exception.Message)"
                Start-Sleep -Seconds ([Math]::Min(30, $attempt * 5))
            }
        }
        if (-not $downloaded) {
            throw "Download failed: $Uri"
        }
    }

    if (-not (Test-Path $temporary) -or (Get-Item $temporary).Length -eq 0) {
        throw "Downloaded file is empty: $Uri"
    }
    Move-Item $temporary $Destination -Force
}

$YtDlpUrl = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
$YtDlpPath = Join-Path $ToolsDir "yt-dlp.exe"

if (Test-Path $YtDlpPath) {
    Write-Host ""
    Write-Host "yt-dlp already exists. Skipping download:"
    Write-Host "  $YtDlpPath"
} else {
    Write-Host ""
    Write-Host "Downloading yt-dlp..."
    Download-FileWithRetry -Uri $YtDlpUrl -Destination $YtDlpPath
}

$FfmpegPath = Join-Path $ToolsDir "ffmpeg.exe"
$FfprobePath = Join-Path $ToolsDir "ffprobe.exe"
$DenoPath = Join-Path $ToolsDir "deno.exe"

$NeedsFfmpegInstall = -not (Test-Path $FfmpegPath) -or -not (Test-Path $FfprobePath)

if (-not $NeedsFfmpegInstall) {
    Write-Host ""
    Write-Host "ffmpeg and ffprobe already exist. Skipping download:"
    Write-Host "  $FfmpegPath"
    Write-Host "  $FfprobePath"
} else {
    $FfmpegZipUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    $FfmpegZipPath = Join-Path $TempDir "ffmpeg.zip"
    $FfmpegExtractDir = Join-Path $TempDir "ffmpeg"

    New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

    Write-Host ""
    Write-Host "Downloading ffmpeg..."
    Download-FileWithRetry -Uri $FfmpegZipUrl -Destination $FfmpegZipPath

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

if (Test-Path $DenoPath) {
    Write-Host ""
    Write-Host "deno already exists. Skipping download:"
    Write-Host "  $DenoPath"
} else {
    $DenoZipUrl = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
    $DenoZipPath = Join-Path $TempDir "deno.zip"
    $DenoExtractDir = Join-Path $TempDir "deno"

    New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
    Write-Host ""
    Write-Host "Downloading deno..."
    Download-FileWithRetry -Uri $DenoZipUrl -Destination $DenoZipPath
    Expand-Archive -Path $DenoZipPath -DestinationPath $DenoExtractDir -Force
    $DenoExe = Get-ChildItem -Path $DenoExtractDir -Recurse -Filter "deno.exe" | Select-Object -First 1
    if (-not $DenoExe) {
        throw "deno.exe was not found in the downloaded archive."
    }
    Copy-Item $DenoExe.FullName -Destination $DenoPath -Force
}

if (Test-Path $TempDir) {
    Write-Host ""
    Write-Host "Cleaning temporary files..."
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
    if (Test-Path $DenoPath) {
        Copy-Item $DenoPath -Destination (Join-Path $DistToolsDir "deno.exe") -Force
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
Write-Host "  $DenoPath"
Write-Host ""
Write-Host "You can now run LiveCatch.exe or run_app.bat."
if (-not $NoPause) {
    pause
}
