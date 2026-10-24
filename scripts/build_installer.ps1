[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$Version = "",
    [string]$InnoCompiler = "",
    [switch]$SkipAppBuild,
    [switch]$SkipPythonInstall,
    [switch]$BuildFrontend,
    [switch]$PortableZip,
    [switch]$SkipInstaller
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DistDir = Join-Path $Root "dist\VISION"
$OutputDir = Join-Path $Root "dist\installer"

function Write-Sha256File {
    param([Parameter(Mandatory = $true)][string]$Path)

    $hash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    $checksumPath = "$Path.sha256"
    "$hash  $(Split-Path -Leaf $Path)" | Set-Content -LiteralPath $checksumPath -Encoding ascii
    Write-Host "SHA256 $hash  $Path"
    Write-Host "Checksum file: $checksumPath"
}

function Find-InnoCompiler {
    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($InnoCompiler)) {
        $candidates.Add($InnoCompiler)
    }

    $command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        $candidates.Add($command.Source)
    }

    $bases = @(
        ${env:ProgramFiles(x86)},
        $env:ProgramFiles,
        (Join-Path $env:LOCALAPPDATA "Programs")
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

    foreach ($base in $bases) {
        foreach ($folder in @("Inno Setup 7", "Inno Setup 6")) {
            $candidates.Add((Join-Path $base "$folder\ISCC.exe"))
        }
    }

    return ($candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1)
}

Push-Location $Root
try {
    if (-not $SkipAppBuild) {
        $buildArgs = @{ Python = $Python }
        if ($SkipPythonInstall) { $buildArgs.SkipInstall = $true }
        if ($BuildFrontend) { $buildArgs.BuildFrontend = $true }
        & (Join-Path $Root "scripts\build.ps1") @buildArgs
    }

    $exe = Join-Path $DistDir "VISION.exe"
    if (-not (Test-Path -LiteralPath $exe)) {
        throw "Packaged app not found: $exe. Run without -SkipAppBuild first."
    }

    $unexpectedModel = Get-ChildItem -LiteralPath $DistDir -Recurse -File -Filter "*.gguf" | Select-Object -First 1
    if ($null -ne $unexpectedModel) {
        throw "The packaged dist folder contains a GGUF model ($($unexpectedModel.FullName)). Rebuild a clean dist before making an installer; the model is downloaded on first run instead of being embedded."
    }

    foreach ($portablePath in @("data", "workspace", "VISION.portable")) {
        $candidate = Join-Path $DistDir $portablePath
        if (Test-Path -LiteralPath $candidate) {
            throw "The packaged dist folder is not clean: found $candidate. Rebuild before making an installer so personal portable data is never packaged."
        }
    }

    if ([string]::IsNullOrWhiteSpace($Version)) {
        $initText = Get-Content -LiteralPath (Join-Path $Root "src\vision_app\__init__.py") -Raw
        if ($initText -notmatch '__version__\s*=\s*"([^"]+)"') {
            throw "Could not read __version__ from src\vision_app\__init__.py. Pass -Version explicitly."
        }
        $Version = $Matches[1]
    }

    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

    if (-not $SkipInstaller) {
        $iscc = Find-InnoCompiler
        if ([string]::IsNullOrWhiteSpace($iscc)) {
            throw @"
Inno Setup compiler ISCC.exe was not found.

Install Inno Setup 6, then run this script again:
  winget install --id JRSoftware.InnoSetup -e -s winget

Or pass the compiler path explicitly:
  .\scripts\build_installer.ps1 -InnoCompiler "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
"@
        }

        $script = Join-Path $Root "installer\VISION.iss"
        Write-Host "Compiling installer with $iscc"
        & $iscc "/DAppVersion=$Version" "/DSourceRoot=$Root" "/DDistDir=$DistDir" "/DOutputDir=$OutputDir" $script
        if ($LASTEXITCODE -ne 0) {
            throw "Inno Setup failed with exit code $LASTEXITCODE"
        }

        $installer = Join-Path $OutputDir "VISION-Setup-$Version-x64.exe"
        if (-not (Test-Path -LiteralPath $installer)) {
            throw "Installer was not produced at $installer"
        }
        Write-Sha256File -Path $installer
        Write-Host "Installer: $installer"
    }

    if ($PortableZip) {
        $portableRoot = Join-Path $Root "dist\portable"
        New-Item -ItemType Directory -Force -Path $portableRoot | Out-Null
        $stageRoot = Join-Path $portableRoot "VISION-$Version-portable-x64"
        if (Test-Path -LiteralPath $stageRoot) {
            $resolvedStage = (Resolve-Path -LiteralPath $stageRoot).Path
            $resolvedRoot = (Resolve-Path -LiteralPath $portableRoot).Path
            if (-not $resolvedStage.StartsWith($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Refusing to clean unexpected portable staging path: $resolvedStage"
            }
            Remove-Item -LiteralPath $resolvedStage -Recurse -Force
        }

        $stageApp = Join-Path $stageRoot "VISION"
        New-Item -ItemType Directory -Force -Path $stageApp | Out-Null
        Copy-Item -Path (Join-Path $DistDir "*") -Destination $stageApp -Recurse -Force
        Copy-Item -LiteralPath (Join-Path $Root "installer\VISION.portable") -Destination (Join-Path $stageApp "VISION.portable") -Force
        New-Item -ItemType Directory -Force -Path (Join-Path $stageApp "data") | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $stageApp "workspace") | Out-Null

        $zip = Join-Path $OutputDir "VISION-$Version-portable-x64.zip"
        if (Test-Path -LiteralPath $zip) {
            Remove-Item -LiteralPath $zip -Force
        }
        Compress-Archive -Path (Join-Path $stageRoot "*") -DestinationPath $zip -CompressionLevel Optimal
        Write-Sha256File -Path $zip
        Write-Host "Portable ZIP: $zip"
    }
}
finally {
    Pop-Location
}
