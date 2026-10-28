# Local synthetic rescue demo only. Run from any directory; existing listeners are left untouched.
$ErrorActionPreference = 'Stop'
$env:RENGU_DS_LOCAL_CONNECT = '1'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$local = Join-Path $root '.local-rescue'
if (-not (Test-Path (Join-Path $local 'event-after-id.txt'))) { throw 'Local rescue demo has not been configured.' }
function Start-DemoProcess($arguments, $name) {
    Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $local "$name.log") -RedirectStandardError (Join-Path $local "$name-error.log")
}
$running = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' })
if (-not (Get-NetTCPConnection -LocalPort 8443 -State Listen -ErrorAction SilentlyContinue)) {
    Start-DemoProcess @('-m', 'scripts.local_rescue_receiver', '--directory', $local) 'receiver'
}
if (-not (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) {
    Start-DemoProcess @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--env-file', '.env') 'api'
}
if (-not ($running.CommandLine -match '-m scripts.rescue_demo_dispatch')) {
    Start-DemoProcess @('-m', 'scripts.rescue_demo_dispatch') 'dispatch'
}
if (-not ($running.CommandLine -match '-m scripts.rescue_demo_events')) {
    Start-DemoProcess @('-m', 'scripts.rescue_demo_events') 'event'
}
