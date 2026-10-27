; SiteMirror Studio — Inno Setup 6 script (optional single-file installer).
; Build the PyInstaller bundle first (build-windows.bat), then compile this file.
#define MyAppName "SiteMirror Studio"
#define MyAppVersion "2.6.0-stable"
#define MyAppExeName "SiteMirrorStudio.exe"

[Setup]
AppId={{C4A21B7E-9F3D-4E6A-8F0A-SITEMIRROR01}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\SiteMirror Studio
DefaultGroupName={#MyAppName}
OutputDir=..\..\dist
OutputBaseFilename=SiteMirror-Studio-2.6.0-stable-windows-x64-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
PrivilegesRequired=lowest
WizardStyle=modern

[Files]
Source: "..\..\dist\SiteMirrorStudio\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
