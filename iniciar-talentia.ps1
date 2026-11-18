[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8001,

    [ValidateSet('127.0.0.1', 'localhost', '0.0.0.0')]
    [string]$ListenAddress = '127.0.0.1'
)

$ErrorActionPreference = 'Stop'
$projectDirectory = $PSScriptRoot
$pythonExecutable = Join-Path $projectDirectory '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw 'No se encontro el entorno .venv. Sigue la instalacion inicial del README antes de iniciar TalentIA.'
}

Write-Host ''
Write-Host 'TalentIA - Seleccion inteligente de personal' -ForegroundColor Cyan
Write-Host "Al terminar el arranque, abre http://localhost:$Port"
Write-Host 'Manten esta ventana abierta. Pulsa Ctrl+C para detener este servidor.'
if ($ListenAddress -eq '0.0.0.0') {
    Write-Host "Acceso de red habilitado en el puerto $Port. La primera cuenta se crea desde localhost."
}
Write-Host ''

Push-Location -LiteralPath $projectDirectory
try {
    & $pythonExecutable -m uvicorn backend.main:app --host $ListenAddress --port $Port
    $serverExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($serverExitCode -and $serverExitCode -ne 0) {
    throw "TalentIA no pudo iniciar o se detuvo con un error (codigo $serverExitCode). Revisa los mensajes anteriores. Si el puerto esta ocupado, usa -Port 8002."
}
