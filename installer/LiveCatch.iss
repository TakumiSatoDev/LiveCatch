#ifndef AppVersion
#define AppVersion "3.1.0"
#endif

[Setup]
AppId={{B9A1E4D2-6C3F-4E4B-9A6B-7D55F72D0F31}
AppName=LiveCatch
AppVersion={#AppVersion}
AppPublisher=TakumiSatoDev
AppPublisherURL=https://github.com/TakumiSatoDev/LiveCatch
AppSupportURL=https://github.com/TakumiSatoDev/LiveCatch/issues
AppUpdatesURL=https://github.com/TakumiSatoDev/LiveCatch/releases/latest
DefaultDirName={localappdata}\Programs\LiveCatch
DefaultGroupName=LiveCatch
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=LiveCatch-Setup-{#AppVersion}
UninstallDisplayIcon={app}\LiveCatch.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesAssociations=no
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoCompany=TakumiSatoDev
VersionInfoDescription=LiveCatch livestream recorder
VersionInfoCopyright=Copyright (C) TakumiSatoDev
LicenseFile=..\LICENSE

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "デスクトップにショートカットを作成する"; GroupDescription: "追加ショートカット:"; Flags: unchecked

[Files]
Source: "..\dist\LiveCatch.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\LiveCatchWorker.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\LiveCatchUpdater.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\tools\*"; DestDir: "{app}\tools"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: isreadme ignoreversion
Source: "..\CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\packaging\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\LiveCatch"; Filename: "{app}\LiveCatch.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\LiveCatch"; Filename: "{app}\LiveCatch.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\LiveCatch.exe"; Description: "LiveCatchを起動する"; Flags: postinstall nowait skipifsilent
