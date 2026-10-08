; GHMS グループホーム業務管理 インストーラー（Inno Setup 6）
; ビルド: iscc /DAppVersion=1.0.0 packaging\installer.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "GHMS グループホーム業務管理"
#define AppExe "GHMS.exe"

[Setup]
AppId={{6E1E2B57-4C0A-4E1B-9A4F-5B0D7C9A31A2}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=GHMS
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\GHMS
DefaultGroupName=GHMS グループホーム業務管理
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=GHMS-Setup-{#AppVersion}
SetupIconFile=ghms.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
UsePreviousTasks=yes

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "デスクトップにアイコンを作る"; GroupDescription: "アイコン:"
Name: "startup"; Description: "Windowsの起動時にGHMSを自動で起動する（サーバーのPCにおすすめ）"; GroupDescription: "起動:"
Name: "lan"; Description: "事業所内の他のPC・タブレットからも使う（ファイアウォールを許可）"; GroupDescription: "ネットワーク:"; Flags: unchecked

[Dirs]
; データは Program Files ではなく ProgramData に置く（更新・アンインストールで消えない）
Name: "{commonappdata}\GHMS"; Permissions: users-modify
Name: "{commonappdata}\GHMS\data"; Permissions: users-modify

[Files]
Source: "..\dist\GHMS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[INI]
Filename: "{commonappdata}\GHMS\config.ini"; Section: "server"; Key: "lan"; String: "1"; Tasks: lan
Filename: "{commonappdata}\GHMS\config.ini"; Section: "server"; Key: "lan"; String: "0"; Tasks: not lan

[Icons]
Name: "{group}\GHMS を開く"; Filename: "{app}\{#AppExe}"
Name: "{group}\GHMS を停止する"; Filename: "{app}\{#AppExe}"; Parameters: "--stop"
Name: "{group}\データのフォルダを開く"; Filename: "{commonappdata}\GHMS"
Name: "{group}\GHMS をアンインストール"; Filename: "{uninstallexe}"
Name: "{autodesktop}\GHMS"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{commonstartup}\GHMS"; Filename: "{app}\{#AppExe}"; Parameters: "--no-browser"; Tasks: startup

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""GHMS"""; Flags: runhidden; Tasks: lan
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""GHMS"" dir=in action=allow protocol=TCP localport=8000 profile=private,domain"; Flags: runhidden; Tasks: lan
; 普通のインストールはブラウザを開く。ネット経由の更新（/SILENT）では開かない（更新画面が自動で切り替わる）
Filename: "{app}\{#AppExe}"; Description: "GHMS を起動する"; Flags: nowait postinstall runasoriginaluser; Check: not WizardSilent
Filename: "{app}\{#AppExe}"; Parameters: "--no-browser"; Flags: nowait runasoriginaluser; Check: WizardSilent

[UninstallRun]
Filename: "{app}\{#AppExe}"; Parameters: "--stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopGHMS"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""GHMS"""; Flags: runhidden; RunOnceId: "DelFirewall"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
// 入れ替える前に、動いているGHMSを止める
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  if FileExists(ExpandConstant('{app}\{#AppExe}')) then
    Exec(ExpandConstant('{app}\{#AppExe}'), '--stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox('GHMS をアンインストールしました。' #13#10 +
           '入居者などのデータは消さずに残しています：' #13#10 + ExpandConstant('{commonappdata}\GHMS'), mbInformation, MB_OK);
end;
