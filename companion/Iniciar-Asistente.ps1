param([string]$Web = 'http://localhost:8001')
$ErrorActionPreference = 'Stop'
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) { throw 'Instala Python 3 desde python.org y vuelve a abrir este asistente.' }
$ollamaExecutable = Join-Path $env:LOCALAPPDATA 'Programs/Ollama/ollama.exe'
$lmStudioReady = $false
try {
    $lmModels = Invoke-RestMethod -Uri 'http://127.0.0.1:1234/v1/models' -TimeoutSec 3
    $lmStudioReady = @($lmModels.data | Where-Object id -eq 'qwen3.5-2b').Count -gt 0
} catch { }
if ($lmStudioReady) { Write-Host 'Qwen ya esta disponible en LM Studio. Se reutilizara sin descargar Ollama.' }
if (-not $lmStudioReady -and -not (Test-Path -LiteralPath $ollamaExecutable)) {
    $installer = Join-Path $PSScriptRoot 'OllamaSetup.exe'
    Write-Host 'Descargando el instalador oficial de Ollama...'
    Invoke-WebRequest 'https://ollama.com/download/OllamaSetup.exe' -OutFile $installer
    $signature = Get-AuthenticodeSignature -LiteralPath $installer
    if ($signature.Status -ne 'Valid') { throw 'No se pudo validar la firma del instalador. No se ejecutara.' }
    Start-Process -FilePath $installer -ArgumentList '/VERYSILENT','/NORESTART' -WindowStyle Hidden -Wait
}
if (-not $lmStudioReady -and -not (Test-Path -LiteralPath $ollamaExecutable)) { throw 'Abre LM Studio con Qwen o instala Ollama desde ollama.com/download.' }
$env:OLLAMA_NO_CLOUD = '1'
if (-not $lmStudioReady) { Start-Process -FilePath $ollamaExecutable -ArgumentList 'serve' -WindowStyle Hidden }
Write-Host 'Cuando aparezca Asistente TalentIA disponible, abre http://127.0.0.1:11435 en tu navegador.'
Write-Host 'Manten esta ventana abierta. En TalentIA conecta la clave y pulsa Descargar Qwen.'
& $pythonCommand.Source (Join-Path $PSScriptRoot 'asistente.py') --origin $Web
