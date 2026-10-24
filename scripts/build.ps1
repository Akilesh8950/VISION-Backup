[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$SkipInstall,
    [switch]$BuildFrontend
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not $SkipInstall) {
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -r requirements.txt
}

if ($BuildFrontend) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "-BuildFrontend requires Node/npm. Install Node 20+ or omit the switch to package the committed app\web build."
    }
    Push-Location (Join-Path $Root "app\frontend")
    try {
        & npm ci --legacy-peer-deps
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed with exit code $LASTEXITCODE" }
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw "React frontend build failed with exit code $LASTEXITCODE" }
    }
    finally {
        Pop-Location
    }
}

$WebDir = Join-Path $Root "app\web"
$WebIndex = Join-Path $WebDir "index.html"
if (-not (Test-Path $WebIndex)) {
    throw "React frontend build is missing ($WebIndex). Run: cd app\frontend; npm ci --legacy-peer-deps; npm run build"
}
$WebIndexText = Get-Content $WebIndex -Raw
$AssetRefs = @([regex]::Matches($WebIndexText, '(?:src|href)="\./(assets/[^"]+)"') | ForEach-Object { $_.Groups[1].Value })
if ($AssetRefs.Count -lt 2) {
    throw "React frontend index does not reference its JS/CSS assets. Rebuild app\frontend before packaging."
}
foreach ($assetRef in $AssetRefs) {
    $assetPath = Join-Path $WebDir ($assetRef -replace '/', '\')
    if (-not (Test-Path $assetPath)) {
        throw "React frontend asset referenced by app\web\index.html is missing: $assetPath"
    }
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
        --collect-submodules webview `
        --add-data "app\web;app/web" `
        run_vision.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
}
finally {
    if (Test-Path $BuildInfo) { Remove-Item $BuildInfo -Force }
}

$DistDir = Join-Path $Root "dist\VISION"
$Exe = Join-Path $DistDir "VISION.exe"
if (-not (Test-Path $Exe)) { throw "Expected executable was not produced: $Exe" }

$WebIndexCandidates = @(
    (Join-Path $DistDir "_internal\app\web\index.html"),
    (Join-Path $DistDir "app\web\index.html")
)
$BundledWebIndex = $WebIndexCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $BundledWebIndex) {
    throw "Expected web frontend was not bundled under dist\VISION (app\web\index.html missing)"
}
$BundledWebDir = Split-Path -Parent $BundledWebIndex
foreach ($assetRef in $AssetRefs) {
    $bundledAsset = Join-Path $BundledWebDir ($assetRef -replace '/', '\')
    if (-not (Test-Path $bundledAsset)) {
        throw "Bundled React frontend is missing an asset referenced by index.html: $bundledAsset"
    }
}
foreach ($extra in @("screenshots.html", "licenses\react.txt", "licenses\chartjs.txt")) {
    if (-not (Test-Path (Join-Path $BundledWebDir $extra))) {
        throw "Bundled React frontend is missing expected file: $extra"
    }
}

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
