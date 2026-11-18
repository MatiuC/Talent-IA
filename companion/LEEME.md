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
