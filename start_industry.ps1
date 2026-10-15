# BIPOLE industry web launcher (PowerShell, UTF-8 native)
# Place next to backend/ and double-click or run from PowerShell.

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }

$env:PYTHONPATH = $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'

try {
    & $py -c "import fastapi, uvicorn" | Out-Null
} catch {
    Write-Host '[x] Missing dependencies. Run: pip install -r requirements.txt' -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}

Write-Host 'BIPOLE industry web server starting...' -ForegroundColor Cyan
Write-Host 'Open http://127.0.0.1:8765/ in your browser.' -ForegroundColor Cyan
Write-Host 'Close this window to stop the server.' -ForegroundColor DarkGray

& $py -m backend.app
Read-Host 'Press Enter to close'
