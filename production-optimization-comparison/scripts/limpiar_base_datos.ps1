$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendPath = Join-Path $ProjectRoot "backend"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    $VenvPython = Join-Path $BackendPath ".venv\Scripts\python.exe"
}

if (-not (Test-Path $VenvPython)) {
    $VenvPython = "python"
}

Push-Location $BackendPath
try {
    & $VenvPython manage.py limpiar_bd --yes
}
finally {
    Pop-Location
}
