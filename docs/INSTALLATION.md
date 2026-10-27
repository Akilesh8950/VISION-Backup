# VISION installation guide

VISION — built by SPIRIT ORG

This guide covers the optimized Windows setup, the fully portable single-folder mode, first-run downloads, moving/backing up an installation, updates, uninstalling, developer builds, and troubleshooting.

## Recommended setup at a glance

For a personal PC, use this combination:

1. **Install for current user only** when Setup asks who can use VISION.
2. Choose **Portable folder (recommended)** on the data-location page.
3. Choose a short folder on a fast internal SSD with at least **12 GiB free**, for example:
   - `D:\VISION`
   - `E:\Apps\VISION`
   - `C:\Users\<you>\VISION`
4. Let VISION download the model and `llama-server` on first launch.

That keeps the application, model, llama.cpp runtime, settings, history, logs, skills, WebView2 profile, and tool workspace together:

```text
D:\VISION\
  VISION.exe              (stable update launcher)
  VISION.portable
  _internal\              (launcher support files)
  current.json
  downloads\
  versions\<version>\VISION.exe
  data\
    models\
    bin\
    webview\
    ...settings, history, logs and skills...
  workspace\
```

### Why this layout is the optimized default

- VISION is packaged as a PyInstaller **one-folder** app rather than a one-file executable. Windows does not have to unpack the whole application to a temporary folder on every launch.
- The approximately **5.24 GiB** model is **not embedded in the installer**. The installer stays comparatively small, and the model is downloaded once into the data location.
- The installer uses Inno Setup solid **LZMA2/Ultra64** compression for a compact setup file.
- Portable mode avoids copying the large model between an app drive and a separate `%LOCALAPPDATA%` drive.
- A current-user install normally needs no administrator prompt and keeps private chat/model data under your control.

## Requirements

- Windows 10 version 1809 or later, or Windows 11. Windows 11 x64 is the primary target.
- 64-bit x64 or x64-compatible Arm64 Windows.
- Microsoft Edge WebView2 Runtime. Windows 11 normally includes it, and Setup checks for it.
- Internet access on first launch to download the model and supported `llama-server`.
- At least 12 GiB free on the selected portable drive for a comfortable local installation. The model itself is about 5.24 GiB, and VISION also needs space for the app, llama.cpp, downloads, history, and workspace.
- Enough system RAM/VRAM for the selected GPU-layer setting. If the GPU runs out of VRAM, lower **GPU layers** in first-run setup or Settings.

> Do not put a portable installation in `C:\Program Files`, `C:\Windows`, a synced cloud folder, or a slow network share. Use a folder your own Windows account can write to. OneDrive/Dropbox-style syncing can repeatedly upload the 5 GiB model and can conflict with live database/cache files.

## Part 1 — Build the installer (developer/owner)

End users only need the finished `VISION-Setup-...exe`; skip to Part 2 if you already have it.

> Optional folder helper: run `setup_helper\VISION-Create-Folders.exe`, choose a parent such as `D:\`, and it creates `D:\VISION-Source` and `D:\VISION`. Put source/build files only in `VISION-Source`; later choose `VISION` as Setup's installation folder. The helper only creates those folders—it does not download or install VISION.

### 1. Install build prerequisites

Install:

- Git
- Python 3.11 x64 available as `python`
- Inno Setup 6
- Node.js 20.19+ (or 22.12+) only if you plan to rebuild the React frontend

Install Inno Setup from an elevated or normal PowerShell terminal with WinGet:

```powershell
winget install --id JRSoftware.InnoSetup -e -s winget
```

### 2. Get the source and open PowerShell in the repo

```powershell
git clone <your-vision-repo-url> VISION
cd VISION
python --version
```

If PowerShell blocks local scripts, allow them for this terminal session only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### 3. Build the app and installer

The committed React production bundle is already in `app\web`, so the normal release build is:

```powershell
.\scripts\build_installer.ps1
```

This will:

1. Run `scripts\build.ps1` to build the actual app at `dist\VISION\VISION.exe` with PyInstaller `--onedir`, optimized compiled bytecode, and the minified, source-map-free React bundle.
2. Refuse to package a dirty `dist\VISION` that contains a `.gguf` model, `data`, `workspace`, `VISION.portable`, frontend `.map` files, frontend JSX/TypeScript/package files, or a copied application source tree.
3. Build the stable outer launcher and stage `dist\installer-payload` as `VISION.exe` + `current.json` + `versions\<version>\VISION.exe`, so later app updates can be applied by the launcher after VISION exits.
4. Compile `installer\VISION.iss` with Inno Setup.
5. Write the installer and its SHA-256 checksum to `dist\installer\`.

The release payload avoids plaintext application source and never receives saved
API secrets. Compiled/minified local code is harder to inspect but can still be
reverse-engineered by a determined person with control of their PC; do not embed
private owner/service secrets in the client.

Expected output:

```text
dist\installer\VISION-Setup-<version>-x64.exe
dist\installer\VISION-Setup-<version>-x64.exe.sha256
```

If frontend source changed, rebuild it as part of the same command:

```powershell
.\scripts\build_installer.ps1 -BuildFrontend
```

Useful rebuild options:

```powershell
# Reuse an existing dist\VISION and only recompile the installer
.\scripts\build_installer.ps1 -SkipAppBuild

# Also create a no-install portable ZIP
.\scripts\build_installer.ps1 -PortableZip

# Use a nonstandard Inno Setup location
.\scripts\build_installer.ps1 -InnoCompiler "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

> The build produced by this repository is not automatically code-signed. Windows SmartScreen may warn on first run. For public distribution, sign the executable and installer with a trusted code-signing certificate and publish the `.sha256` checksum beside the download.

## Part 2 — Install VISION (end user tutorial)

### Step 1: Verify the installer download

If a `.sha256` file was provided with the installer, open PowerShell in the download folder and run:

```powershell
Get-FileHash .\VISION-Setup-<version>-x64.exe -Algorithm SHA256
Get-Content .\VISION-Setup-<version>-x64.exe.sha256
```

The long hexadecimal hash should match. If it does not match, do not run the installer; download it again from the trusted source.

### Step 2: Start Setup

Double-click:

```text
VISION-Setup-<version>-x64.exe
```

If Windows SmartScreen appears for an unsigned local build:

1. Confirm the SHA-256 hash first.
2. Click **More info**.
3. Click **Run anyway** only if the hash matches and you trust the source.

Setup first checks for Microsoft Edge WebView2 Runtime. If it is missing, install Microsoft's Evergreen WebView2 Runtime, then launch VISION again. Microsoft's download page is:

<https://developer.microsoft.com/en-us/microsoft-edge/webview2/?form=MO12FY#download>

### Step 3: Choose current user or all users

Setup asks who should be able to use this installation.

#### Recommended: Install for current user only

Choose this for a normal personal installation. It:

- normally avoids an administrator prompt;
- enables fully portable single-folder mode;
- keeps shortcuts and uninstall registration under your account.

#### Install for all users

Choose this only when several Windows accounts on the same PC should share the same VISION program files. Setup will request administrator permission and will force **Standard Windows data** mode. Each user still gets private data under their own profile; the model may therefore be downloaded per user. This prevents shared Program Files data permissions and privacy problems.

### Step 4: Choose Portable or Standard data

On **Choose the data location mode**, select one of:

#### Portable folder (recommended)

Everything goes under the installation folder you select next:

```text
<chosen folder>\VISION.exe
<chosen folder>\VISION.portable
<chosen folder>\data\
<chosen folder>\workspace\
```

Choose this when you want VISION on `D:`, an external SSD, or another visible folder you can back up and move.

#### Standard Windows data

The program still installs into the folder you select next, but data stays in the usual Windows locations:

```text
%LOCALAPPDATA%\VISION\
%USERPROFILE%\VISION\workspace\
```

Choose this for all-users/Program Files installs or managed PCs.

> Security exception: API keys and Hugging Face tokens are not stored as plain files in portable `data`. VISION keeps secrets in Windows Credential Manager. If you move a portable folder to another PC/account, re-enter those secrets in Settings.

### Step 5: Choose the installation location

Click **Browse…** and choose the final VISION folder itself, for example:

```text
D:\VISION
```

Do not choose the drive root alone. Prefer:

- a fast internal NVMe/SSD;
- at least 12 GiB free for local mode;
- a short path without cloud sync;
- a folder your account can write to without administrator rights.

For portable mode, Setup warns if the selected drive has less than about 9 GiB free and blocks protected locations such as Program Files and Windows.

### Step 6: Choose shortcuts and install

Leave **Create Start Menu shortcuts** enabled unless you do not want them. Enable **Create a desktop shortcut** if desired.

Review the Ready page and click **Install**.

### Step 7: Launch VISION and complete first-run setup

Leave **Launch VISION** checked and click **Finish**.

On first launch, VISION opens its setup flow:

1. Click **1. Download model**.
   - This downloads `vision-ornith-1.5-9b-q4_k_m.gguf` (about 5.24 GiB).
   - Keep the PC awake and connected until it finishes.
2. Click **2. Download llama-server**.
   - This downloads the supported llama.cpp runtime if a suitable bundled/existing runtime is not already found.
3. Leave **GPU layers** at `99` for maximum GPU offload, or lower it if your GPU has limited VRAM.
4. Click **Finish**.
5. Send a short test message, such as `Hello, confirm you are ready.`

You can click **Skip for now** and finish later from Settings, but local chat needs both the model and `llama-server`.

## Portable ZIP alternative

Developer builds can also produce:

```text
dist\installer\VISION-<version>-portable-x64.zip
```

To use it:

1. Verify its `.sha256` checksum.
2. Extract it to a writable SSD folder such as `D:\`.
3. Open the extracted `VISION` folder.
4. Confirm `VISION.portable` is beside `VISION.exe`.
5. Run `VISION.exe` and complete first-run setup.

The ZIP has no uninstaller or automatic shortcuts. To remove it, close VISION and delete the extracted folder.

## Moving or backing up a portable installation

1. Close VISION completely.
2. Wait a few seconds so the managed `llama-server` process can exit.
3. Copy or ZIP the **entire** VISION folder, including:
   - `VISION.exe`
   - `VISION.portable`
   - `_internal\`
   - `current.json`
   - `versions\` (downloaded app versions; `downloads\` may be omitted from a backup)
   - `data\`
   - `workspace\`
4. Paste/extract it at the new location.
5. Delete old desktop/Start Menu shortcuts if they still point to the old path, and create new shortcuts to the new `VISION.exe`.
6. Launch from the new location and send a test message.
7. Re-enter API/Hugging Face secrets on a different PC or Windows account; Credential Manager entries do not travel inside the folder.

Projects, code-workspace roots, MCP server commands, or connector paths that referenced absolute paths outside the portable folder may need to be re-pointed after a move.

For a smaller backup, you may omit `data\models\*.gguf`, but VISION will need to download the model again after restore.

## Updating

### Normal in-app updates

Open **Settings → Updates**. VISION separately checks:

- **App** — downloads and hash-verifies in the background, then waits. It is applied only when you click **Restart to apply**; the outer launcher switches versions after VISION exits and keeps the previous version for rollback.
- **Model** — downloads to a pending location and activates on the next launch, never during a conversation.
- **Config** — validated prompt/tool-schema/UI-string changes can apply immediately; malformed remote config is rejected.

Leave **Automatically check for updates** on unless you want manual checks only. Offline checks fail quietly and keep the last-known status.

### Setup/manual updates

Use this if Setup reports that a newer launcher is required, or if you prefer a manual reinstall:

1. Close VISION.
2. Run the newer `VISION-Setup-...exe`.
3. Choose the same install scope, the same Portable/Standard data mode, and the same installation folder.
4. Finish setup and launch VISION.

Program files are replaced. Portable `data` and `workspace`, and Standard profile data, are not intentionally deleted by an update.

If you switch between Portable and Standard during an upgrade, VISION does not silently migrate private data. The installer adds/removes the `VISION.portable` marker for the newly selected mode and leaves the old data in place. Migrate manually only after closing VISION:

- Standard → Portable: copy the contents of `%LOCALAPPDATA%\VISION` to `<install>\data`, and `%USERPROFILE%\VISION\workspace` to `<install>\workspace`.
- Portable → Standard: copy `<install>\data` contents to `%LOCALAPPDATA%\VISION`, and `<install>\workspace` contents to `%USERPROFILE%\VISION\workspace`.

Back up both locations before merging folders.

## Uninstalling

Open **Windows Settings → Apps → Installed apps → VISION → Uninstall**.

### Portable installs

The uninstaller removes program files, the portable marker, shortcuts, and uninstall registration. It deliberately leaves:

```text
<install>\data\
<install>\workspace\
```

This protects the model, history, settings, and workspace from accidental deletion. To remove everything, uninstall first, confirm VISION is closed, then manually delete the remaining installation folder.

### Standard installs

The uninstaller removes program files and shortcuts. To also remove local data, manually delete:

```text
%LOCALAPPDATA%\VISION\
%USERPROFILE%\VISION\
```

Remove saved VISION credentials from Windows Credential Manager if you are retiring the PC/account.

## Advanced silent installation

Validate silent deployment on a test machine/VM before using it broadly.

Current-user portable install to `D:\VISION`:

```powershell
.\VISION-Setup-<version>-x64.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /DIR="D:\VISION"
```

All-users standard install:

```powershell
.\VISION-Setup-<version>-x64.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /ALLUSERS /DIR="C:\Program Files\VISION"
```

The all-users form requires administrator rights and uses per-user Standard data. The local model and llama.cpp runtime are still first-run downloads unless your deployment separately provisions them.

## Troubleshooting

### Setup asks for administrator permission unexpectedly

You probably selected **Install for all users**, or launched Setup with **Run as administrator**. Restart Setup normally and choose **current user only** for a portable personal install.

### Portable folder option is disabled

An all-users install is selected. Go back/restart Setup and choose **current user only**. Portable private data is intentionally not placed in shared Program Files.

### Setup says the folder is not suitable for portable mode

Choose a folder outside Program Files and Windows, such as `D:\VISION` or `C:\Users\<you>\VISION`.

### Not enough disk space

Free space or choose another SSD. For local use, keep at least 12 GiB free on the portable drive. In Standard mode, also check free space on the drive containing `%LOCALAPPDATA%`.

### A blank window opens or the interface does not load

Install/repair Microsoft Edge WebView2 Runtime, then relaunch VISION. Also confirm no security software quarantined files under `versions\<version>\_internal\app\web`.

### VISION used `%LOCALAPPDATA%` even though I expected portable data

Check all of the following:

1. `VISION.portable` exists in the same folder as the `VISION.exe` you launched.
2. Your shortcut points to that `VISION.exe`, not an older install.
3. The `VISION_HOME` environment variable is not set; it intentionally overrides portable detection.

Check `VISION_HOME` in PowerShell:

```powershell
$env:VISION_HOME
[Environment]::GetEnvironmentVariable("VISION_HOME", "User")
[Environment]::GetEnvironmentVariable("VISION_HOME", "Machine")
```

### Model download is slow or interrupted

Use a stable connection, keep the PC awake, and retry from VISION Settings. Avoid storing the portable folder on a nearly full or cloud-synced drive.

### Out of VRAM, server exits, or the first response is very slow

Lower **GPU layers** in Settings and restart the local server. The default `99` requests maximum offload; smaller GPUs may need a lower value.

### Antivirus or SmartScreen blocks the unsigned build

Verify the published SHA-256 hash. Only restore/allow a file whose hash matches a trusted release. For public releases, use a signed installer.

### Developer build says `ISCC.exe` was not found

Install Inno Setup:

```powershell
winget install --id JRSoftware.InnoSetup -e -s winget
```

Then open a new PowerShell window and rerun `.\scripts\build_installer.ps1`, or pass `-InnoCompiler` with the full `ISCC.exe` path.

### PowerShell says scripts are disabled

For the current terminal only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```
