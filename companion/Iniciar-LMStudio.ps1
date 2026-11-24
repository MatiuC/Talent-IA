param([string]$Web = 'http://localhost:8001')
$ErrorActionPreference = 'Stop'
try {
    $models = Invoke-RestMethod 'http://127.0.0.1:1234/api/v1/models' -TimeoutSec 5
    $loaded = @($models.models | Where-Object { $_.type -eq 'llm' -and $_.loaded_instances.Count -gt 0 })
    if ($loaded.Count -eq 0) { Write-Warning 'Carga un modelo de conversacion en LM Studio para analizar.' }
} catch { Write-Warning 'Activa el servidor de LM Studio en el puerto 1234 para analizar.' }
Write-Host 'Abre http://127.0.0.1:11435 cuando el asistente esté disponible. Mantén esta ventana abierta.'
python -u (Join-Path $PSScriptRoot 'asistente.py') --origin $Web
