; Windows installer for datapipe (Inno Setup 6). It wraps the standalone program built by packaging/build.py.
; Build it with:  python packaging/build_installer.py        (or: ISCC /DAppVersion=0.1.0 packaging\datapipe.iss)
;
; Installs for the current user only (no administrator rights needed): %LOCALAPPDATA%\Programs\datapipe. The setup window
; offers "for all users" instead, which installs to Program Files and asks for administrator rights.
; Your files (~\datapipe\files) and results (~\datapipe\work) are NOT part of the installation, so uninstalling never deletes them.
; The wizard is in English or Ukrainian: it follows the language of Windows, and asks only when Windows uses neither.
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
ShowLanguageDialog=auto

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"

[Messages]
english.WelcomeLabel2=This installs [name/ver] on your computer.%n%ndatapipe cleans and checks data files and keeps everything on this computer: the program opens in your web browser, but nothing is sent anywhere.%n%nThis installer is not signed, so Windows may call the publisher "unknown". That is expected.
english.FinishedLabel=datapipe is installed. Start it from the Start menu. Put your data files in the folder  %USERPROFILE%\datapipe\files  or choose a file from anywhere inside the app.
ukrainian.WelcomeLabel2=Ця програма встановить [name/ver] на ваш комп’ютер.%n%ndatapipe очищає та перевіряє файли з даними й залишає все на цьому комп’ютері: програма відкривається у вашому браузері, але нічого нікуди не надсилається.%n%nЦей інсталятор не підписано, тому Windows може назвати видавця “невідомим”. Це очікувано.
ukrainian.FinishedLabel=datapipe встановлено. Запустіть його з меню “Пуск”. Кладіть файли з даними в папку  %USERPROFILE%\datapipe\files  або виберіть файл будь-де всередині програми.

[CustomMessages]
english.AppComment=Clean and check a data file, on this computer
english.FilesFolder=datapipe files folder
english.FilesFolderComment=Put your data files here
english.StartNow=Start datapipe now
english.UninstallNote=datapipe was removed.%n%nYour files and results were not touched; they are still in%n%1%nDelete that folder yourself if you no longer need them.
ukrainian.AppComment=Очищення та перевірка файлу з даними на цьому комп’ютері
ukrainian.FilesFolder=datapipe - папка з файлами
ukrainian.FilesFolderComment=Кладіть сюди файли з даними
ukrainian.StartNow=Запустити datapipe зараз
ukrainian.UninstallNote=datapipe видалено.%n%nВаші файли та результати не чіпали; вони досі лежать у%n%1%nВидаліть цю папку самі, якщо вони вам більше не потрібні.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Dirs]
; The folder the program reads by default. Created here so it can be filled before the first start; kept on uninstall.
Name: "{#UserData}\files"; Flags: uninsneveruninstall

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{#UserData}"; Comment: "{cm:AppComment}"
Name: "{autoprograms}\{cm:FilesFolder}"; Filename: "{#UserData}\files"; Comment: "{cm:FilesFolderComment}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{#UserData}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; WorkingDir: "{#UserData}"; Description: "{cm:StartNow}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox(FmtMessage(CustomMessage('UninstallNote'), [ExpandConstant('{#UserData}')]), mbInformation, MB_OK);
end;
