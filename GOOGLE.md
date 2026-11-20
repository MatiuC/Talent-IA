# Google Calendar y Gmail por reclutador

Cada usuario conecta su propia cuenta en Configuración → Mi cuenta de Google.
La cuenta pública de demostración se comparte: para cuentas Google independientes,
crea primero un usuario TalentIA distinto por reclutador.

## Configuración única del administrador

1. Crea un proyecto en Google Cloud y activa Google Calendar API y Gmail API.
2. Configura la pantalla de consentimiento OAuth. En modo de pruebas, añade los correos de los reclutadores como usuarios de prueba.
3. Crea un cliente OAuth de tipo **Aplicación web**.
4. Registra exactamente `http://localhost:8001/google/callback` como URI de redirección autorizada.
5. Descarga el JSON y guárdalo como `credentials.json` en la raíz del proyecto. No lo subas a GitHub.
6. Reinicia TalentIA. Cada reclutador inicia sesión en TalentIA y pulsa **Conectar mi Google**, selecciona su cuenta y autoriza Calendar y Gmail.

En un servidor configura GOOGLE_REDIRECT_URI con la URL HTTPS del servidor terminada en /google/callback y registra esa misma dirección en Google Cloud y en el JSON. No se deriva del encabezado Host del navegador.

La nueva conexión por usuario habilita envíos reales tras la confirmación, incluso en el entorno de pruebas que fuerza simulación para la integración antigua. Para probar sin enviar, mantén Google desconectado. No envíes invitaciones a los correos ficticios del lote.

Al confirmar una entrevista se crea el evento, se solicita Meet para videollamadas y se envía el correo desde la cuenta conectada. Google puede tardar en generar Meet; el enlace al evento siempre permite consultar la reunión. Calendar puede enviar además su propia notificación al invitado.

Si Calendar confirma el evento pero Gmail falla, se conserva el evento y se muestra el fallo; no se marca la entrevista como completamente enviada ni se reintenta automáticamente para evitar duplicados. Revisa Calendar antes de actuar.

Desconectar elimina la autorización local del usuario; para revocarla también en Google visita las conexiones de terceros de tu cuenta. Las autorizaciones se guardan en la base local excluida de Git; protege el acceso al servidor y sus copias de seguridad. No se comparten entre usuarios TalentIA.

Documentación oficial:
- https://developers.google.com/identity/protocols/oauth2/web-server
- https://developers.google.com/workspace/calendar/api/v3/reference/events/insert
- https://developers.google.com/workspace/gmail/api/guides/sending
