[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8001,

    [ValidateSet('127.0.0.1', 'localhost', '0.0.0.0')]
    [string]$ListenAddress = '127.0.0.1',

    [string]$Modelo = '',

    [switch]$RestaurarLimpia,

    [switch]$SinAbrirNavegador
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Test-LocalPort {
    param([int]$TargetPort)
    return [bool](Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue)
}

function Get-LmStudioModels {
    return Invoke-RestMethod 'http://127.0.0.1:1234/api/v1/models' -TimeoutSec 4
}

$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$requirementsPath = Join-Path $PSScriptRoot 'requirements.txt'
$dbPath = Join-Path $PSScriptRoot 'data\pruebas.db'
$cleanBackupPath = Join-Path $PSScriptRoot 'data\pruebas-presentacion-limpia.db'
$uploadsPath = Join-Path $PSScriptRoot 'data\pruebas-uploads'
$assistantScript = Join-Path $PSScriptRoot 'companion\asistente.py'

Write-Host ''
Write-Host 'TalentIA - Inicio completo de demostracion en vivo' -ForegroundColor Cyan
Write-Host '==================================================' -ForegroundColor Cyan

# 1. Entorno Python y dependencias
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    Write-Host 'Creando entorno virtual .venv...' -ForegroundColor Yellow
    python -m venv (Join-Path $PSScriptRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear .venv.' }
    & $pythonExe -m pip install -r $requirementsPath
    if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar las dependencias de requirements.txt.' }
}

# 2. Restauracion opcional de base limpia para presentacion
if ($RestaurarLimpia) {
    if (Test-LocalPort -TargetPort $Port) {
        throw "No se puede restaurar data\pruebas.db mientras TalentIA esta escuchando en el puerto $Port. Detenlo primero e intenta de nuevo."
    }
    if (-not (Test-Path -LiteralPath $cleanBackupPath -PathType Leaf)) {
        throw "No se encontro el respaldo limpio en $cleanBackupPath."
    }
    Copy-Item -LiteralPath $cleanBackupPath -Destination $dbPath -Force
    New-Item -ItemType Directory -Path $uploadsPath -Force | Out-Null
    Get-ChildItem -LiteralPath $uploadsPath -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
    Write-Host 'Base data\pruebas.db restaurada desde data\pruebas-presentacion-limpia.db.' -ForegroundColor Green
}

if (-not (Test-Path -LiteralPath $dbPath -PathType Leaf)) {
    Write-Host 'Inicializando base de pruebas...' -ForegroundColor Yellow
    & $pythonExe (Join-Path $PSScriptRoot 'preparar_prueba.py')
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo inicializar data\pruebas.db.' }
}

# 3. Verificar LM Studio (puerto 1234) y modelo cargado
$lmModels = $null
try {
    $lmModels = Get-LmStudioModels
} catch {
    $lmsCmd = Get-Command lms -ErrorAction SilentlyContinue
    if ($lmsCmd) {
        Write-Host 'Iniciando servidor local de LM Studio con lms...' -ForegroundColor Yellow
        try {
            & $lmsCmd.Source server start | Out-Null
            Start-Sleep -Seconds 2
            $lmModels = Get-LmStudioModels
        } catch {
            $lmModels = $null
        }
    }
}

if ($null -eq $lmModels) {
    $lmExe = Join-Path $env:LOCALAPPDATA 'Programs\LM Studio\LM Studio.exe'
    Write-Warning 'LM Studio no responde en http://127.0.0.1:1234. Abre LM Studio y activa su servidor local antes de analizar CVs.'
    if (Test-Path -LiteralPath $lmExe -PathType Leaf) {
        Write-Host "Ruta de LM Studio: $lmExe" -ForegroundColor DarkGray
    }
} else {
    $loaded = @($lmModels.models | Where-Object { $_.type -eq 'llm' -and $_.loaded_instances.Count -gt 0 })
    if ($loaded.Count -eq 0 -and $Modelo) {
        $lmsCmd = Get-Command lms -ErrorAction SilentlyContinue
        if ($lmsCmd) {
            Write-Host "Cargando modelo '$Modelo' en LM Studio..." -ForegroundColor Yellow
            try {
                & $lmsCmd.Source load $Modelo --yes | Out-Null
                Start-Sleep -Seconds 2
                $lmModels = Get-LmStudioModels
                $loaded = @($lmModels.models | Where-Object { $_.type -eq 'llm' -and $_.loaded_instances.Count -gt 0 })
            } catch {
                Write-Warning "No se pudo cargar automaticamente '$Modelo'."
            }
        }
    }
    if ($loaded.Count -gt 0) {
        $loadedNames = @($loaded | ForEach-Object { $_.loaded_instances.id }) -join ', '
        Write-Host "LM Studio listo en http://127.0.0.1:1234 | Modelo cargado: $loadedNames" -ForegroundColor Green
    } else {
        Write-Warning 'LM Studio esta activo, pero no hay ningun modelo LLM de conversacion cargado (por ejemplo qwen3.5-2b).'
    }
}

# 4. Iniciar asistente local (puerto 11435) si no esta escuchando
$origin = "http://localhost:$Port"
$assistantProcess = $null

try {
    if (Test-LocalPort -TargetPort 11435) {
        Write-Host 'Asistente local ya activo en http://127.0.0.1:11435.' -ForegroundColor Green
    } else {
        Write-Host "Iniciando asistente local para $origin..." -ForegroundColor Yellow
        $assistantProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-u', $assistantScript, '--origin', $origin) -WorkingDirectory $PSScriptRoot -NoNewWindow -PassThru
        $deadline = (Get-Date).AddSeconds(6)
        while ((Get-Date) -lt $deadline -and -not (Test-LocalPort -TargetPort 11435)) {
            Start-Sleep -Milliseconds 250
        }
        if (Test-LocalPort -TargetPort 11435) {
            Write-Host 'Asistente local listo en http://127.0.0.1:11435.' -ForegroundColor Green
        } else {
            Write-Warning 'El asistente local tardo en responder en el puerto 11435. Revisa si hubo algun mensaje arriba.'
        }
    }

    # 5. Variables de entorno del espacio de demostracion
    $env:DATABASE_PATH = 'data/pruebas.db'
    $env:UPLOADS_DIR = 'data/pruebas-uploads'
    $env:FORZAR_SIMULACION = 'true'

    Write-Host ''
    Write-Host "Web TalentIA          : $origin"
    Write-Host 'Clave asistente local : http://127.0.0.1:11435'
    Write-Host 'LM Studio local       : http://127.0.0.1:1234'
    Write-Host 'Credenciales locales  : data\acceso-reclutador.txt'
    Write-Host ''

    if (-not $SinAbrirNavegador) {
        if (Test-LocalPort -TargetPort 11435) {
            Start-Process 'http://127.0.0.1:11435'
        }
        if (Test-LocalPort -TargetPort $Port) {
            Start-Process $origin
        } else {
            $openScript = @"
`$limit = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt `$limit) {
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        Start-Process '$origin'
        break
    }
    Start-Sleep -Milliseconds 300
}
"@
            $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($openScript))
            Start-Process powershell -WindowStyle Hidden -ArgumentList @('-NoProfile', '-EncodedCommand', $encoded)
        }
    }

    if (Test-LocalPort -TargetPort $Port) {
        Write-Host "TalentIA ya esta escuchando en $origin (puerto $Port). No se inicio una segunda instancia." -ForegroundColor Cyan
        exit 0
    }

    Write-Host 'Iniciando servidor TalentIA en primer plano (pulsa Ctrl+C para detener)...' -ForegroundColor Cyan
    & $pythonExe -m uvicorn backend.main:app --host $ListenAddress --port $Port
    $exitCode = $LASTEXITCODE
    if ($exitCode -and $exitCode -ne 0) {
        throw "TalentIA finalizo con codigo $exitCode."
    }
} finally {
    if ($assistantProcess -and -not $assistantProcess.HasExited) {
        Write-Host 'Deteniendo asistente local iniciado por esta sesion...' -ForegroundColor Yellow
        Stop-Process -Id $assistantProcess.Id -Force -ErrorAction SilentlyContinue
    }
}
