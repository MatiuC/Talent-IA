"""Integracion con Google Calendar y Gmail, con modo simulacion automatico.

Si `credentials.json` no existe (o faltan las librerias de Google, o la
autorizacion falla), el modulo entra en MODO SIMULACION: construye y registra
exactamente el mismo payload que se habria enviado, pero sin llamar a la red.
Asi cualquier integrante del equipo puede probar el flujo completo de principio
a fin sin bloquearse por credenciales.

Autorizacion inicial (una sola vez, por quien tenga credentials.json):
    python -m backend.google_services
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any, Dict, List, Optional

from backend.config import settings

logger = logging.getLogger("talentia.google")

SCOPES: List[str] = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.send",
]

MODO_REAL = "real"
MODO_SIMULACION = "simulacion"

# --- Deteccion de librerias -------------------------------------------------
try:
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    LIBRERIAS_GOOGLE_DISPONIBLES = True
    _ERROR_LIBRERIAS = ""
except ImportError as exc:  # pragma: no cover - entorno sin librerias de Google
    LIBRERIAS_GOOGLE_DISPONIBLES = False
    _ERROR_LIBRERIAS = str(exc)
    GoogleRequest = Credentials = InstalledAppFlow = build = None  # type: ignore[assignment]

    class HttpError(Exception):  # type: ignore[no-redef]
        """Sustituto cuando googleapiclient no esta instalado."""


# ---------------------------------------------------------------------------
# Plantillas del correo de invitacion
# ---------------------------------------------------------------------------
ASUNTO_INVITACION = "Invitacion a entrevista - {titulo_vacante}"

CUERPO_INVITACION = """Estimado/a {nombre}:

Le saludamos de {empresa}.

Hemos revisado su postulacion para la vacante de {titulo_vacante} y nos complace
invitarle a una entrevista.

  Fecha y hora : {fecha_legible}
  Duracion     : {duracion} minutos
  Modalidad    : {modalidad}
  Zona horaria : {zona}
{bloque_enlace}{bloque_notas}
Por favor confirme su asistencia respondiendo a este correo.

Atentamente,
Equipo de Seleccion
{empresa}

--
Mensaje generado por TalentIA, asistente de seleccion de personal.
"""


def _formato_legible(fecha: datetime) -> str:
    dias = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
    meses = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ]
    return (
        f"{dias[fecha.weekday()]} {fecha.day} de {meses[fecha.month - 1]} "
        f"de {fecha.year}, {fecha.strftime('%H:%M')}"
    )


class GoogleServices:
    """Fachada de Google Calendar + Gmail con degradacion segura a simulacion."""

    def __init__(self) -> None:
        self._credenciales: Optional[Any] = None
        self._calendar: Optional[Any] = None
        self._gmail: Optional[Any] = None
        self._motivo_simulacion: str = ""
        self._inicializado: bool = False

    # -- Estado -----------------------------------------------------------
    @property
    def modo(self) -> str:
        """Modo efectivo: 'real' o 'simulacion'."""
        if settings.forzar_simulacion:
            return MODO_SIMULACION
        if not LIBRERIAS_GOOGLE_DISPONIBLES:
            return MODO_SIMULACION
        if not settings.google_credentials_path.exists():
            return MODO_SIMULACION
        return MODO_REAL

    @property
    def motivo_simulacion(self) -> str:
        if settings.forzar_simulacion:
            return "FORZAR_SIMULACION=true en el archivo .env."
        if not LIBRERIAS_GOOGLE_DISPONIBLES:
            return (
                "Librerias de Google no instaladas "
                f"({_ERROR_LIBRERIAS}). Ejecute: pip install -r requirements.txt"
            )
        if not settings.google_credentials_path.exists():
            return (
                f"No se encontro {settings.google_credentials_path.name} en la raiz del "
                "proyecto: el flujo se ejecuta en modo simulacion."
            )
        return self._motivo_simulacion

    def estado(self) -> Dict[str, Any]:
        return {
            "modo": self.modo,
            "motivo": self.motivo_simulacion,
            "credenciales_presentes": settings.google_credentials_path.exists(),
            "token_presente": settings.google_token_path.exists(),
            "librerias_disponibles": LIBRERIAS_GOOGLE_DISPONIBLES,
            "calendar_id": settings.google_calendar_id,
            "timezone": settings.timezone,
        }

    # -- Autenticacion ----------------------------------------------------
    def _autenticar(self, permitir_navegador: bool = False) -> bool:
        """Carga o renueva las credenciales OAuth2. Devuelve True si hay sesion."""
        if self.modo == MODO_SIMULACION:
            return False
        if self._inicializado and self._calendar is not None and self._gmail is not None:
            return True

        try:
            credenciales: Optional[Any] = None
            if settings.google_token_path.exists():
                credenciales = Credentials.from_authorized_user_file(
                    str(settings.google_token_path), SCOPES
                )

            if credenciales and credenciales.expired and credenciales.refresh_token:
                credenciales.refresh(GoogleRequest())

            if not credenciales or not credenciales.valid:
                if not permitir_navegador:
                    self._motivo_simulacion = (
                        "No existe un token.json valido. Autorice una vez ejecutando: "
                        "python -m backend.google_services"
                    )
                    return False
                flujo = InstalledAppFlow.from_client_secrets_file(
                    str(settings.google_credentials_path), SCOPES
                )
                credenciales = flujo.run_local_server(port=0)

            settings.google_token_path.write_text(credenciales.to_json(), encoding="utf-8")

            self._credenciales = credenciales
            self._calendar = build("calendar", "v3", credentials=credenciales, cache_discovery=False)
            self._gmail = build("gmail", "v1", credentials=credenciales, cache_discovery=False)
            self._inicializado = True
            self._motivo_simulacion = ""
            return True

        except Exception as exc:
            self._motivo_simulacion = f"Fallo de autenticacion con Google: {type(exc).__name__}: {exc}"
            logger.warning(self._motivo_simulacion)
            return False

    def autorizar_interactivo(self) -> bool:
        """Lanza el consentimiento OAuth en el navegador y guarda token.json."""
        return self._autenticar(permitir_navegador=True)

    # -- Construccion de payloads ----------------------------------------
    def construir_payloads(
        self,
        nombre_candidato: str,
        email_candidato: str,
        titulo_vacante: str,
        fecha_hora: datetime,
        duracion_minutos: int,
        modalidad: str,
        notas: str = "",
        enlace: str = "",
    ) -> Dict[str, Any]:
        """Arma el evento de Calendar y el correo de Gmail (sin enviarlos)."""
        fin = fecha_hora + timedelta(minutes=duracion_minutos)
        fecha_legible = _formato_legible(fecha_hora)

        descripcion_evento = (
            f"Entrevista de seleccion para la vacante: {titulo_vacante}\n"
            f"Candidato: {nombre_candidato}\n"
            f"Modalidad: {modalidad}\n"
        )
        if notas:
            descripcion_evento += f"\nNotas del reclutador:\n{notas}\n"
        descripcion_evento += "\nAgendado automaticamente por TalentIA."

        evento: Dict[str, Any] = {
            "summary": f"Entrevista: {nombre_candidato} - {titulo_vacante}",
            "description": descripcion_evento,
            "location": modalidad,
            "start": {"dateTime": fecha_hora.isoformat(), "timeZone": settings.timezone},
            "end": {"dateTime": fin.isoformat(), "timeZone": settings.timezone},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "email", "minutes": 24 * 60},
                    {"method": "popup", "minutes": 30},
                ],
            },
        }
        if email_candidato:
            evento["attendees"] = [{"email": email_candidato, "displayName": nombre_candidato}]

        bloque_enlace = f"  Enlace       : {enlace}\n" if enlace else ""
        bloque_notas = f"\nNotas adicionales:\n{notas}\n" if notas else ""

        correo = {
            "para": email_candidato,
            "asunto": ASUNTO_INVITACION.format(titulo_vacante=titulo_vacante),
            "cuerpo": CUERPO_INVITACION.format(
                nombre=nombre_candidato,
                empresa=settings.nombre_empresa,
                titulo_vacante=titulo_vacante,
                fecha_legible=fecha_legible,
                duracion=duracion_minutos,
                modalidad=modalidad,
                zona=settings.timezone,
                bloque_enlace=bloque_enlace,
                bloque_notas=bloque_notas,
            ),
        }

        return {
            "calendar_event": evento,
            "email": correo,
            "fecha_hora_iso": fecha_hora.isoformat(),
            "fecha_legible": fecha_legible,
            "duracion_minutos": duracion_minutos,
        }

    # -- Operaciones ------------------------------------------------------
    def agendar_entrevista(
        self,
        nombre_candidato: str,
        email_candidato: str,
        titulo_vacante: str,
        fecha_hora: datetime,
        duracion_minutos: int,
        modalidad: str = "Videollamada",
        notas: str = "",
    ) -> Dict[str, Any]:
        """Crea el evento y envia la invitacion. Funcion bloqueante.

        El backend la ejecuta con `asyncio.to_thread` para no bloquear el event loop.
        Devuelve siempre un diccionario de resultado, nunca lanza excepciones.
        """
        payloads = self.construir_payloads(
            nombre_candidato=nombre_candidato,
            email_candidato=email_candidato,
            titulo_vacante=titulo_vacante,
            fecha_hora=fecha_hora,
            duracion_minutos=duracion_minutos,
            modalidad=modalidad,
            notas=notas,
        )

        resultado: Dict[str, Any] = {
            "modo": MODO_SIMULACION,
            "calendar_ok": False,
            "gmail_ok": False,
            "evento_id": "",
            "enlace_evento": "",
            "mensaje": "",
            "payload": payloads,
        }

        if not self._autenticar():
            resultado["mensaje"] = (
                f"MODO SIMULACION: {self.motivo_simulacion} "
                "Se registro el payload que se habria enviado a Google Calendar y Gmail."
            )
            logger.info(
                "[SIMULACION] Evento Calendar: %s | Correo a: %s",
                payloads["calendar_event"]["summary"],
                payloads["email"]["para"] or "(sin correo)",
            )
            return resultado

        resultado["modo"] = MODO_REAL
        mensajes: List[str] = []

        # --- Google Calendar ---
        try:
            evento_creado = (
                self._calendar.events()
                .insert(
                    calendarId=settings.google_calendar_id,
                    body=payloads["calendar_event"],
                    sendUpdates="all" if email_candidato else "none",
                )
                .execute()
            )
            resultado["calendar_ok"] = True
            resultado["evento_id"] = str(evento_creado.get("id", ""))
            resultado["enlace_evento"] = str(evento_creado.get("htmlLink", ""))
            payloads["calendar_event"]["enlace"] = resultado["enlace_evento"]
            mensajes.append("Evento creado en Google Calendar.")
        except HttpError as exc:
            mensajes.append(f"Error de Google Calendar: {exc}")
            logger.warning("Error creando evento de Calendar: %s", exc)
        except Exception as exc:  # pragma: no cover
            mensajes.append(f"Error inesperado en Calendar: {type(exc).__name__}: {exc}")

        # --- Gmail ---
        if email_candidato:
            try:
                mensaje = EmailMessage()
                mensaje["To"] = email_candidato
                mensaje["From"] = settings.email_remitente
                mensaje["Subject"] = payloads["email"]["asunto"]
                cuerpo = payloads["email"]["cuerpo"]
                if resultado["enlace_evento"]:
                    cuerpo += f"\nEvento en el calendario: {resultado['enlace_evento']}\n"
                mensaje.set_content(cuerpo)

                crudo = base64.urlsafe_b64encode(mensaje.as_bytes()).decode()
                self._gmail.users().messages().send(userId="me", body={"raw": crudo}).execute()
                resultado["gmail_ok"] = True
                mensajes.append(f"Invitacion enviada por Gmail a {email_candidato}.")
            except HttpError as exc:
                mensajes.append(f"Error de Gmail: {exc}")
                logger.warning("Error enviando correo por Gmail: %s", exc)
            except Exception as exc:  # pragma: no cover
                mensajes.append(f"Error inesperado en Gmail: {type(exc).__name__}: {exc}")
        else:
            mensajes.append("Sin correo valido: no se envio invitacion por Gmail.")

        resultado["mensaje"] = " ".join(mensajes)
        return resultado


# Instancia compartida por el backend
google_services = GoogleServices()


def _cli() -> None:  # pragma: no cover - utilidad de linea de comandos
    """Autorizacion interactiva unica: genera token.json."""
    print("=" * 70)
    print("TalentIA - Autorizacion de Google Calendar y Gmail")
    print("=" * 70)
    estado = google_services.estado()
    for clave, valor in estado.items():
        print(f"  {clave:24}: {valor}")
    print("-" * 70)

    if not settings.google_credentials_path.exists():
        print(f"ERROR: no se encontro {settings.google_credentials_path}")
        print("Descargue credentials.json desde Google Cloud Console (OAuth 2.0,")
        print("tipo 'Aplicacion de escritorio') y coloquelo en la raiz del proyecto.")
        print("Sin este archivo el sistema funciona igual, en MODO SIMULACION.")
        return

    if google_services.autorizar_interactivo():
        print(f"OK: credenciales guardadas en {settings.google_token_path}")
        print("El backend ya puede crear eventos y enviar correos reales.")
    else:
        print(f"FALLO: {google_services.motivo_simulacion}")


if __name__ == "__main__":  # pragma: no cover
    _cli()


__all__ = ["GoogleServices", "google_services", "MODO_REAL", "MODO_SIMULACION", "SCOPES"]
