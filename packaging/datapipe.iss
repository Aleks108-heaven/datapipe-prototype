; Windows installer for datapipe (Inno Setup 6). It wraps the program folder built by  packaging/build.py --onedir.
; Build it with:  python packaging/build_installer.py        (or: ISCC /DAppVersion=0.2.0 packaging\datapipe.iss)
;
; Installs for ALL users into Program Files (%ProgramFiles%\datapipe) and asks for administrator rights (the User Account Control prompt).
; Program Files is the folder Windows protects: only an administrator can add to or change what is in it, so a program or script running
; as an ordinary user cannot swap datapipe.exe for something else. Where administrator rights are not available:  datapipe-setup.exe /CURRENTUSER
; installs for the current user only, into %LOCALAPPDATA%\Programs\datapipe.
; A copy installed for the current user by an earlier version is removed when this one is installed for all users (the Start menu would
; otherwise show datapipe twice).
; Your files (~\datapipe\files) and results (~\datapipe\work) are NOT part of the installation, so uninstalling never deletes them.
; The wizard is in English or Ukrainian: it follows the language of Windows, and asks only when Windows uses neither.
; The installer is not code-signed, so Windows shows "Unknown publisher" (SmartScreen: More info, then Run anyway). Signing needs a
; certificate that someone has to pay for; it is the one thing that would remove that message.

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
AppSupportURL=https://github.com/Aleks108-heaven/datapipe-prototype/issues
AppUpdatesURL=https://github.com/Aleks108-heaven/datapipe-prototype/releases
AppComments=Clean and check data files, on this computer
VersionInfoVersion={#AppVersion}
VersionInfoCompany=datapipe-prototype
VersionInfoDescription=datapipe setup
VersionInfoProductName=datapipe
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=datapipe-setup-{#AppVersion}
SetupIconFile=icons\datapipe.ico
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
english.WelcomeLabel2=This installs [name/ver] for all users of this computer, in the Program Files folder, where Windows protects it from being changed by anything that does not have administrator rights.%n%ndatapipe cleans and checks data files and keeps everything on this computer: the program opens in your web browser, but nothing is sent anywhere.%n%nThis installer is not signed, so Windows may call the publisher "unknown". That is expected.
english.FinishedLabel=datapipe is installed. Start it from the Start menu. Put your data files in the folder  %USERPROFILE%\datapipe\files  or choose a file from anywhere inside the app.
ukrainian.WelcomeLabel2=Ця програма встановить [name/ver] для всіх користувачів цього комп’ютера, у папку Program Files, де Windows захищає її від змін з боку всього, що не має прав адміністратора.%n%ndatapipe очищає та перевіряє файли з даними й залишає все на цьому комп’ютері: програма відкривається у вашому браузері, але нічого нікуди не надсилається.%n%nЦей інсталятор не підписано, тому Windows може назвати видавця “невідомим”. Це очікувано.
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
; The folder the program reads by default, made here for the current user only: for all users the program makes it for whoever starts it
; (an administrator account that approved the installation is not necessarily the person who will use it). Kept on uninstall.
Name: "{#UserData}\files"; Flags: uninsneveruninstall; Check: not IsAdminInstallMode

[Files]
Source: "..\dist\onedir\datapipe\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; The working folder is the home folder, which exists for everyone (~\datapipe may not, for a user who never started the program).
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{%USERPROFILE}"; Comment: "{cm:AppComment}"
Name: "{autoprograms}\{cm:FilesFolder}"; Filename: "{#UserData}\files"; Comment: "{cm:FilesFolderComment}"; Check: not IsAdminInstallMode
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{%USERPROFILE}"; Tasks: desktopicon

[Run]
; runasoriginaluser: the setup is elevated, but the program must start as the person who is going to use it, not as the administrator.
Filename: "{app}\{#AppExe}"; WorkingDir: "{%USERPROFILE}"; Description: "{cm:StartNow}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
const
  PerUserKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{7EBB069F-1951-4BBD-B2F4-5CC43237849C}_is1';

{ A copy installed for the current user by an earlier version is removed first: two copies would show two Start menu entries, and the
  older one would keep starting. Only done when installing for all users. }
procedure RemovePerUserInstall();
var
  Cmd: String;
  Code: Integer;
begin
  if not IsAdminInstallMode() then Exit;
  if RegQueryStringValue(HKCU, PerUserKey, 'UninstallString', Cmd) then
  begin
    Cmd := RemoveQuotes(Cmd);
    if FileExists(Cmd) then
      Exec(Cmd, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, Code);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then RemovePerUserInstall();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox(FmtMessage(CustomMessage('UninstallNote'), [ExpandConstant('{#UserData}')]), mbInformation, MB_OK);
end;
