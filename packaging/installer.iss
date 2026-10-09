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
; 設定（config.ini）・ログは ProgramData\GHMS に、データはインストールのときに選んだフォルダ
; （初めは ProgramData\GHMS\data）に置く。Program Files ではないので、更新・アンインストールで消えない
; だれでも書きかえられないように、管理者・SYSTEM と、インストールした人だけが書きこめるようにする
; （インストールした人の権限は、下の [Code] の LockDataFolder で icacls を使ってつける）
Name: "{commonappdata}\GHMS"; Permissions: admins-full system-full; Flags: uninsneveruninstall
Name: "{commonappdata}\GHMS\data"; Permissions: admins-full system-full; Flags: uninsneveruninstall; Check: UsingDefaultData

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
Name: "{group}\GHMS をブラウザで開く"; Filename: "{app}\{#AppExe}"; Parameters: "--browser"
Name: "{group}\GHMS を停止する"; Filename: "{app}\{#AppExe}"; Parameters: "--stop"
Name: "{group}\データのフォルダを開く"; Filename: "{code:DataDir}"
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
  DataPage: TInputDirWizardPage;
  Upgrading: Boolean;   // すでにデータがある（更新・入れ直し）：データの場所は変えない

function GetDriveTypeW(lpRootPathName: String): Cardinal; external 'GetDriveTypeW@kernel32.dll stdcall';

function ConfigFile(): String;
begin
  Result := ExpandConstant('{commonappdata}\GHMS\config.ini');
end;

function DefaultDataDir(): String;
begin
  Result := ExpandConstant('{commonappdata}\GHMS\data');
end;

// config.ini に書いてあるデータの場所（なければ初めの場所）
function ConfiguredDataDir(): String;
begin
  Result := RemoveBackslashUnlessRoot(Trim(GetIniString('data', 'dir', '', ConfigFile())));
  if Result = '' then
    Result := DefaultDataDir();
end;

// これから使うデータの場所
function ChosenDataDir(): String;
begin
  if Upgrading then
    Result := ConfiguredDataDir()
  else
    Result := RemoveBackslashUnlessRoot(Trim(DataPage.Values[0]));
end;

function DataDir(Param: String): String;
begin
  Result := ChosenDataDir();
end;

function UsingDefaultData(): Boolean;
begin
  Result := CompareText(ChosenDataDir(), DefaultDataDir()) = 0;
end;

function StartsWithDir(Path, Dir: String): Boolean;
begin
  Path := AddBackslash(Lowercase(Path));
  Dir := AddBackslash(Lowercase(RemoveBackslashUnlessRoot(Dir)));
  Result := Copy(Path, 1, Length(Dir)) = Dir;
end;

function DirIsEmpty(Dir: String): Boolean;
var
  F: TFindRec;
begin
  Result := True;
  if FindFirst(AddBackslash(Dir) + '*', F) then
  try
    repeat
      if (F.Name <> '.') and (F.Name <> '..') then
      begin
        Result := False;
        Exit;
      end;
    until not FindNext(F);
  finally
    FindClose(F);
  end;
end;

// データの場所として使えないとき、その理由（使えるときは空）
function BadDataDir(D: String): String;
begin
  Result := '';
  if (Length(D) < 3) or (D[2] <> ':') or (D[3] <> '\') then
    Result := '「D:\GHMSデータ」のように、このPCのドライブから書いてください。（ネットワーク上のフォルダには置けません。データがこわれるおそれがあります）'
  else if GetDriveTypeW(Copy(D, 1, 3)) = 4 then
    Result := 'ネットワークドライブには置けません（データがこわれるおそれがあります）。このPCのドライブを選び、ネットワークはバックアップの2か所目に使ってください。'
  else if StartsWithDir(D, WizardDirValue()) then
    Result := 'プログラムのフォルダの中には置けません（更新・アンインストールで消えるため）。'
  else if StartsWithDir(D, ExpandConstant('{win}')) or StartsWithDir(D, ExpandConstant('{commonpf}')) or StartsWithDir(D, ExpandConstant('{commonpf32}')) then
    Result := 'Windows や Program Files のフォルダの中には置けません。';
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
procedure LockOne(Dir, User: String);
begin
  Icacls('"' + Dir + '" /inheritance:r /grant:r *S-1-5-32-544:(OI)(CI)F *S-1-5-18:(OI)(CI)F "' +
         User + ':(OI)(CI)M" /C /Q');
  // 前の版でつけていた「Users に変更を許す」を外す
  Icacls('"' + Dir + '" /remove:g *S-1-5-32-545 /C /Q');
  // 中のフォルダ・ファイルは、上の権限を引きつぐだけにする
  Icacls('"' + Dir + '\*" /reset /T /C /Q');
end;

procedure LockDataFolder();
var
  Dir, Ini, User, Data: String;
begin
  Dir := ExpandConstant('{commonappdata}\GHMS');
  Ini := ConfigFile();
  User := Trim(GetIniString('install', 'user', '', Ini));
  if User = '' then
  begin
    User := ExpandConstant('{username}');
    SetIniString('install', 'user', User, Ini);
  end;
  LockOne(Dir, User);
  // 別の場所に置いたデータのフォルダも同じようにしめる（ドライブのいちばん上は選べないようにしてある）
  Data := ChosenDataDir();
  if (not StartsWithDir(Data, Dir)) and DirExists(Data) and (Length(Data) > 3) then
    LockOne(Data, User);
  if FileExists(Ini) then
    Icacls('"' + Ini + '" /inheritance:r /grant:r *S-1-5-32-544:F *S-1-5-18:F *S-1-5-32-545:R /C /Q');
end;

// はじめて入れるときだけ、事業所名・事業所番号をたずねる（最初の設定画面に入った状態で始まる）
procedure InitializeWizard();
var
  Def: String;
begin
  Upgrading := (Trim(GetIniString('data', 'dir', '', ConfigFile())) <> '') or
               FileExists(AddBackslash(ConfiguredDataDir()) + 'ghms.sqlite3');
  DataPage := CreateInputDirPage(wpSelectDir, 'データの保存場所',
    '入居者・職員などのデータを保存するフォルダを選んでください。',
    'ふつうはこのままで大丈夫です。別のドライブ（D: など）に置きたいときだけ変えてください。' #13#10 +
    '・USBメモリやネットワーク上のフォルダは選べません（データがこわれる・なくすおそれ）。それらはバックアップの保存先に使います。' #13#10 +
    '・更新やアンインストールをしても、このフォルダのデータは消えません。',
    False, 'GHMSデータ');
  DataPage.Add('データのフォルダ:');
  Def := Trim(ExpandConstant('{param:DATADIR|}'));
  if Def = '' then
    Def := DefaultDataDir();
  DataPage.Values[0] := Def;
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
  // 更新（すでにデータがある）ときは聞かない。データの場所も変えない
  Result := Upgrading and ((PageID = OfficePage.ID) or (PageID = DataPage.ID));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  D, Why: String;
  T: Cardinal;
begin
  Result := True;
  if (DataPage = nil) or (CurPageID <> DataPage.ID) then
    Exit;
  D := RemoveBackslashUnlessRoot(Trim(DataPage.Values[0]));
  Why := BadDataDir(D);
  if Why <> '' then
  begin
    MsgBox(Why, mbError, MB_OK);
    Result := False;
    Exit;
  end;
  T := GetDriveTypeW(Copy(D, 1, 3));
  if (T = 2) and (MsgBox('取り外せるドライブ（USBメモリなど）です。データの保存場所にはおすすめしません（はずすと使えない・なくすおそれ）。' #13#10 +
                         'USBメモリはバックアップの保存先に使ってください。それでもここに保存しますか？', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) <> IDYES) then
  begin
    Result := False;
    Exit;
  end;
  // ドライブのいちばん上や、ほかのファイルがあるフォルダには、中に「GHMSデータ」を作る（ほかのファイルの権限を変えないため）
  if (Length(D) = 3) or (DirExists(D) and (not FileExists(AddBackslash(D) + 'ghms.sqlite3')) and (not DirIsEmpty(D))) then
  begin
    D := AddBackslash(D) + 'GHMSデータ';
    MsgBox('選んだ場所にはほかのファイルがある（またはドライブのいちばん上な）ので、その中に次のフォルダを作って保存します：' #13#10 + D, mbInformation, MB_OK);
    DataPage.Values[0] := D;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo + NewLine + NewLine + 'データの保存場所:' + NewLine + Space + ChosenDataDir();
  if Upgrading then
    Result := Result + NewLine + Space + '（いまのデータをそのまま使います。入れかえる前に、自動でバックアップします）';
  if MemoTasksInfo <> '' then
    Result := Result + NewLine + NewLine + MemoTasksInfo;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // データの場所は、ファイルを入れる前に書いておく（入れたあとすぐ起動するGHMSが、まちがいなく読むように）
  if (CurStep = ssInstall) and not Upgrading then
  begin
    ForceDirectories(ExpandConstant('{commonappdata}\GHMS'));
    ForceDirectories(ChosenDataDir());
    SetIniString('data', 'dir', ChosenDataDir(), ConfigFile());
  end;
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

// 入れ替える前に、動いているGHMSを止めて、データをバックアップする（作れなければインストールしない）
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  Dir, Db, Bk, Name: String;
begin
  Result := '';
  if not Upgrading then
  begin
    // 画面を出さないインストール（/DATADIR=）のときも、使えない場所は止める。
    // ドライブのいちばん上・ほかのファイルがあるフォルダなら、中に「GHMSデータ」を作る
    Dir := ChosenDataDir();
    if (Length(Dir) = 3) or (DirExists(Dir) and (not FileExists(AddBackslash(Dir) + 'ghms.sqlite3')) and (not DirIsEmpty(Dir))) then
      DataPage.Values[0] := AddBackslash(Dir) + 'GHMSデータ';
    Result := BadDataDir(ChosenDataDir());
    if Result <> '' then
      Result := 'データの保存場所に使えません：' + ChosenDataDir() + #13#10 + Result;
    Exit;
  end;
  if FileExists(ExpandConstant('{app}\{#AppExe}')) then
    Exec(ExpandConstant('{app}\{#AppExe}'), '--stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Dir := ConfiguredDataDir();
  Db := AddBackslash(Dir) + 'ghms.sqlite3';
  if FileExists(Db) then
  begin
    Bk := AddBackslash(Dir) + 'backup\before_install';
    Name := Bk + '\ghms_' + GetDateTimeString('yyyymmdd_hhnnss', #0, #0) + '_to_{#AppVersion}.sqlite3';
    if (not ForceDirectories(Bk)) or (not FileCopy(Db, Name, False)) then
      Result := '入れかえる前のデータのバックアップを作れなかったため、インストールを中止しました（データはそのままです）。' #13#10 +
                'ディスクの空きを確かめてから、もう一度インストールしてください。' #13#10 + Db
    else
      Log('バックアップしました: ' + Name);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox('GHMS をアンインストールしました。' #13#10 +
           '入居者などのデータは消さずに残しています：' #13#10 + ConfiguredDataDir() + #13#10 +
           'もう一度インストールすると、このデータをそのまま使います。', mbInformation, MB_OK);
end;
