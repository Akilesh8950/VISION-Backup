; VISION Windows installer (Inno Setup 6)
; Build through scripts\build_installer.ps1 so version and dist paths stay in sync.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceRoot
  #define SourceRoot ".."
#endif
#ifndef DistDir
  #define DistDir "..\dist\installer-payload"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\installer"
#endif

#define AppName "VISION"
#define AppExeName "VISION.exe"
#define PortableMarker "VISION.portable"

[Setup]
AppId={{4053D7ED-FBE7-442F-9624-9ACCA45DA82C}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=SPIRIT ORG
AppCopyright=VISION — built by SPIRIT ORG
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UsePreviousPrivileges=no
OutputDir={#OutputDir}
OutputBaseFilename=VISION-Setup-{#AppVersion}-x64
SetupIconFile={#SourceRoot}\assets\vision.ico
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
WizardStyle=modern
ShowLanguageDialog=no
MinVersion=10.0.17763
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline dialog
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
CloseApplications=yes
RestartApplications=no
VersionInfoCompany=SPIRIT ORG
VersionInfoDescription=VISION Desktop Setup
VersionInfoProductName=VISION Desktop
VersionInfoVersion={#AppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startmenuicon"; Description: "Create Start Menu shortcuts"; GroupDescription: "Shortcuts:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Dirs]
Name: "{app}\downloads"
Name: "{app}\versions"
Name: "{app}\data"; Check: UsePortableData
Name: "{app}\workspace"; Check: UsePortableData

[Files]
; Launcher tree built by scripts\build_installer.ps1: the stable outer
; VISION.exe sits beside current.json/versions and launches
; versions\<AppVersion>\VISION.exe. Never package developer data or a model.
Source: "{#DistDir}\*"; DestDir: "{app}"; Excludes: "data\*,workspace\*,*.gguf,{#PortableMarker}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\installer\{#PortableMarker}"; DestDir: "{app}"; Check: UsePortableData; Flags: onlyifdoesntexist

[InstallDelete]
; If an upgrade switches from portable to standard data, remove the old marker.
; Existing portable data itself is deliberately left untouched.
Type: files; Name: "{app}\{#PortableMarker}"; Check: UseStandardData

[UninstallDelete]
; Downloaded app versions are program files, not user data. Remove the
; launcher-managed payloads/cache on uninstall while still leaving portable
; data\ and workspace\ untouched above the installer layer.
Type: filesandordirs; Name: "{app}\versions"
Type: filesandordirs; Name: "{app}\downloads"
Type: files; Name: "{app}\current.json"
Type: files; Name: "{app}\pending.json"
Type: files; Name: "{app}\launcher-state.json"
Type: files; Name: "{app}\rollback-notice.txt"

[Icons]
Name: "{autoprograms}\VISION"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: startmenuicon
Name: "{group}\Uninstall VISION"; Filename: "{uninstallexe}"; Tasks: startmenuicon
Name: "{autodesktop}\VISION"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch VISION"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
var
  DataModePage: TInputOptionWizardPage;
  PreviousInstalledVersion: String;

function UsePortableData: Boolean;
begin
  Result := not IsAdminInstallMode;
  if DataModePage <> nil then
    Result := Result and DataModePage.Values[0];
end;

function UseStandardData: Boolean;
begin
  Result := not UsePortableData;
end;

function ExtractCurrentVersion(const Text: String): String;
var
  KeyPos: Integer;
  ColonPos: Integer;
  QuoteStart: Integer;
  QuoteEnd: Integer;
begin
  Result := '';
  KeyPos := Pos('"version"', Text);
  if KeyPos = 0 then Exit;

  ColonPos := Pos(':', Copy(Text, KeyPos, Length(Text) - KeyPos + 1));
  if ColonPos = 0 then Exit;
  ColonPos := KeyPos + ColonPos - 1;

  QuoteStart := Pos('"', Copy(Text, ColonPos + 1, Length(Text) - ColonPos));
  if QuoteStart = 0 then Exit;
  QuoteStart := ColonPos + QuoteStart;

  QuoteEnd := Pos('"', Copy(Text, QuoteStart + 1, Length(Text) - QuoteStart));
  if QuoteEnd = 0 then Exit;
  QuoteEnd := QuoteStart + QuoteEnd;

  if QuoteEnd > QuoteStart + 1 then
    Result := Copy(Text, QuoteStart + 1, QuoteEnd - QuoteStart - 1);
end;

function ReadExistingCurrentVersion: String;
var
  Raw: AnsiString;
begin
  Result := '';
  if LoadStringFromFile(ExpandConstant('{app}\current.json'), Raw) then
    Result := ExtractCurrentVersion(String(Raw));
end;

procedure WriteCurrentJson;
var
  PreviousVersion: String;
  Content: String;
begin
  PreviousVersion := PreviousInstalledVersion;
  if (PreviousVersion = '') or (CompareText(PreviousVersion, '{#AppVersion}') = 0) then
    PreviousVersion := '';
  if (PreviousVersion <> '') and
     not FileExists(ExpandConstant('{app}\versions\') + PreviousVersion + '\{#AppExeName}') then
    PreviousVersion := '';

  Content :=
    '{' + #13#10 +
    '  "version": "{#AppVersion}",' + #13#10 +
    '  "previous": "' + PreviousVersion + '",' + #13#10 +
    '  "model_sha": "",' + #13#10 +
    '  "last_checked": null' + #13#10 +
    '}' + #13#10;
  SaveStringToFile(ExpandConstant('{app}\current.json'), AnsiString(Content), False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  { Capture the launcher selection before [Files] overwrites current.json;
    after installation, select this setup's version while preserving the
    previously installed version as the rollback target. }
  if CurStep = ssInstall then
    PreviousInstalledVersion := ReadExistingCurrentVersion;
  if CurStep = ssPostInstall then
    WriteCurrentJson;
end;

function WebView2Installed: Boolean;
var
  Version: String;
begin
  Result := False;

  if RegQueryStringValue(
    HKEY_LOCAL_MACHINE,
    'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
    'pv',
    Version
  ) then
    Result := (Version <> '') and (Version <> '0.0.0.0');

  if not Result and RegQueryStringValue(
    HKEY_CURRENT_USER,
    'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
    'pv',
    Version
  ) then
    Result := (Version <> '') and (Version <> '0.0.0.0');
end;

function InitializeSetup: Boolean;
begin
  Result := True;
  if not WebView2Installed then
  begin
    Result := MsgBox(
      'Microsoft Edge WebView2 Runtime was not detected.' + #13#10 + #13#10 +
      'VISION needs WebView2 to display its interface. Windows 11 normally includes it. ' +
      'If it is missing, install the Evergreen WebView2 Runtime from Microsoft, then launch VISION.' + #13#10 + #13#10 +
      'Continue installing VISION anyway?',
      mbConfirmation,
      MB_YESNO
    ) = IDYES;
  end;
end;

procedure InitializeWizard;
begin
  DataModePage := CreateInputOptionPage(
    wpWelcome,
    'Choose the data location mode',
    'Do you want one portable folder, or standard Windows data folders?',
    'You will choose the installation folder on the next page.' + #13#10 + #13#10 +
    'Portable mode is the best choice when you want VISION on D:, an external SSD, or another ' +
    'folder you control. It keeps the app, downloaded model, llama.cpp, settings, history, logs, ' +
    'skills, WebView2 profile and tool workspace under that one folder.' + #13#10 + #13#10 +
    'Standard mode keeps per-user data under %LOCALAPPDATA%\VISION and the tool workspace under ' +
    '%USERPROFILE%\VISION\workspace. Choose Standard for an all-users/Program Files install.',
    True,
    False
  );

  DataModePage.Add('Portable folder (recommended) — everything under the folder I choose');
  DataModePage.Add('Standard Windows data — app in the chosen folder, data in my Windows profile');

  if IsAdminInstallMode then
  begin
    DataModePage.Values[1] := True;
    DataModePage.CheckListBox.ItemEnabled[0] := False;
  end
  else
  begin
    DataModePage.Values[0] := True;
  end;
end;

function IsSameOrSubDir(const ParentDir, ChildDir: String): Boolean;
var
  NormalizedParent: String;
  NormalizedChild: String;
begin
  NormalizedParent := AddBackslash(ParentDir);
  NormalizedChild := AddBackslash(ChildDir);
  Result := CompareText(Copy(NormalizedChild, 1, Length(NormalizedParent)), NormalizedParent) = 0;
end;

function ExistingParentPath(Path: String): String;
var
  ParentPath: String;
begin
  Result := Path;
  while not DirExists(Result) do
  begin
    ParentPath := RemoveBackslash(ExtractFilePath(RemoveBackslash(Result)));
    if (ParentPath = '') or (CompareText(ParentPath, Result) = 0) then
      Break;
    Result := ParentPath;
  end;
end;

function CheckPortableDirectory: Boolean;
var
  SelectedDir: String;
  QueryPath: String;
  FreeBytes: Int64;
  TotalBytes: Int64;
  RecommendedBytes: Int64;
begin
  Result := True;
  RecommendedBytes := 9;
  RecommendedBytes := RecommendedBytes * 1024 * 1024 * 1024;
  SelectedDir := WizardDirValue;

  if IsSameOrSubDir(ExpandConstant('{pf}'), SelectedDir) or
     IsSameOrSubDir(ExpandConstant('{win}'), SelectedDir) then
  begin
    MsgBox(
      'Portable mode needs a folder that your Windows account can write to, such as D:\VISION, ' +
      'E:\Apps\VISION or a folder under your user profile. Please choose a folder outside ' +
      'Program Files and Windows.',
      mbError,
      MB_OK
    );
    Result := False;
    Exit;
  end;

  QueryPath := ExistingParentPath(SelectedDir);
  if GetSpaceOnDisk64(QueryPath, FreeBytes, TotalBytes) then
  begin
    { About 9 GiB: app + first-run model/llama downloads + working headroom. }
    if FreeBytes < RecommendedBytes then
    begin
      Result := MsgBox(
        'The selected portable drive has less than about 9 GiB free.' + #13#10 + #13#10 +
        'VISION itself can install there, but the first-run local model is about 5.24 GiB and ' +
        'also needs download/working headroom. For the smoothest local setup, choose an SSD ' +
        'location with at least 12 GiB free.' + #13#10 + #13#10 +
        'Continue with this location anyway?',
        mbConfirmation,
        MB_YESNO
      ) = IDYES;
    end;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and UsePortableData then
    Result := CheckPortableDirectory;
end;

function InitializeUninstall: Boolean;
begin
  Result := True;
  if FileExists(ExpandConstant('{app}\{#PortableMarker}')) then
  begin
    MsgBox(
      'This is a portable VISION installation.' + #13#10 + #13#10 +
      'Uninstalling removes the program files and shortcuts, but deliberately leaves your ' +
      'data, model and workspace folders in place:' + #13#10 +
      ExpandConstant('{app}\data') + #13#10 +
      ExpandConstant('{app}\workspace') + #13#10 + #13#10 +
      'Delete those folders manually only if you no longer need them.',
      mbInformation,
      MB_OK
    );
  end;
end;
