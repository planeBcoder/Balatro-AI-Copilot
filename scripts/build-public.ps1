param([Parameter(Mandatory=$true)][string]$Python,[string]$Cache,[string]$Compiler)
$ErrorActionPreference='Stop'
$project=Split-Path -Parent $PSScriptRoot
$lock=Get-Content -LiteralPath (Join-Path $project 'dependencies.lock.json') -Raw | ConvertFrom-Json
# Inno's compressor has legacy MAX_PATH constraints. A short temporary build
# directory avoids failures under long project paths and is never recursively deleted.
$session=Join-Path ([IO.Path]::GetTempPath()) ('bc-'+[guid]::NewGuid().ToString('N').Substring(0,8))
$payload=Join-Path $session 'payload'
$public=Join-Path $project 'dist-public'
$dependencyCache=if($Cache){$Cache}else{Join-Path $session 'cache'}
New-Item -ItemType Directory -Force -Path $session,$payload,$public,$dependencyCache | Out-Null
& (Join-Path $PSScriptRoot 'prepare-dependencies.ps1') -Cache $dependencyCache

function FetchVerified($entry) {
    $path=Join-Path $dependencyCache $entry.filename
    if(-not(Test-Path -LiteralPath $path)){Invoke-WebRequest -Uri $entry.url -OutFile $path}
    if((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256){throw ('Checksum mismatch: '+$entry.filename)}
    return $path
}

$sources=Join-Path $session 'third-party-sources'
New-Item -ItemType Directory -Force -Path $sources | Out-Null
foreach($name in @('qtbase_source','qtsvg_source','pyside_source')) {
    $path=FetchVerified $lock.$name
    Copy-Item -LiteralPath $path -Destination $sources
}
Copy-Item -LiteralPath (Join-Path $project 'dependencies.lock.json') -Destination $sources
Copy-Item -LiteralPath (Join-Path $project 'THIRD_PARTY_NOTICES.md') -Destination $sources
if(-not $Compiler) {
    $compilerInstaller=FetchVerified $lock.compiler
    if((Get-AuthenticodeSignature -LiteralPath $compilerInstaller).Status -ne 'Valid'){throw 'Compiler vendor signature is invalid.'}
    $compilerDir=Join-Path $session 'inno'
    $p=Start-Process -FilePath $compilerInstaller -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER',('/DIR="'+$compilerDir+'"')) -WindowStyle Hidden -Wait -PassThru
    if($p.ExitCode -ne 0){throw 'Compiler setup failed.'}
    $Compiler=Join-Path $compilerDir 'ISCC.exe'
}
Push-Location -LiteralPath $project
try {
    & $Python -m pytest tests -q --disable-warnings
    if($LASTEXITCODE -ne 0){throw 'Tests failed; publishing aborted.'}
    & $Python -m PyInstaller --noconfirm --distpath (Join-Path $session 'dist') --workpath (Join-Path $session 'build') BalatroAICopilot.spec
    if($LASTEXITCODE -ne 0){throw 'Packaging failed.'}
    $built=Join-Path $session 'dist\BalatroAICopilot'
    Copy-Item -LiteralPath (Join-Path $built 'BalatroAICopilot.exe') -Destination $payload
    Copy-Item -LiteralPath (Join-Path $built '_internal') -Destination $payload -Recurse
    foreach($name in @('README.md','LICENSE','THIRD_PARTY_NOTICES.md','PRIVACY.md','SECURITY.md','CHANGELOG.md')) {
        Copy-Item -LiteralPath (Join-Path $project $name) -Destination $payload
    }
    Copy-Item -LiteralPath (Join-Path $project 'licenses') -Destination $payload -Recurse
    $licenseTarget=Join-Path $payload 'licenses'
    $runtime=& $Python -c 'import json,sys,sysconfig; print(json.dumps({"base":sys.base_prefix,"site":sysconfig.get_paths()["purelib"]}))' | ConvertFrom-Json
    Copy-Item -LiteralPath (Join-Path $runtime.base 'LICENSE.txt') -Destination (Join-Path $licenseTarget 'Python-LICENSE.txt')
    foreach($info in (Get-ChildItem -LiteralPath $runtime.site -Directory -Filter '*.dist-info')) {
        if($info.Name -match '^(pyside6|shiboken6|pydantic|pyinstaller|annotated_types|typing_extensions|typing_inspection)') {
            $licenses=Join-Path $info.FullName 'licenses'
            if(Test-Path -LiteralPath $licenses){Copy-Item -LiteralPath $licenses -Destination (Join-Path $licenseTarget $info.Name) -Recurse}
        }
    }
    $licenseSources=Join-Path $session 'license-sources'
    New-Item -ItemType Directory -Force -Path $licenseSources | Out-Null
    & tar -xf (Join-Path $sources 'qtbase-6.11.2.tar.gz') -C $licenseSources 'qtbase-6.11.2/LICENSES'
    if($LASTEXITCODE -ne 0){throw 'Cannot extract Qt license texts.'}
    Copy-Item -LiteralPath (Join-Path $licenseSources 'qtbase-6.11.2\LICENSES') -Destination (Join-Path $licenseTarget 'Qt-License-Texts') -Recurse
    $bundle=Start-Process -FilePath (Join-Path $payload 'BalatroAICopilot.exe') -ArgumentList @('--package-check',('"'+(Join-Path $session 'bundle-check.json')+'"')) -WindowStyle Hidden -Wait -PassThru
    if($bundle.ExitCode -ne 0 -or -not(Test-Path -LiteralPath (Join-Path $session 'bundle-check.json'))){throw 'Frozen application bundle check failed.'}
    & $Compiler ('/DPayload='+$payload) ('/DOutputDir='+$public) (Join-Path $project 'installer\copilot.iss')
    if($LASTEXITCODE -ne 0){throw 'Installer compilation failed.'}
    Compress-Archive -Path (Join-Path $payload '*') -DestinationPath (Join-Path $public 'Balatro-AI-Copilot-1.6.0-beta.1-Portable.zip') -Force
    Compress-Archive -Path (Join-Path $sources '*') -DestinationPath (Join-Path $public 'Balatro-AI-Copilot-ThirdPartySources.zip') -Force
    $lines=Get-ChildItem -LiteralPath $public -File | Where-Object Extension -In @('.exe','.zip') | Sort-Object Name | ForEach-Object { (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()+'  '+$_.Name }
    [IO.File]::WriteAllLines((Join-Path $public 'SHA256SUMS.txt'),$lines,[Text.UTF8Encoding]::new($false))
    & $Python (Join-Path $PSScriptRoot 'audit-public.py') $public
    if($LASTEXITCODE -ne 0){throw 'Public package audit failed.'}
    Write-Host ('Public release ready: '+$public)
} finally {Pop-Location}
