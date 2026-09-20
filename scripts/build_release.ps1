param(
    [Parameter(Mandatory = $true)]
    [string]$Version
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path
Set-Location $Root

$LocalPython = Join-Path $Root ".venv\Scripts\python.exe"
$Python = if (Test-Path $LocalPython) { $LocalPython } else { (Get-Command python -ErrorAction Stop).Source }
$DeclaredVersion = (& $Python -c "from livecatch_core import __version__; print(__version__)" ).Trim()
if ($DeclaredVersion -ne $Version) {
    throw "Version mismatch: livecatch_core is $DeclaredVersion but the release requested $Version."
}

& $Python -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "Installing Python dependencies failed." }
& $Python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed." }

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "install_tools.ps1") -NoPause
if ($LASTEXITCODE -ne 0) { throw "Runtime tool installation failed." }

if (Test-Path (Join-Path $Root "dist")) {
    Remove-Item (Join-Path $Root "dist") -Recurse -Force
}

& $Python -m PyInstaller --noconfirm --clean --onefile --console --name LiveCatchWorker `
    --collect-all yt_dlp --collect-all yt_dlp_ejs livecatch_worker.py
if ($LASTEXITCODE -ne 0) { throw "LiveCatchWorker build failed." }
& $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name LiveCatch `
    --collect-all yt_dlp --collect-all yt_dlp_ejs --collect-all pystray --hidden-import pystray._win32 --collect-all PIL livecatch.py
if ($LASTEXITCODE -ne 0) { throw "LiveCatch GUI build failed." }

$DistTools = Join-Path $Root "dist\tools"
New-Item -ItemType Directory -Force -Path $DistTools | Out-Null
foreach ($Tool in @("ffmpeg.exe", "ffprobe.exe", "deno.exe")) {
    $Source = Join-Path $Root "tools\$Tool"
    if (-not (Test-Path $Source)) {
        throw "Required release tool is missing: $Source"
    }
    Copy-Item $Source -Destination (Join-Path $DistTools $Tool) -Force
}

$Iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
if ($Iscc) {
    $IsccPath = $Iscc.Source
} else {
    $Candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    )
    $IsccPath = $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $IsccPath) {
    throw "Inno Setup 6 (ISCC.exe) was not found. Install it before building a release."
}

& $IsccPath "/DAppVersion=$Version" (Join-Path $Root "installer\LiveCatch.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed." }

Write-Host "Release installer created under dist\installer."
