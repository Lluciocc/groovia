#define MyAppName "Groovia"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Lluciocc"
#define MyAppExeName "Groovia.exe"

[Setup]
AppId={{B8B7F0A7-8F05-4D07-9C7B-7E5F6C5F1B41}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Groovia music player
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoCopyright=Copyright 2026 Lluciocc
DefaultDirName={localappdata}\Programs\Groovia
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64
OutputDir=..\..\dist\installer
OutputBaseFilename=Groovia-{#MyAppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
Uninstallable=yes
CloseApplications=yes
RestartApplications=no
SetupIconFile=..\..\build\windows\Groovia.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ChangesAssociations=no

[Files]
Source: "..\..\dist\Groovia\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Groovia"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

; The uninstaller removes only {app}. Groovia's database, music, lyrics,
; downloader cache and settings live outside {app} and are intentionally kept.
