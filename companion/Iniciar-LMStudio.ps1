param([string]$Web = 'http://localhost:8001')
$ErrorActionPreference = 'Stop'
$models = Invoke-RestMethod 'http://127.0.0.1:1234/v1/models' -TimeoutSec 5
if (-not ($models.data | Where-Object id -eq 'qwen3.5-2b')) { throw 'Carga qwen3.5-2b en LM Studio y activa su servidor en el puerto 1234.' }
Write-Host 'Abre http://127.0.0.1:11435 cuando el asistente esté disponible. Mantén esta ventana abierta.'
python -u (Join-Path $PSScriptRoot 'asistente.py') --origin $Web
