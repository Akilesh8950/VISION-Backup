[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not $SkipInstall) {
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -r requirements.txt
}

$Commit = "dev"
if (Get-Command git -ErrorAction SilentlyContinue) {
    $value = & git rev-parse --short HEAD 2>$null
    if ($LASTEXITCODE -eq 0 -and $value) { $Commit = $value.Trim() }
}

$BuildInfo = Join-Path $Root "src\vision_app\_build_info.py"
"BUILD_COMMIT = `"$Commit`"" | Set-Content -Encoding UTF8 $BuildInfo

try {
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onedir `
        --windowed `
        --name VISION `
        --distpath dist `
        --workpath build\pyinstaller `
        --specpath build `
        --icon assets\vision.ico `
        --version-file assets\version_info.txt `
        --paths src `
        --collect-submodules vision_app `
        run_vision.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
}
finally {
    if (Test-Path $BuildInfo) { Remove-Item $BuildInfo -Force }
}

$DistDir = Join-Path $Root "dist\VISION"
$Exe = Join-Path $DistDir "VISION.exe"
if (-not (Test-Path $Exe)) { throw "Expected executable was not produced: $Exe" }

$BundledLlama = Join-Path $Root "third_party\llama.cpp"
if (Test-Path (Join-Path $BundledLlama "llama-server.exe")) {
    $Target = Join-Path $DistDir "llama.cpp"
    New-Item -ItemType Directory -Force $Target | Out-Null
    Copy-Item (Join-Path $BundledLlama "*") $Target -Recurse -Force
    Write-Host "Bundled llama.cpp copied to $Target"
} else {
    Write-Warning "third_party\llama.cpp\llama-server.exe not found; the app will use Settings/PATH or wizard download."
}

Write-Host "Built $Exe"
