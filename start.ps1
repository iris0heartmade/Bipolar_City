# Generic BIPOLE launcher (PowerShell, UTF-8 native).
# For the industry web game, prefer start_industry.ps1.

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m backend.app
