#define MyAppName "Groovia"
#define MyAppVersion "1.2.0"
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
; Register Groovia as an optional "Open with" handler for common audio files.
; The OpenWithProgids entries make Groovia available without changing the
; user's current default music player.
ChangesAssociations=yes

[Files]
Source: "..\..\dist\Groovia\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; ProgID shared by all supported audio extensions.
Root: HKCU; Subkey: "Software\Classes\Groovia.Audio"; ValueType: string; ValueName: ""; ValueData: "Groovia audio file"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Groovia.Audio\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKCU; Subkey: "Software\Classes\Groovia.Audio\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

; Register only as an available "Open with" application, never as the
; default handler. HKCU keeps the installation per-user as configured above.
Root: HKCU; Subkey: "Software\Classes\.mp3\OpenWithProgids"; ValueType: string; ValueName: "Groovia.Audio"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.flac\OpenWithProgids"; ValueType: string; ValueName: "Groovia.Audio"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.ogg\OpenWithProgids"; ValueType: string; ValueName: "Groovia.Audio"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.oga\OpenWithProgids"; ValueType: string; ValueName: "Groovia.Audio"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.opus\OpenWithProgids"; ValueType: string; ValueName: "Groovia.Audio"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.wav\OpenWithProgids"; ValueType: string; ValueName: "Groovia.Audio"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.aac\OpenWithProgids"; ValueType: string; ValueName: "Groovia.Audio"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.m4a\OpenWithProgids"; ValueType: string; ValueName: "Groovia.Audio"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.m4b\OpenWithProgids"; ValueType: string; ValueName: "Groovia.Audio"; ValueData: ""; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\.mp4\OpenWithProgids"; ValueType: string; ValueName: "Groovia.Audio"; ValueData: ""; Flags: uninsdeletevalue

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Groovia"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

; The uninstaller removes only {app}. Groovia's database, music, lyrics,
; downloader cache and settings live outside {app} and are intentionally kept.
