; Windows installer for datapipe (Inno Setup 6). It wraps the standalone program built by packaging/build.py.
; Build it with:  python packaging/build_installer.py        (or: ISCC /DAppVersion=0.1.0 packaging\datapipe.iss)
;
; Installs for the current user only (no administrator rights needed): %LOCALAPPDATA%\Programs\datapipe. The setup window
; offers "for all users" instead, which installs to Program Files and asks for administrator rights.
; Your files (~\datapipe\files) and results (~\datapipe\work) are NOT part of the installation, so uninstalling never deletes them.
; The installer is not code-signed, so Windows shows an "unknown publisher" warning (SmartScreen: More info, then Run anyway).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "datapipe"
#define AppExe "datapipe.exe"
#define UserData "{%USERPROFILE}\datapipe"

[Setup]
; Never change this id: it is how a newer installer finds and upgrades an older installation.
AppId={{7EBB069F-1951-4BBD-B2F4-5CC43237849C}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=datapipe-prototype
AppPublisherURL=https://github.com/Aleks108-heaven/datapipe-prototype
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=datapipe-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
CloseApplications=yes
RestartApplications=no

[Messages]
WelcomeLabel2=This installs [name/ver] on your computer.%n%ndatapipe cleans and checks data files and keeps everything on this computer: the program opens in your web browser, but nothing is sent anywhere.%n%nThis installer is not signed, so Windows may call the publisher "unknown". That is expected.
FinishedLabel=datapipe is installed. Start it from the Start menu. Put your data files in the folder  %USERPROFILE%\datapipe\files  or choose a file from anywhere inside the app.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Dirs]
; The folder the program reads by default. Created here so it can be filled before the first start; kept on uninstall.
Name: "{#UserData}\files"; Flags: uninsneveruninstall

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{#UserData}"; Comment: "Clean and check a data file, on this computer"
Name: "{autoprograms}\{#AppName} files folder"; Filename: "{#UserData}\files"; Comment: "Put your data files here"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{#UserData}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; WorkingDir: "{#UserData}"; Description: "Start {#AppName} now"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox('datapipe was removed.' + #13#10 + #13#10 +
           'Your files and results were not touched; they are still in' + #13#10 +
           ExpandConstant('{#UserData}') + #13#10 + 'Delete that folder yourself if you no longer need them.',
           mbInformation, MB_OK);
end;
