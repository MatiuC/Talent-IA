## Usar Qwen ya descargado en LM Studio

Abre el servidor de LM Studio en 127.0.0.1:1234 con qwen3.5-2b disponible. El asistente lo detecta y lo prioriza sin descargar Ollama ni otro modelo. Mantén LM Studio y el asistente abiertos. Para iniciar el asistente sin instalar Ollama, ejecuta desde esta carpeta:

```powershell
powershell -ExecutionPolicy Bypass -File .\Iniciar-LMStudio.ps1
```

Después abre http://127.0.0.1:11435 y copia la clave en Configuración de TalentIA. Esta es una clave de vinculación de TalentIA, no una API key de LM Studio. Si la página no abre, comprueba que la terminal del asistente siga abierta; tener solo LM Studio abierto no basta.

# Asistente local TalentIA (Windows)

Necesitas Python 3.10 o superior. Extrae el ZIP en una carpeta permanente.

Ejecuta en PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\Iniciar-Asistente.ps1 -Web "https://TU-DOMINIO"
```

Usa el origen exacto de la web, sin ruta ni barra final. Para este prototipo usa http://localhost:8001.
El asistente instala Ollama desde el proveedor oficial si falta. Después abre http://127.0.0.1:11435 y copia la clave de vinculación en Configuración de TalentIA. Pulsa Descargar Qwen una vez. Su descarga puede ocupar varios GB. En próximas sesiones abre el mismo asistente: el modelo se conserva.

El navegador puede solicitar permiso para acceder a la red local. Debes permitirlo para esta web. El asistente solo escucha en tu computador y acepta el origen configurado y tu clave. No expongas sus puertos a Internet.

Los PDF y los resultados se guardan en el servidor de TalentIA; la inferencia ocurre en tu computador. No se promete que los CV nunca salgan del equipo. No hay envío de prompts a servicios de IA en la nube.

No borres la carpeta .ollama/models de tu usuario si quieres conservar el modelo. En Windows la clave se conserva en %LOCALAPPDATA%/TalentIA/companion-key. Este primer instalador está orientado a Windows; macOS/Linux requieren instalar Ollama y ejecutar asistente.py manualmente.


## Otros modelos en LM Studio

El asistente detecta el primer modelo de conversación cargado en LM Studio 0.4 o superior: Gemma, Qwen u otro compatible con respuestas JSON estructuradas. Los modelos de embeddings no sirven para evaluar CV. Carga solo el modelo que quieras usar; si tienes varios cargados puedes elegir su identificador con la variable de entorno `LM_STUDIO_MODEL` antes de iniciar el asistente.

La clave de vinculación de TalentIA es independiente del modelo y se conserva al cambiarlo. Inicia `Iniciar-LMStudio.ps1` (dentro de companion si usas el repositorio) y abre http://127.0.0.1:11435. El iniciador abre el asistente aunque falte cargar el modelo. Comprueba en Configuración qué modelo está listo.

La compatibilidad y calidad dependen del modelo y de la memoria disponible: no todos admiten el formato JSON requerido. El servidor de LM Studio debe estar activo en 127.0.0.1:1234, sin autenticación API adicional para esta configuración.
