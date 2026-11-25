# TalentIA · Selección inteligente de personal con IA local

Web de selección de personal con roles (**Reclutador líder** y **Reclutador**), gestión de vacantes, carga de hasta **100 currículums en PDF** por lote, progreso flotante persistente con doble barra, evaluación estructurada con evidencias en **LM Studio**, descarte rápido para puntajes bajos (`1/5` y `2/5`), ranking, calendario de entrevistas, métricas técnicas en vivo (latencia, *throughput*, CPU, RAM, GPU/VRAM y logs) y diseño responsivo para pantallas divididas.

La inferencia con el LLM se ejecuta **100 % en el computador del cliente** mediante el asistente local (`127.0.0.1:11435`) conectado a **LM Studio** (`127.0.0.1:1234`), mientras que los PDF, resultados y auditoría se guardan en **SQLite (modo WAL)**. No necesitas servicios de Google para usar el flujo principal (puedes activarlos opcionalmente por reclutador).

---

## Requisitos

- **Python 3.12** o superior y **Git**.
- **LM Studio 0.4** o superior con un modelo de conversación compatible con respuestas JSON estructuradas (por ejemplo, **Qwen 3.5 2B** o **Gemma**).
  - Configura el servidor local en `http://127.0.0.1:1234`.
  - Recomendado para concurrencia de 3 usuarios en una GPU local: `context_length: 8192` y `parallel: 4`, con *Reasoning / Thinking* desactivado.
- En equipos con GPU NVIDIA, `nvidia-smi` disponible en el sistema habilita automáticamente la telemetría de uso de **GPU %** y **VRAM** en la vista **Actividad**.

---

## Inicio rápido de demostración en vivo (Windows)

```powershell
git clone https://github.com/MatiuC/Talent-IA.git
cd Talent-IA
powershell -ExecutionPolicy Bypass -File .\probar-local.ps1
```

La primera vez, `probar-local.ps1` crea `.venv`, instala `requirements.txt` e inicializa `data/pruebas.db` y `data/acceso-pruebas.txt` (excluidos de Git). Para arrancar todo en **una sola terminal** en usos posteriores:

```powershell
powershell -ExecutionPolicy Bypass -File .\iniciar-demo.ps1
```

### Qué automatiza `iniciar-demo.ps1`
- Verifica `.venv` y `data/pruebas.db`.
- Comprueba LM Studio en `http://127.0.0.1:1234` (intenta iniciarlo con `lms server start` si falta y valida si hay un modelo cargado; admite `-Modelo <id>` para cargarlo automáticamente).
- Inicia el asistente local en `http://127.0.0.1:11435` con `--origin http://localhost:8001` (y lo detiene limpiamente al cerrar con `Ctrl+C`).
- Abre en tu navegador la clave del asistente (`http://127.0.0.1:11435`) y TalentIA (`http://localhost:8001`), o permite omitirlo con `-SinAbrirNavegador`.
- Ejecuta Uvicorn con los logs estructurados en tiempo real en la consola.
- Parámetros útiles: `-RestaurarLimpia` (restaura `data/pruebas.db` desde `data/pruebas-presentacion-limpia.db` si el servidor está detenido), `-ListenAddress 0.0.0.0`, `-Port 8001`.

### Reiniciar datos a cero entre pruebas (`limpiar-demo.ps1`)
Para vaciar candidatos, evaluaciones, entrevistas, métricas y PDFs subidos conservando usuarios, sesiones activas, conexión Google y vacantes iniciales:

```powershell
powershell -ExecutionPolicy Bypass -File .\limpiar-demo.ps1
```

Opciones adicionales:
- `-CerrarSesiones`: también invalida las sesiones activas.
- `-RestaurarDesdeRespaldo`: reemplaza `data/pruebas.db` desde `data/pruebas-presentacion-limpia.db` (requiere detener el servidor antes).

---

## Inicio manual por terminales

### Windows
1. **Terminal 1 (Servidor web):**
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\probar-local.ps1
   ```
2. **Terminal 2 (Asistente local + LM Studio):**
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\companion\Iniciar-LMStudio.ps1
   ```
3. Abre `http://127.0.0.1:11435`, copia la clave de vinculación y pégala en **TalentIA → Configuración → Motor de este computador → Conectar mi computador** (permite el acceso a red local si el navegador lo solicita).

### Linux / macOS
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python preparar_prueba.py
DATABASE_PATH=data/pruebas.db UPLOADS_DIR=data/pruebas-uploads FORZAR_SIMULACION=true python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001
```
En otra terminal, con LM Studio activo:
```bash
python3 companion/asistente.py --origin http://localhost:8001
```

> Usa `http://localhost:8001` (no `?demo=1` ni `127.0.0.1:8001`) para la prueba real con LM Studio; `?demo=1` muestra únicamente datos ilustrativos de ejemplo.

---

## Roles, concurrencia y métricas

### Aislamiento por rol
- **Reclutador líder (`admin`)**: visualiza y gestiona todas las vacantes, candidatos, PDFs, entrevistas, métricas y logs de todo el equipo, además de crear cuentas desde **Configuración**.
- **Reclutador (`recruiter`)**: visualiza y gestiona únicamente los currículums, entrevistas, contadores por vacante y métricas que él mismo sube; cualquier acceso directo a perfiles de otro reclutador responde `403`.

### Concurrencia de 3 usuarios simultáneos
- **Base de datos**: SQLite configurado con `PRAGMA journal_mode=WAL`, `busy_timeout = 30s`, hilos con `asyncio.to_thread` y transacción atómica en aprobación de entrevistas (`409` ante doble aprobación simultánea).
- **Asistente local ([companion/asistente.py](companion/asistente.py))**: servidor multihilo (`ThreadingHTTPServer`) con `TALENTIA_CONCURRENCY=3` por defecto, caché de metadatos de LM Studio (`30 s` con respaldo ante carga alta), cálculo dinámico de contexto por ranura paralela (`context_length // parallel`) con reintento automático ante exceso de contexto y reporte detallado de errores HTTP.

### Panel de Actividad del sistema (`#metrics`)
- **KPIs**: `CV procesados`, `Tiempo promedio (s)`, `Generación del modelo (tokens/s)` y `Análisis exitosos (%)`.
- **Recursos de tu equipo**: barras en vivo de **Procesador (%)**, **Memoria RAM (%)**, **GPU (%)** y **Memoria GPU / VRAM (MB y %)** mediante `psutil` + `nvidia-smi`.
- **Un vistazo al motor**: modelo realmente ejecutado, latencia máxima, tokens generados totales, sesiones en el historial y errores registrados.
- **Historial de análisis** y **logs en consola**: trazabilidad por archivo, sesión (`usuario-id:Nombre`), tiempo, tokens/s, tokens, CPU/RAM/GPU, modelo y estado.

---

## Funcionalidad y límites

- **Progreso flotante persistente con doble barra**: aparece en la esquina superior derecha únicamente durante análisis activos, resúmenes sin cerrar o recuperación tras recarga (`/api/pendientes-local/resumen`). Combina una barra superior animada de actividad y una barra porcentual de avance, conserva contadores y solicitudes al navegar por secciones y bloquea dobles envíos.
- **Tolerancia a errores y reanudación sin duplicados**: los perfiles con `ERROR_PROCESAMIENTO` se ocultan de **Candidatos** y de los totales visibles, pero permanecen guardados para reintentarse con **Retomar pendientes** actualizando el mismo registro. Ante pérdida de conexión, detiene limpiamente el lote conservando lo ya guardado.
- **Descarte rápido**: en tablas de **Candidatos**, los perfiles en estado **Por revisar** con puntaje `1/5` o `2/5` muestran el botón inline **Descartar candidato** para registrar el motivo sin abrir primero el detalle completo.
- **Diseño responsivo**: a partir de `<= 1040 px` (ventanas divididas o en una esquina de pantalla) el menú lateral colapsa en cajón deslizable (`☰`) con fondo tocable y sin desbordamiento horizontal.
- **Extracción PDF**: requiere PDFs con texto seleccionable (sin OCR; los escaneados o ilegibles pasan automáticamente a **Revisión manual**).
- **Criterio humano**: la IA aporta puntaje orientativo (`1` a `5`) y citas textuales como evidencia; la decisión final y el agendamiento siempre requieren confirmación humana.
- **Privacidad**: nunca subas a Git `data/`, `.env`, `credentials.json`, claves ni documentos privados.

---

## Pruebas automatizadas

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Incluye **21 pruebas** en `tests/` (`test_auth.py`, `test_google_accounts.py`, `test_workflows.py`) que validan autenticación, aislamiento de reclutadores vs. reclutador líder, recuperación de pendientes sin duplicados, inferencia local del cliente y concurrencia de 3 usuarios autenticados.

---

## Google Calendar y Gmail opcionales

Cada reclutador puede conectar su propia cuenta desde **Configuración** siguiendo [GOOGLE.md](GOOGLE.md) para configurar `credentials.json` en Google Cloud Console. Los envíos reales requieren confirmación explícita en cada entrevista; sin Google conectado, el sistema opera en **modo simulación** sin enviar correos ni crear eventos reales.

## Otros modelos en LM Studio

El asistente detecta automáticamente el primer modelo LLM cargado en LM Studio 0.4 o superior (Qwen, Gemma u otro compatible con `json_schema`). Si tienes varios cargados, define `LM_STUDIO_MODEL` antes de iniciar el asistente. La clave de vinculación (`%LOCALAPPDATA%\TalentIA\companion-key`) es independiente del modelo y se conserva entre sesiones.
