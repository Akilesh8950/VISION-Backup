# VISION Folder Setup helper

`VISION-Create-Folders.exe` is a small Windows x64 utility. It asks for one
parent folder, then creates exactly these two top-level folders:

```text
<chosen folder>\VISION-Source
<chosen folder>\VISION
```

Use them this way:

- **VISION-Source** — put the source/build files here and open PowerShell here
  when building the installer.
- **VISION** — choose this as the final installation folder in Setup.

The helper only creates the folders and a short README in each. It does not
download VISION, install VISION, or create the launcher's internal update
folders; Setup/the installed app creates those later.

## Rebuild

With MinGW-w64 available:

```powershell
windres VisionFolderSetup.rc -O coff -o VisionFolderSetup.res
g++ -std=c++17 -O2 -mwindows -municode -static -static-libgcc -static-libstdc++ VisionFolderSetup.cpp VisionFolderSetup.res -o VISION-Create-Folders.exe -lole32 -lshell32 -lcomctl32 -luuid
```

The committed `.sha256` file contains the SHA-256 of the supplied executable.
The executable is unsigned, so Windows may show a SmartScreen prompt; verify
its hash before choosing **More info → Run anyway**.
