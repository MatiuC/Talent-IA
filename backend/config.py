"""Lectura de variables de entorno y resolucion de rutas dinamicas.

Todas las rutas se resuelven respecto a la raiz del repositorio, de modo que el
backend funciona igual sin importar desde que carpeta se ejecute uvicorn.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

try:  # python-dotenv es opcional: si falta, se usan las variables del sistema
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - ruta defensiva
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False


# Raiz del repositorio: .../talentia-local
BASE_DIR: Path = Path(__file__).resolve().parent.parent

# Carga el .env local del desarrollador (si existe). No sobreescribe variables
# ya definidas en el sistema operativo.
load_dotenv(BASE_DIR / ".env", override=False)

_VERDADEROS = {"1", "true", "t", "yes", "y", "si", "sí", "on"}


def _texto(nombre: str, por_defecto: str) -> str:
    valor = os.getenv(nombre)
    if valor is None:
        return por_defecto
    valor = valor.strip()
    return valor if valor else por_defecto


def _entero(nombre: str, por_defecto: int) -> int:
    try:
        return int(_texto(nombre, str(por_defecto)))
    except (TypeError, ValueError):
        return por_defecto


def _flotante(nombre: str, por_defecto: float) -> float:
    try:
        return float(_texto(nombre, str(por_defecto)))
    except (TypeError, ValueError):
        return por_defecto


def _booleano(nombre: str, por_defecto: bool) -> bool:
    return _texto(nombre, str(por_defecto)).lower() in _VERDADEROS


def _ruta(nombre: str, por_defecto: str) -> Path:
    """Resuelve una ruta relativa contra la raiz del repositorio."""
    bruto = Path(_texto(nombre, por_defecto)).expanduser()
    return bruto if bruto.is_absolute() else (BASE_DIR / bruto)


class Settings:
    """Configuracion efectiva del prototipo."""

    def __init__(self) -> None:
        # --- Motor LLM local ------------------------------------------------
        self.ollama_base_url: str = _texto("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.ollama_model: str = _texto("OLLAMA_MODEL", "qwen3.5:2b")
        self.ollama_num_parallel: int = max(1, _entero("OLLAMA_NUM_PARALLEL", 3))
        self.llm_timeout: float = _flotante("LLM_TIMEOUT_SEGUNDOS", 180.0)
        self.llm_max_reintentos: int = max(1, _entero("LLM_MAX_REINTENTOS", 2))
        self.ollama_think: bool = _booleano("OLLAMA_THINK", False)
        self.ollama_keep_alive: str = _texto("OLLAMA_KEEP_ALIVE", "30m")
        self.llm_num_ctx: int = _entero("LLM_NUM_CTX", 8192)
        self.llm_temperatura: float = _flotante("LLM_TEMPERATURA", 0.1)
        self.llm_max_caracteres_cv: int = _entero("LLM_MAX_CARACTERES_CV", 12000)

        # --- Backend --------------------------------------------------------
        self.backend_url: str = _texto("BACKEND_URL", "http://localhost:8000").rstrip("/")
        self.api_host: str = _texto("API_HOST", "0.0.0.0")
        self.api_port: int = _entero("API_PORT", 8000)
        self.cors_origins: List[str] = [
            origen.strip()
            for origen in _texto("CORS_ORIGINS", "*").split(",")
            if origen.strip()
        ] or ["*"]

        # --- Base de datos --------------------------------------------------
        self.database_path: Path = _ruta("DATABASE_PATH", "data/talentia.db")
        self.sqlite_timeout: float = _flotante("SQLITE_TIMEOUT", 30.0)

        # --- Almacenamiento de archivos -------------------------------------
        self.uploads_dir: Path = _ruta("UPLOADS_DIR", "data/uploads")

        # --- Reglas de negocio ----------------------------------------------
        self.min_caracteres_pdf: int = _entero("MIN_CARACTERES_PDF", 50)
        self.max_pdf_mb: float = _flotante("MAX_PDF_MB", 15.0)

        # --- Integraciones Google -------------------------------------------
        self.google_credentials_path: Path = _ruta("GOOGLE_CREDENTIALS_PATH", "credentials.json")
        self.google_token_path: Path = _ruta("GOOGLE_TOKEN_PATH", "token.json")
        self.google_calendar_id: str = _texto("GOOGLE_CALENDAR_ID", "primary")
        self.timezone: str = _texto("TIMEZONE", "America/Guayaquil")
        self.duracion_entrevista_minutos: int = _entero("DURACION_ENTREVISTA_MINUTOS", 45)
        self.forzar_simulacion: bool = _booleano("FORZAR_SIMULACION", False)
        self.email_remitente: str = _texto("EMAIL_REMITENTE", "me")
        self.nombre_empresa: str = _texto("NOMBRE_EMPRESA", "TalentIA - Reclutamiento PYME")

        self._preparar_directorios()

    # ------------------------------------------------------------------
    @property
    def ollama_chat_url(self) -> str:
        """Endpoint de chat de Ollama."""
        return f"{self.ollama_base_url}/api/chat"

    @property
    def ollama_tags_url(self) -> str:
        """Endpoint que lista los modelos descargados localmente."""
        return f"{self.ollama_base_url}/api/tags"

    @property
    def max_pdf_bytes(self) -> int:
        return int(self.max_pdf_mb * 1024 * 1024)

    def _preparar_directorios(self) -> None:
        """Crea las carpetas necesarias para datos y cargas."""
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def resumen(self) -> dict:
        """Vista no sensible de la configuracion, util para /api/health."""
        return {
            "ollama_base_url": self.ollama_base_url,
            "ollama_model": self.ollama_model,
            "ollama_num_parallel": self.ollama_num_parallel,
            "llm_timeout_segundos": self.llm_timeout,
            "llm_max_reintentos": self.llm_max_reintentos,
            "database_path": str(self.database_path),
            "uploads_dir": str(self.uploads_dir),
            "min_caracteres_pdf": self.min_caracteres_pdf,
            "timezone": self.timezone,
            "forzar_simulacion": self.forzar_simulacion,
        }


settings = Settings()

__all__ = ["settings", "Settings", "BASE_DIR"]
