$ErrorActionPreference = 'Stop'
$appRoot = $PSScriptRoot
$logRoot = Join-Path $appRoot 'logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

function Is-Listening([int]$port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

if (-not (Is-Listening 8000)) {
    $backendRoot = Join-Path $appRoot 'backend'
    Start-Process -FilePath (Join-Path $backendRoot '.venv/Scripts/python.exe') -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000') -WorkingDirectory $backendRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot 'api.stdout.log') -RedirectStandardError (Join-Path $logRoot 'api.stderr.log') | Out-Null
}

if (-not (Is-Listening 5173)) {
    $frontendRoot = Join-Path $appRoot 'frontend'
    $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
    $nodePath = if ($nodeCommand) { $nodeCommand.Source } else { Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe' }
    if (-not (Test-Path -LiteralPath $nodePath)) { throw 'Node.js was not found. Install Node.js or configure its path before starting the frontend.' }
    Start-Process -FilePath $nodePath -ArgumentList @('node_modules/vite/bin/vite.js', 'preview', '--host', '127.0.0.1', '--port', '5173', '--strictPort') -WorkingDirectory $frontendRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot 'frontend.stdout.log') -RedirectStandardError (Join-Path $logRoot 'frontend.stderr.log') | Out-Null
}

$ready = $false
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    try {
        $health = Invoke-RestMethod 'http://127.0.0.1:8000/api/health' -TimeoutSec 2
        if ($health.status -eq 'ok' -and (Is-Listening 5173)) { $ready = $true; break }
    } catch { }
    Start-Sleep -Seconds 1
}
if (-not $ready) { throw "The app did not become ready. Check the logs in $logRoot." }
Write-Output 'MLB Explorer is ready: http://127.0.0.1:5173/'
