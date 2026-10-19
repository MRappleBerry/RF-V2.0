$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
$scriptPath = Join-Path $PSScriptRoot 'start_redflagged_tunnel.py'

if (-not (Test-Path $pythonPath)) {
    throw "Python virtual environment was not found at $pythonPath"
}

& $pythonPath $scriptPath @args