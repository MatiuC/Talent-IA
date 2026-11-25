[CmdletBinding()]
param(
    [switch]$CerrarSesiones,

    [switch]$RestaurarDesdeRespaldo,

    [ValidateRange(1024, 65535)]
    [int]$Port = 8001
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    $pythonExe = 'python'
}

$dbPath = Join-Path $PSScriptRoot 'data\pruebas.db'
$backupPath = Join-Path $PSScriptRoot 'data\pruebas-presentacion-limpia.db'
$uploadsPath = Join-Path $PSScriptRoot 'data\pruebas-uploads'

if (-not (Test-Path -LiteralPath $dbPath -PathType Leaf)) {
    throw "No se encontro la base de pruebas en $dbPath."
}

Write-Host ''
Write-Host 'TalentIA - Reinicio limpio de datos a cero' -ForegroundColor Cyan
Write-Host '==========================================' -ForegroundColor Cyan

if ($RestaurarDesdeRespaldo) {
    $listening = [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($listening) {
        throw "No se puede reemplazar data\pruebas.db mientras el puerto $Port esta activo. Deten TalentIA primero o ejecuta limpiar-demo.ps1 sin -RestaurarDesdeRespaldo."
    }
    if (-not (Test-Path -LiteralPath $backupPath -PathType Leaf)) {
        throw "No se encontro el respaldo limpio en $backupPath."
    }
    Copy-Item -LiteralPath $backupPath -Destination $dbPath -Force
    Write-Host 'Base restaurada desde data\pruebas-presentacion-limpia.db.' -ForegroundColor Green
}

# Limpiar PDFs subidos conservando la carpeta
New-Item -ItemType Directory -Path $uploadsPath -Force | Out-Null
$lockedFiles = 0
Get-ChildItem -LiteralPath $uploadsPath -Force -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction Stop
    } catch {
        $lockedFiles++
        Write-Warning "No se pudo eliminar temporalmente $($_.Name): $($_.Exception.Message)"
    }
}

# Limpiar tablas operativas conservando usuarios, sesiones (salvo -CerrarSesiones), Google y 4 vacantes iniciales
$env:TALENTIA_CLEAN_DB = $dbPath
$env:TALENTIA_CLOSE_SESSIONS = if ($CerrarSesiones) { '1' } else { '0' }
@'
import os
import sqlite3
from pathlib import Path

db_path = Path(os.environ["TALENTIA_CLEAN_DB"])
close_sessions = os.environ.get("TALENTIA_CLOSE_SESSIONS") == "1"

conn = sqlite3.connect(str(db_path), timeout=30.0, isolation_level=None)
try:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM entrevistas")
        conn.execute("DELETE FROM evaluaciones")
        conn.execute("DELETE FROM logs_metricas")
        conn.execute("DELETE FROM candidatos")
        conn.execute("DELETE FROM google_oauth_states")
        conn.execute("DELETE FROM auth_attempts")
        if close_sessions:
            conn.execute("DELETE FROM auth_sessions")
        conn.execute("DELETE FROM vacantes WHERE id > 4")
        conn.execute("UPDATE vacantes SET activa = 1 WHERE id <= 4")
        for tbl, seq_val in (("candidatos", 0), ("evaluaciones", 0), ("entrevistas", 0), ("logs_metricas", 0), ("vacantes", 4)):
            conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = ?", (seq_val, tbl))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    counts = {
        tbl: conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        for tbl in ("candidatos", "evaluaciones", "entrevistas", "logs_metricas")
    }
    vacantes = conn.execute("SELECT COUNT(*) FROM vacantes WHERE activa = 1").fetchone()[0]
    users = conn.execute("SELECT COUNT(*) FROM auth_users").fetchone()[0]
    google_ok = conn.execute("SELECT COUNT(*) FROM google_accounts WHERE token IS NOT NULL AND token != ''").fetchone()[0]
finally:
    conn.close()

print(
    f"candidatos={counts['candidatos']} | evaluaciones={counts['evaluaciones']} | "
    f"entrevistas={counts['entrevistas']} | logs={counts['logs_metricas']}"
)
print(f"vacantes_activas={vacantes} | usuarios={users} | google_conectado={google_ok > 0}")
'@ | & $pythonExe -
if ($LASTEXITCODE -ne 0) {
    throw 'Error limpiando las tablas de SQLite en data\pruebas.db.'
}

$remainingUploads = (Get-ChildItem -LiteralPath $uploadsPath -Force -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Host "archivos_uploads=$remainingUploads | bloqueados=$lockedFiles"
Write-Host ''
Write-Host 'Espacio reiniciado a cero (usuarios y Google conservados).' -ForegroundColor Green
Write-Host 'Actualiza tus pestanas del navegador (F5) para empezar nuevas pruebas.' -ForegroundColor Yellow
