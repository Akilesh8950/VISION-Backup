[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$Version = "",
    [ValidateSet("stable", "dev")]
    [string]$Channel = "stable",
    [string]$Notes = "See the GitHub release for details.",
    [string]$NotesPath = "",
    [string]$MinLauncher = "1.0.0",
    [switch]$BuildFrontend,
    [switch]$SkipInstall,
    [switch]$SkipBuild,
    [switch]$SkipLauncher,
    [switch]$Publish,
    [string]$SigningKey = "",
    [string]$SigningKeyId = "",
    [switch]$SignConfig
)

# Release ritual for the three-channel update system.
#
# The desktop payload below is produced by scripts\build.ps1, which invokes
# PyInstaller with --onedir.  Do not change the app payload to --onefile: the
# launcher stages and launches versions\<version>\VISION.exe from an onedir
# tree, and onefile self-extraction fights that atomic staged layout.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not $Version) {
    $InitText = Get-Content (Join-Path $Root "src\vision_app\__init__.py") -Raw
    if ($InitText -notmatch '__version__\s*=\s*"([^"]+)"') {
        throw "Could not read __version__ from src\vision_app\__init__.py. Pass -Version explicitly."
    }
    $Version = $Matches[1]
}
if ($Version -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$') {
    throw "Version must be semantic, for example 1.2.3. Got: $Version"
}
if ($MinLauncher -notmatch '^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$') {
    throw "MinLauncher must be semantic, for example 1.0.0. Got: $MinLauncher"
}
if ($NotesPath) {
    $Notes = Get-Content (Resolve-Path $NotesPath) -Raw
}

if (-not $SkipBuild) {
    if ($BuildFrontend) {
        & (Join-Path $Root "scripts\build.ps1") -Python $Python -SkipInstall:$SkipInstall -BuildFrontend
    }
    else {
        & (Join-Path $Root "scripts\build.ps1") -Python $Python -SkipInstall:$SkipInstall
    }
    if ($LASTEXITCODE -ne 0) { throw "App build failed with exit code $LASTEXITCODE" }
}

$AppDist = Join-Path $Root "dist\VISION"
$AppExe = Join-Path $AppDist "VISION.exe"
if (-not (Test-Path $AppExe)) {
    throw "Expected onedir app executable was not produced: $AppExe"
}

$TreeRoot = Join-Path $Root "dist\launcher-tree"
$VersionStage = Join-Path $TreeRoot "versions\$Version"
$ArtifactDir = Join-Path $Root "dist\release"
New-Item -ItemType Directory -Force $VersionStage, $ArtifactDir | Out-Null

# Refresh only this version's staging directory.  The guard ensures a bad
# relative path can never recursively remove something outside dist.
$ResolvedVersionStage = [IO.Path]::GetFullPath($VersionStage)
$ResolvedDist = [IO.Path]::GetFullPath((Join-Path $Root "dist"))
if (-not $ResolvedVersionStage.StartsWith($ResolvedDist, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to stage a release outside the project dist directory: $ResolvedVersionStage"
}
Get-ChildItem -LiteralPath $VersionStage -Force | Remove-Item -Recurse -Force
Copy-Item (Join-Path $AppDist "*") $VersionStage -Recurse -Force

if (-not $SkipLauncher) {
    $LauncherDist = Join-Path $Root "dist\launcher-dist"
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onedir `
        --windowed `
        --name VISION `
        --distpath $LauncherDist `
        --workpath (Join-Path $Root "build\launcher") `
        --specpath (Join-Path $Root "build\launcher") `
        --icon (Join-Path $Root "assets\vision.ico") `
        (Join-Path $Root "launcher_stub.py")
    if ($LASTEXITCODE -ne 0) { throw "Launcher build failed with exit code $LASTEXITCODE" }

    $LauncherApp = Join-Path $LauncherDist "VISION"
    if (-not (Test-Path (Join-Path $LauncherApp "VISION.exe"))) {
        throw "Expected launcher executable was not produced under $LauncherApp"
    }
    Copy-Item (Join-Path $LauncherApp "*") $TreeRoot -Recurse -Force
}

New-Item -ItemType Directory -Force (Join-Path $TreeRoot "downloads") | Out-Null
$Current = [ordered]@{
    version = $Version
    previous = ""
    model_sha = ""
    last_checked = $null
}
$Current | ConvertTo-Json -Depth 3 | Set-Content (Join-Path $TreeRoot "current.json") -Encoding UTF8

# Gate 3: every update payload carries version.json at its root so the app
# can prove the archive matches the manifest before extracting anything.
$VersionJson = [ordered]@{ version = $Version; channel = $Channel; platform = "windows-x64" }
$VersionJson | ConvertTo-Json | Set-Content (Join-Path $VersionStage "version.json") -Encoding UTF8

$AppAsset = "VISION-$Version-win64.zip"
$AppZip = Join-Path $ArtifactDir $AppAsset
if (Test-Path $AppZip) { Remove-Item $AppZip -Force }
# Zip the *contents* of the version directory so VISION.exe is at the ZIP
# root, exactly where UpdateService._extract_app_zip expects it.
Compress-Archive -Path (Join-Path $VersionStage "*") -DestinationPath $AppZip -CompressionLevel Optimal

$InitialAsset = "VISION-$Version-win64-launcher.zip"
$InitialZip = Join-Path $ArtifactDir $InitialAsset
if (Test-Path $InitialZip) { Remove-Item $InitialZip -Force }
Compress-Archive -Path (Join-Path $TreeRoot "*") -DestinationPath $InitialZip -CompressionLevel Optimal

$Hash = (Get-FileHash -Algorithm SHA256 $AppZip).Hash.ToLowerInvariant()
$Size = (Get-Item $AppZip).Length
$Tag = "v$Version"
$DownloadUrl = "https://github.com/Akilesh8950/VISION/releases/download/$Tag/$AppAsset"
$Manifest = [ordered]@{
    version = $Version
    channel = $Channel
    platform = "windows-x64"
    url = $DownloadUrl
    size_bytes = $Size
    sha256 = $Hash
    min_launcher = $MinLauncher
    notes = $Notes.Trim()
    released_at = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
}
$ManifestName = if ($Channel -eq "dev") { "latest-dev.json" } else { "latest.json" }
$ManifestPath = Join-Path $Root $ManifestName
$Manifest | ConvertTo-Json -Depth 3 | Set-Content $ManifestPath -Encoding UTF8

if ($SigningKey -or $SigningKeyId) {
    if (-not $SigningKey -or -not $SigningKeyId) {
        throw "-SigningKey and -SigningKeyId must be supplied together."
    }
    $SigningScript = Join-Path $Root "scripts\release_signing.py"
    & $Python $SigningScript sign --private-key $SigningKey --key-id $SigningKeyId --purpose "app-manifest-$Channel" $ManifestPath
    if ($LASTEXITCODE -ne 0) { throw "Manifest signing failed with exit code $LASTEXITCODE" }
    if ($SignConfig) {
        $ConfigName = if ($Channel -eq "dev") { "config-dev.json" } else { "config.json" }
        $ConfigPath = Join-Path $Root "updates\$ConfigName"
        & $Python $SigningScript sign --private-key $SigningKey --key-id $SigningKeyId --purpose "remote-config-$Channel" $ConfigPath
        if ($LASTEXITCODE -ne 0) { throw "Config signing failed with exit code $LASTEXITCODE" }
    }
}
elseif ($Publish) {
    throw "Refusing to publish an unsigned app manifest. Supply -SigningKey and -SigningKeyId (and -SignConfig when the config changed)."
}
else {
    Write-Warning "Manifest is unsigned and will be rejected by signed-release builds until scripts\release_signing.py signs it."
}

Write-Host "Staged launcher tree: $TreeRoot"
Write-Host "App update ZIP:       $AppZip"
Write-Host "Initial install ZIP:  $InitialZip"
Write-Host "SHA-256:              $Hash"
Write-Host "Manifest:             $ManifestPath"

if ($Publish) {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw "-Publish requires the GitHub CLI (gh) on PATH."
    }
    & gh release view $Tag *> $null
    if ($LASTEXITCODE -ne 0) {
        & gh release create $Tag --title "VISION $Version" --notes $Notes
        if ($LASTEXITCODE -ne 0) { throw "gh release create failed with exit code $LASTEXITCODE" }
    }
    & gh release upload $Tag $AppZip $InitialZip --clobber
    if ($LASTEXITCODE -ne 0) { throw "gh release upload failed with exit code $LASTEXITCODE" }
    Write-Host "Uploaded release assets to $Tag. Commit $ManifestName only after the upload succeeds."
}
else {
    Write-Host "Not published. Re-run with -Publish after reviewing the ZIPs, then commit $ManifestName."
}
