$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $ProjectRoot ".venv/Scripts/python.exe"

& $Python -m ruff check (Join-Path $ProjectRoot "backend")
& $Python -m ruff format --check (Join-Path $ProjectRoot "backend")
Push-Location (Join-Path $ProjectRoot "backend")
try {
    & $Python -m mypy app
    & $Python -m pytest --cov-report= --cov-fail-under=0
    & $Python -m coverage run --append -m app.ingestion.smoke --provider fake
    & $Python -m coverage run --append -m app.rag.smoke --embedding-provider fake --llm-provider fake
    & $Python -m coverage report --fail-under=80
}
finally {
    Pop-Location
}
npm --prefix (Join-Path $ProjectRoot "frontend") run lint
npm --prefix (Join-Path $ProjectRoot "frontend") run type-check
npm --prefix (Join-Path $ProjectRoot "frontend") run test:coverage
npm --prefix (Join-Path $ProjectRoot "frontend") run build
