param(
    [Parameter(Mandatory=$true)][string]$BaseApp,
    [Parameter(Mandatory=$true)][string]$NewVersion,
    [string]$Config
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo
try {
    if (git status --porcelain) { throw 'Lokale Aenderungen zuerst committen oder sichern.' }
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw 'Git-Aktualisierung fehlgeschlagen.' }
    $python = Join-Path $repo '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Entwicklungsumgebung zuerst nach README einrichten.' }
    & $python -m pip install -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw 'Entwicklungsabhaengigkeiten konnten nicht aktualisiert werden.' }
    & $python -m pytest tests -q -p no:cacheprovider
    if ($LASTEXITCODE -ne 0) { throw 'Regressionstests fehlgeschlagen; kein Build.' }
    $argsBuild = @('tools/build_extension.py', '--source', $BaseApp, '--target', (Join-Path $NewVersion 'App'))
    if ($Config) { $argsBuild += @('--config', $Config) }
    & $python @argsBuild
    if ($LASTEXITCODE -ne 0) { throw 'Build fehlgeschlagen.' }
    & $python tools/verify_release.py (Join-Path $NewVersion 'App')
    if ($LASTEXITCODE -ne 0) { throw 'Paketpruefung fehlgeschlagen.' }
    $testOutput = Join-Path $repo ('diagnostics\update-test-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss'))
    $exe = Join-Path $NewVersion 'App\AtracsysLiveTracker_Messskripte.exe'
    $arguments = '--sync-self-test "' + $testOutput + '"'
    $p = Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory (Split-Path $exe) -WindowStyle Hidden -Wait -PassThru
    $resultPath = Join-Path $testOutput 'result.json'
    if ($p.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $resultPath)) { throw 'GUI-Selbsttest fehlgeschlagen.' }
    if (-not (Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json).passed) { throw 'GUI-Selbsttest nicht bestanden.' }
    Write-Output ('Neue Version geprueft: ' + $exe)
} finally { Pop-Location }
