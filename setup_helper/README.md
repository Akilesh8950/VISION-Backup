# VISION Folder Setup helper (source only)

Public VISION packages do **not** include a prebuilt
`VISION-Create-Folders.exe`. The normal installer already creates the
installation folder and its internal update folders, so end users do not
need this helper.

The optional helper source remains here for a future verified Windows
build. When built, the utility asks for one parent folder, then creates
exactly these two top-level folders:

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

## Rebuild locally

With MinGW-w64 available:

```powershell
windres VisionFolderSetup.rc -O coff -o VisionFolderSetup.res
g++ -std=c++17 -O2 -mwindows -municode -static -static-libgcc -static-libstdc++ VisionFolderSetup.cpp VisionFolderSetup.res -o VISION-Create-Folders.exe -lole32 -lshell32 -lcomctl32 -luuid
```

A locally rebuilt executable is still unsigned unless the release operator
signs it with a trusted Windows code-signing certificate. Do not commit the
rebuilt `.exe` or a checksum for it to the public source tree.
