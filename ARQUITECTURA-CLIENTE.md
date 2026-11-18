# TalentIA: web central y Qwen en el computador del cliente

La interfaz usa ahora este recorrido:

1. El usuario inicia sesión en la web alojada en el servidor.
2. Descarga el asistente Windows desde Configuración, lo extrae y ejecuta `Iniciar-Asistente.ps1 -Web https://su-dominio` (requiere Python 3.10+). El script instala Ollama desde su proveedor oficial si falta.
3. Abre la página local del asistente y copia su clave en Configuración. El navegador puede pedir permiso de acceso a la red local.
4. Pulsa Descargar / comprobar Qwen. El modelo queda en el almacenamiento de Ollama; no se vuelve a descargar cuando ya existe.
5. La web sube los PDF al servidor para extraer y conservar documentos. Devuelve al navegador el texto y la rúbrica. El navegador llama al asistente en 127.0.0.1:11435; este ejecuta Qwen con Ollama en 127.0.0.1:11434. Solo el resultado vuelve al servidor.

**Los PDF sí se guardan en el servidor. La inferencia es local.** No se usa el equipo servidor para ejecutar el modelo en el nuevo flujo de la interfaz. Los endpoints antiguos de análisis siguen disponibles para compatibilidad con la primera versión; la interfaz nueva no los usa.

El asistente limita los orígenes a la web indicada, exige una clave y escucha solo en loopback. La clave se guarda en `%LOCALAPPDATA%/TalentIA/companion-key`, y la web la conserva únicamente durante la sesión de pestaña. Reabrir el navegador puede requerir vincular otra vez; no requiere descargar el modelo otra vez. Mantén el asistente y Ollama abiertos durante el análisis.

## Desplegar la web central

Esta entrega actualiza el proyecto local; no se ha contratado ni configurado hosting público. El backend es Python/FastAPI y necesita almacenamiento persistente para `data/` y las dependencias de `requirements.txt`. No necesita Ollama para el flujo de cliente. Usa un proxy HTTPS delante de Uvicorn y configura `AUTH_COOKIE_SECURE=true`. No publiques `data/`, `.env` ni credenciales como archivos estáticos. Crea la primera cuenta desde localhost en el servidor antes de abrir el servicio a los usuarios.

El dominio definitivo se pasa al asistente con `-Web`, sin barra final. La comunicación HTTPS → loopback depende del navegador y de su permiso de acceso local; debe probarse en los navegadores objetivo al desplegar. Esta entrega no valida ese permiso en un dominio público.

## Cambios de producto

- Hasta 100 currículums por lote, con validación en navegador y servidor. La inferencia se procesa de uno en uno en el cliente para controlar la memoria.
- Menú funcional de espacio de trabajo y búsqueda global de ofertas.
- Calendario mensual navegable, selección de día y detalle de entrevista.
- Enlaces Meet identificados como simulados. No crean reuniones reales.
- Correo prellenado mediante la aplicación de correo del usuario; no se envía automáticamente. Google continúa como integración opcional existente.
- Orden por mayor/menor ajuste y fecha de recepción.
- Nombres de candidatos y vacantes con mayor tamaño.

## Verificación

15 pruebas automatizadas de API pasan, incluyendo envío del resultado calculado en cliente sin invocar el modelo del servidor y rechazo de lotes de 101 archivos. Se comprobó la extracción real del CV aportado: 1 página, 2.859 caracteres útiles, legible. No se ha ejecutado Qwen contra ese CV en esta entrega porque el motor no está instalado ni vinculado en el equipo. Tampoco se han enviado correos.
