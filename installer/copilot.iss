#ifndef Payload
  #error Payload is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
[Setup]
AppId={{29044B32-4BE5-4E01-B496-9E744380D610}
AppName=Balatro AI Copilot
AppVersion=1.6.0-beta.1
AppPublisher=planeBcoder
AppPublisherURL=https://github.com/planeBcoder/Balatro-AI-Copilot
DefaultDirName={localappdata}\Programs\BalatroAICopilot
DefaultGroupName=Balatro AI Copilot
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=Balatro-AI-Copilot-1.6.0-beta.1-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
UninstallDisplayIcon={app}\BalatroAICopilot.exe
CloseApplications=no
SetupLogging=yes
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Balatro AI Copilot"; Filename: "{app}\BalatroAICopilot.exe"
Name: "{group}\Game setup and repair"; Filename: "{app}\BalatroAICopilot.exe"; Parameters: "--setup"
Name: "{group}\Experimental isolated trainer"; Filename: "{app}\BalatroAICopilot.exe"; Parameters: "--trainer"
Name: "{autodesktop}\Balatro AI Copilot"; Filename: "{app}\BalatroAICopilot.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\BalatroAICopilot.exe"; Parameters: "--setup"; Description: "Connect Balatro (game must be closed)"; Flags: postinstall nowait skipifsilent
[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer;
begin
  Result := '';
  if FileExists(ExpandConstant('{app}\BalatroAICopilot.exe')) then begin
    if not Exec(ExpandConstant('{app}\BalatroAICopilot.exe'), '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code) then Code := 1;
    if Code <> 0 then Result := 'The assistant is finishing an active request. Please retry shortly.';
  end;
end;

function InitializeUninstall(): Boolean;
var Code: Integer;
begin
  Result := True;
  if FileExists(ExpandConstant('{app}\BalatroAICopilot.exe')) then begin
    if not Exec(ExpandConstant('{app}\BalatroAICopilot.exe'), '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code) then Code := 1;
    if Code <> 0 then begin
      Result := False;
      MsgBox('The assistant is finishing an active request. Please retry shortly.', mbError, MB_OK);
      exit;
    end;
    if not Exec(ExpandConstant('{app}\BalatroAICopilot.exe'), '--uninstall-integration', '', SW_HIDE, ewWaitUntilTerminated, Code) then
      Code := 1;
    Result := Code = 0;
    if not Result then
      MsgBox('Please close Balatro and the AI assistant, then retry. Modified integration files are preserved and may need manual review. Your saves and shared mod loaders will not be removed.', mbError, MB_OK);
  end;
end;
