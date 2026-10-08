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
; だれでも書きかえられないように、管理者・SYSTEM と、インストールした人だけが書きこめるようにする
; （インストールした人の権限は、下の [Code] の LockDataFolder で icacls を使ってつける）
Name: "{commonappdata}\GHMS"; Permissions: admins-full system-full
Name: "{commonappdata}\GHMS\data"; Permissions: admins-full system-full

[Files]
Source: "..\dist\GHMS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[INI]
; config.ini は Windows の標準（Shift-JIS／cp932）で書く。GHMS は UTF-8 → cp932 の順に読む。
; 手で直すときも、メモ帳で「ANSI」を選んで保存してください（UTF-8 と混ぜない）
; 更新のときは、すでに書いてある lan の値（手で変えた値も）を変えない（まだないときだけ書く）
Filename: "{commonappdata}\GHMS\config.ini"; Section: "server"; Key: "lan"; String: "1"; Tasks: lan; Flags: createkeyifdoesntexist
Filename: "{commonappdata}\GHMS\config.ini"; Section: "server"; Key: "lan"; String: "0"; Tasks: not lan; Flags: createkeyifdoesntexist

[Icons]
Name: "{group}\GHMS を開く"; Filename: "{app}\{#AppExe}"
Name: "{group}\GHMS を停止する"; Filename: "{app}\{#AppExe}"; Parameters: "--stop"
Name: "{group}\データのフォルダを開く"; Filename: "{commonappdata}\GHMS"
Name: "{group}\GHMS をアンインストール"; Filename: "{uninstallexe}"
Name: "{autodesktop}\GHMS"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{commonstartup}\GHMS"; Filename: "{app}\{#AppExe}"; Parameters: "--no-browser"; Tasks: startup

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""GHMS"""; Flags: runhidden; Tasks: lan
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""GHMS"" dir=in action=allow protocol=TCP localport={code:FirewallPort} profile=private,domain"; Flags: runhidden; Tasks: lan
; 普通のインストールはブラウザを開く。ネット経由の更新（/SILENT）では開かない（更新画面が自動で切り替わる）
Filename: "{app}\{#AppExe}"; Description: "GHMS を起動する"; Flags: nowait postinstall runasoriginaluser; Check: not WizardSilent
Filename: "{app}\{#AppExe}"; Parameters: "--no-browser"; Flags: nowait runasoriginaluser; Check: WizardSilent

[UninstallRun]
Filename: "{app}\{#AppExe}"; Parameters: "--stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopGHMS"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""GHMS"""; Flags: runhidden; RunOnceId: "DelFirewall"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
var
  OfficePage: TInputQueryWizardPage;

function ConfigFile(): String;
begin
  Result := ExpandConstant('{commonappdata}\GHMS\config.ini');
end;

// ファイアウォールで開けるポート。config.ini の [server] port（なければ・まちがっていれば 8000）
function FirewallPort(Param: String): String;
var
  P: Integer;
begin
  P := StrToIntDef(Trim(GetIniString('server', 'port', '8000', ConfigFile())), 8000);
  if (P < 1) or (P > 65535) then
    P := 8000;
  Result := IntToStr(P);
end;

procedure Icacls(Params: String);
var
  ResultCode: Integer;
begin
  if not Exec(ExpandConstant('{sys}\icacls.exe'), Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
    Log('icacls に失敗しました: ' + Params + ' (' + IntToStr(ResultCode) + ')');
end;

// データのフォルダ（ProgramData\GHMS）の権限：管理者・SYSTEM はすべて、インストールした人は変更（M）だけ。
// ほかの Windows ユーザーは読み書きできない（個人情報のため）。
// config.ini は管理者だけが書きかえられ、GHMS（ふつうのユーザー）は読むだけ。
// *S-1-5-32-544 = Administrators、*S-1-5-18 = SYSTEM、*S-1-5-32-545 = Users（日本語版Windowsでも同じ）
// 使う人は、はじめてインストールした人（config.ini の [install] user に覚える）。
// 更新のときに別の管理者のアカウントで「はい」を押しても、使う人は変わらない。
procedure LockDataFolder();
var
  Dir, Ini, User: String;
begin
  Dir := ExpandConstant('{commonappdata}\GHMS');
  Ini := ConfigFile();
  User := Trim(GetIniString('install', 'user', '', Ini));
  if User = '' then
  begin
    User := ExpandConstant('{username}');
    SetIniString('install', 'user', User, Ini);
  end;
  Icacls('"' + Dir + '" /inheritance:r /grant:r *S-1-5-32-544:(OI)(CI)F *S-1-5-18:(OI)(CI)F "' +
         User + ':(OI)(CI)M" /C /Q');
  // 前の版でつけていた「Users に変更を許す」を外す
  Icacls('"' + Dir + '" /remove:g *S-1-5-32-545 /C /Q');
  // 中のフォルダ・ファイルは、上の権限を引きつぐだけにする
  Icacls('"' + Dir + '\*" /reset /T /C /Q');
  if FileExists(Ini) then
    Icacls('"' + Ini + '" /inheritance:r /grant:r *S-1-5-32-544:F *S-1-5-18:F *S-1-5-32-545:R /C /Q');
end;

// はじめて入れるときだけ、事業所名・事業所番号をたずねる（最初の設定画面に入った状態で始まる）
procedure InitializeWizard();
begin
  OfficePage := CreateInputQueryPage(wpSelectTasks, '事業所の情報',
    'あなたの事業所の名前と番号を入れてください。',
    'インストール後に最初に開く「はじめての設定」に、この内容が入った状態で始まります（あとから変更できます）。');
  OfficePage.Add('事業所名:', False);
  OfficePage.Add('事業所番号（わからなければ空欄で大丈夫です）:', False);
  OfficePage.Values[0] := GetIniString('office', 'name', '', ConfigFile());
  OfficePage.Values[1] := GetIniString('office', 'no', '', ConfigFile());
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  // 更新（すでに入っている）ときは聞かない
  Result := (PageID = OfficePage.ID) and FileExists(ExpandConstant('{commonappdata}\GHMS\data\ghms.sqlite3'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if Trim(OfficePage.Values[0]) <> '' then
    begin
      SetIniString('office', 'name', Trim(OfficePage.Values[0]), ConfigFile());
      SetIniString('office', 'no', Trim(OfficePage.Values[1]), ConfigFile());
    end;
    // config.ini を書き終えてから権限をしめる
    LockDataFolder();
  end;
end;

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
