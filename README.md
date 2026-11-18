# TalentIA · Prueba local con LM Studio

Web de selección de personal con cuentas, vacantes, carga de hasta 100 PDF, evaluación con evidencias, ranking y calendario de entrevistas. La IA se ejecuta en el computador del usuario mediante LM Studio. No necesitas servicios de Google.

## Requisitos
- Python 3.12 o superior y Git.
- LM Studio con `qwen3.5-2b` descargado. Activa su servidor local en `http://127.0.0.1:1234` y carga el modelo. El identificador debe ser `qwen3.5-2b`.
- Memoria suficiente para el modelo y conexión solo para las descargas iniciales.

## Windows: iniciar la prueba

```powershell
git clone https://github.com/MatiuC/Talent-IA.git
cd Talent-IA
powershell -ExecutionPolicy Bypass -File .\probar-local.ps1
```

Este comando instala las dependencias, crea una base aislada `data/pruebas.db`, una cuenta pública de demostración y dos vacantes (Analista de datos junior y Soporte técnico). No modifica una base personal existente.

**Acceso público de pruebas:**

- Correo: `prueba@talentia.local`
- Contraseña: `_-dF06kcIpgYIKvNCdq2`

Las nuevas instalaciones usan estas mismas credenciales, que también aparecen en la terminal y se guardan en `data/acceso-pruebas.txt`, excluido de Git. Si ya creaste una cuenta con otra contraseña, conserva la original. Este acceso compartido es solo para demostraciones con datos de prueba.

Abre http://localhost:8001 y entra con esas credenciales. Mantén la terminal abierta.

En una segunda terminal, desde la carpeta del proyecto:

```powershell
powershell -ExecutionPolicy Bypass -File .\companion\Iniciar-LMStudio.ps1
```

Abre http://127.0.0.1:11435, copia la clave de vinculación y pégala en TalentIA → Configuración → Motor de este computador → Conectar mi computador. Si el navegador lo solicita, permite el acceso local. La clave permanece durante la sesión de pestaña; el modelo permanece guardado en LM Studio entre sesiones.

Selecciona una vacante y carga tu CV. La evaluación se hace en tu equipo y se guarda en la base local. Puedes subir el mismo CV a ambas vacantes para comparar su ajuste. No uses `?demo=1` para esta prueba: esa vista solo muestra ejemplos.

## Linux/macOS

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

## Funcionalidad y límites
- Completar/reabrir vacantes preserva el historial; las cerradas no admiten nuevos candidatos.
- Ordenar candidatos por mayor/menor ajuste o recepción.
- Calendario mensual navegable. Los enlaces Meet son demostrativos y no crean reuniones.
- Los correos se preparan en la aplicación de correo del usuario; no se envían automáticamente.
- PDF con texto seleccionable; los escaneados requieren revisión manual. No hay OCR.
- La IA puede equivocarse: revisa el CV original y sus evidencias. La decisión es humana.
- Los CV y los resultados se guardan en la instalación de la web. Si se aloja en otro servidor, los documentos sí se transfieren a ese servidor, aunque la inferencia siga siendo local.
- Una cuenta de pruebas y sus dos vacantes se generan en cada equipo; no se distribuyen bases de datos ni documentos privados.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Las pruebas usan bases temporales y simulan los servicios externos. Se verificó además una extracción real del CV con Qwen en LM Studio. La integración con Google y la interfaz antigua de Streamlit son históricas y no hacen falta para este recorrido.

Para publicar el backend en Internet, consulta ARQUITECTURA-CLIENTE.md. GitHub almacena el código; no ejecuta este servidor Python. No publiques `data/`, `.env`, claves ni credenciales personales. Las credenciales de demostración anteriores son públicas intencionalmente.
