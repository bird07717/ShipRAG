$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $ProjectRoot ".venv/Scripts/python.exe"

if (-not (Test-Path $VenvPython)) {
    python -m venv (Join-Path $ProjectRoot ".venv")
}

& $VenvPython -m pip install "pip==25.2"
& $VenvPython -m pip install -r (Join-Path $ProjectRoot "backend/requirements.lock")
& $VenvPython -m pip install --no-deps -e (Join-Path $ProjectRoot "backend")
npm --prefix (Join-Path $ProjectRoot "frontend") ci

$EnvPath = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $EnvPath)) {
    Copy-Item (Join-Path $ProjectRoot ".env.example") $EnvPath
}

Write-Host "Bootstrap complete. Review .env before starting services."
