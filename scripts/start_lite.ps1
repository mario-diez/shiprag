# Arranque rápido perfil lite (PC casa - PowerShell)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path .venv)) {
  py -3 -m venv .venv
}
& .\.venv\Scripts\Activate.ps1
python -m pip install -q -U pip
pip install -q -e ".[dev]"

$env:SHIPRAG_PROFILE = "lite"
Write-Host "==> Ingesta sample (lite)"
shiprag --profile lite ingest data/sample
Write-Host "==> Smoke"
shiprag --profile lite smoke
Write-Host "==> UI en http://127.0.0.1:8080"
shiprag --profile lite serve --host 127.0.0.1 --port 8080
