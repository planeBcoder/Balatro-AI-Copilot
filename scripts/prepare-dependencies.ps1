param([string]$Cache)
$ErrorActionPreference='Stop'
$project=Split-Path -Parent $PSScriptRoot
$lock=Get-Content -LiteralPath (Join-Path $project 'dependencies.lock.json') -Raw | ConvertFrom-Json
$target=Join-Path $project 'dependencies'
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -LiteralPath (Join-Path $project 'dependencies.lock.json') -Destination $target -Force
foreach($name in @('lovely','steamodded')) {
    $entry=$lock.$name
    $path=Join-Path $target $entry.filename
    if(-not(Test-Path -LiteralPath $path)) {
        $cached=if($Cache){Join-Path $Cache $entry.filename}else{''}
        if($cached -and (Test-Path -LiteralPath $cached)) {Copy-Item -LiteralPath $cached -Destination $path}
        else {Invoke-WebRequest -Uri $entry.url -OutFile $path}
    }
    if((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256) {
        throw "Dependency checksum mismatch: $name. Nothing will be installed."
    }
}
