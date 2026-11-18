$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) { python -m venv .venv }
& ./.venv/Scripts/python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar las dependencias.' }
& ./.venv/Scripts/python.exe preparar_prueba.py
if ($LASTEXITCODE -ne 0) { throw 'No se pudo preparar el usuario de pruebas.' }
$env:DATABASE_PATH = 'data/pruebas.db'
$env:UPLOADS_DIR = 'data/pruebas-uploads'
$env:FORZAR_SIMULACION = 'true'
Write-Host 'Abre http://localhost:8001. En otra terminal ejecuta el asistente como indica README.md.'
& ./.venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8001
