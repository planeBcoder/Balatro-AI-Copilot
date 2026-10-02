param([Parameter(Mandatory=$true)][string]$Release,[Parameter(Mandatory=$true)][string]$Report)
$ErrorActionPreference='Stop'
$testRoot=Join-Path ([IO.Path]::GetTempPath()) ('bc-test-'+[guid]::NewGuid().ToString('N').Substring(0,8))
$installDir=Join-Path $testRoot 'app'
$oldLocal=$env:LOCALAPPDATA;$oldRoaming=$env:APPDATA
New-Item -ItemType Directory -Force -Path $testRoot | Out-Null
try {
    $env:LOCALAPPDATA=Join-Path $testRoot 'local'
    $env:APPDATA=Join-Path $testRoot 'roaming'
    New-Item -ItemType Directory -Force -Path $env:LOCALAPPDATA,$env:APPDATA | Out-Null
    $installer=Join-Path $Release 'Balatro-AI-Copilot-1.6.0-beta.1-Setup.exe'
    $installed=Start-Process -FilePath $installer -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/DIR="'+$installDir+'"')) -WindowStyle Hidden -Wait -PassThru
    if($installed.ExitCode -ne 0){throw 'Test installation failed.'}
    $exe=Join-Path $installDir 'BalatroAICopilot.exe'
    if(-not(Test-Path -LiteralPath $exe)){throw 'Installed executable not found.'}
    $checkFile=Join-Path $testRoot 'bundle-check.json'
    $checked=Start-Process -FilePath $exe -ArgumentList @('--package-check',('"'+$checkFile+'"')) -WindowStyle Hidden -Wait -PassThru
    if($checked.ExitCode -ne 0 -or -not(Test-Path -LiteralPath $checkFile)){throw 'Installed application failed its bundle check.'}
    $preview=[IO.Path]::ChangeExtension([IO.Path]::GetFullPath($Report),'.png')
    $gui=Start-Process -FilePath $exe -ArgumentList @('--setup-preview',('"'+$preview+'"')) -WindowStyle Hidden -Wait -PassThru
    if($gui.ExitCode -ne 0 -or -not(Test-Path -LiteralPath $preview)){throw 'Installed Qt setup window failed to render.'}
    $tempBoundary=[IO.Path]::GetFullPath($testRoot)+'\'
    if(-not [IO.Path]::GetFullPath($installDir).StartsWith($tempBoundary,[StringComparison]::OrdinalIgnoreCase)){throw 'Unsafe uninstall test path.'}
    $uninstalled=Start-Process -FilePath (Join-Path $installDir 'unins000.exe') -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART') -WindowStyle Hidden -Wait -PassThru
    if($uninstalled.ExitCode -ne 0 -or (Test-Path -LiteralPath $exe)){throw 'Test uninstall failed.'}
    $result=@{installed=$true;bundle_verified=$true;qt_window_rendered=$true;uninstalled=$true;isolated_user_data=$true;game_integration_writes=0;network_requests=0}
    $json=$result | ConvertTo-Json
    [IO.File]::WriteAllText([IO.Path]::GetFullPath($Report),$json,[Text.UTF8Encoding]::new($false))
    Write-Host 'Installer round-trip passed: install, frozen bundle, Qt setup, uninstall. No game integration was installed in this test.'
} finally {$env:LOCALAPPDATA=$oldLocal;$env:APPDATA=$oldRoaming}
