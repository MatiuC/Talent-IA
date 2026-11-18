"""Esquemas Pydantic v2 de TalentIA.

Incluye el contrato de Structured Output que se envia a Ollama
(`EvaluacionCV.model_json_schema()`) y los modelos de entrada/salida de la API.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Estados del candidato dentro del flujo
# ---------------------------------------------------------------------------
ESTADO_PENDIENTE = "PENDIENTE_REVISION"
ESTADO_REVISION_MANUAL = "REVISION_MANUAL"
ESTADO_APROBADO = "APROBADO"
ESTADO_RECHAZADO = "RECHAZADO"
ESTADO_ENTREVISTA = "ENTREVISTA_PROGRAMADA"
ESTADO_CONTACTO_PENDIENTE = "CONTACTO_PENDIENTE"
ESTADO_ERROR = "ERROR_PROCESAMIENTO"

ESTADOS_VALIDOS = (
    ESTADO_PENDIENTE,
    ESTADO_REVISION_MANUAL,
    ESTADO_APROBADO,
    ESTADO_RECHAZADO,
    ESTADO_ENTREVISTA,
    ESTADO_CONTACTO_PENDIENTE,
    ESTADO_ERROR,
)

# Expresion regular de validacion de correo (RFC 5322 simplificado)
PATRON_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def email_es_valido(email: Optional[str]) -> bool:
    """Valida un correo electronico extraido del CV."""
    if not email or not isinstance(email, str):
        return False
    return bool(PATRON_EMAIL.match(email.strip()))


# ---------------------------------------------------------------------------
# Vacantes
# ---------------------------------------------------------------------------
class VacanteCreate(BaseModel):
    """Datos para registrar una vacante."""

    model_config = ConfigDict(str_strip_whitespace=True)

    titulo: str = Field(..., min_length=3, max_length=200, description="Titulo del puesto")
    descripcion: str = Field(
        default="", max_length=5000, description="Descripcion y funciones del puesto"
    )
    habilidades_obligatorias: List[str] = Field(
        default_factory=list, description="Habilidades tecnicas obligatorias"
    )
    anios_experiencia_min: int = Field(
        default=0, ge=0, le=50, description="Anios minimos de experiencia exigidos"
    )

    @field_validator("habilidades_obligatorias", mode="before")
    @classmethod
    def _normalizar_habilidades(cls, valor: Any) -> List[str]:
        """Acepta lista o cadena separada por comas/saltos de linea."""
        if valor is None:
            return []
        if isinstance(valor, str):
            partes = re.split(r"[,;\n]", valor)
        elif isinstance(valor, (list, tuple, set)):
            partes = [str(item) for item in valor]
        else:
            return []
        limpias: List[str] = []
        vistas = set()
        for parte in partes:
            texto = str(parte).strip()
            if texto and texto.lower() not in vistas:
                vistas.add(texto.lower())
                limpias.append(texto)
        return limpias


class VacanteOut(VacanteCreate):
    """Vacante almacenada."""

    id: int
    creada_en: str
    total_candidatos: int = 0
    activa: bool = True


# ---------------------------------------------------------------------------
# Contrato de Structured Output para el LLM
# ---------------------------------------------------------------------------
class EvaluacionCV(BaseModel):
    """Esquema estricto que el modelo local debe devolver por cada CV.

    Este es el objeto que se envia a Ollama como `format`, por lo que todos los
    campos son obligatorios: la gramatica de Ollama obliga al modelo a emitirlos.
    El validador `mode="before"` tolera respuestas incompletas en la ruta de
    reintento, donde el JSON puede llegar en texto libre.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    nombre_candidato: str = Field(
        ..., description="Nombre completo del candidato tal como aparece en el CV"
    )
    email: str = Field(
        ..., description="Correo electronico del candidato; cadena vacia si no aparece en el CV"
    )
    anios_experiencia: float = Field(
        ..., ge=0, le=60, description="Anios totales de experiencia profesional detectados"
    )
    habilidades_detectadas: List[str] = Field(
        ..., description="Habilidades tecnicas encontradas explicitamente en el CV"
    )
    puntaje: int = Field(
        ..., ge=1, le=5, description="Puntaje entero de ajuste a la vacante, de 1 (bajo) a 5 (alto)"
    )
    cumple_minimos: bool = Field(
        ..., description="True solo si cumple las habilidades obligatorias y la experiencia minima"
    )
    justificacion_evidencia: str = Field(
        ...,
        description="Justificacion breve citando fragmentos concretos del CV que sustentan el puntaje",
    )

    # -- Normalizacion defensiva ---------------------------------------------
    @model_validator(mode="before")
    @classmethod
    def _completar_campos(cls, datos: Any) -> Any:
        if not isinstance(datos, dict):
            return datos
        normalizado = dict(datos)

        # Alias frecuentes que suelen aparecer en modelos pequenos
        alias = {
            "nombre": "nombre_candidato",
            "candidato": "nombre_candidato",
            "correo": "email",
            "correo_electronico": "email",
            "experiencia": "anios_experiencia",
            "anos_experiencia": "anios_experiencia",
            "años_experiencia": "anios_experiencia",
            "habilidades": "habilidades_detectadas",
            "skills": "habilidades_detectadas",
            "score": "puntaje",
            "puntuacion": "puntaje",
            "justificacion": "justificacion_evidencia",
            "evidencia": "justificacion_evidencia",
        }
        for origen, destino in alias.items():
            if origen in normalizado and destino not in normalizado:
                normalizado[destino] = normalizado.pop(origen)

        valores_defecto = {
            "nombre_candidato": "",
            "email": "",
            "anios_experiencia": 0,
            "habilidades_detectadas": [],
            "puntaje": 1,
            "cumple_minimos": False,
            "justificacion_evidencia": "",
        }
        for campo, defecto in valores_defecto.items():
            if normalizado.get(campo) is None:
                normalizado[campo] = defecto
        return normalizado

    @field_validator("nombre_candidato", mode="before")
    @classmethod
    def _nombre_no_vacio(cls, valor: Any) -> str:
        texto = str(valor or "").strip()
        return texto if texto else "No identificado"

    @field_validator("email", mode="before")
    @classmethod
    def _limpiar_email(cls, valor: Any) -> str:
        texto = str(valor or "").strip().lower()
        # Descarta marcadores tipo "no disponible", "n/a", "null"
        if texto in {"n/a", "na", "null", "none", "no disponible", "-"}:
            return ""
        return texto

    @field_validator("anios_experiencia", mode="before")
    @classmethod
    def _coercionar_experiencia(cls, valor: Any) -> float:
        if isinstance(valor, (int, float)):
            numero = float(valor)
        else:
            encontrado = re.search(r"\d+(?:[.,]\d+)?", str(valor or ""))
            numero = float(encontrado.group(0).replace(",", ".")) if encontrado else 0.0
        return max(0.0, min(60.0, numero))

    @field_validator("habilidades_detectadas", mode="before")
    @classmethod
    def _normalizar_detectadas(cls, valor: Any) -> List[str]:
        if valor is None:
            return []
        if isinstance(valor, str):
            partes = re.split(r"[,;\n]", valor)
        elif isinstance(valor, (list, tuple, set)):
            partes = [str(item) for item in valor]
        else:
            return []
        limpias: List[str] = []
        vistas = set()
        for parte in partes:
            texto = str(parte).strip()
            if texto and texto.lower() not in vistas:
                vistas.add(texto.lower())
                limpias.append(texto)
        return limpias[:40]

    @field_validator("puntaje", mode="before")
    @classmethod
    def _acotar_puntaje(cls, valor: Any) -> int:
        try:
            numero = int(round(float(str(valor).replace(",", ".").strip())))
        except (TypeError, ValueError):
            numero = 1
        return max(1, min(5, numero))

    @field_validator("cumple_minimos", mode="before")
    @classmethod
    def _coercionar_booleano(cls, valor: Any) -> bool:
        if isinstance(valor, bool):
            return valor
        return str(valor).strip().lower() in {"true", "1", "si", "sí", "yes", "y"}

    @field_validator("justificacion_evidencia", mode="before")
    @classmethod
    def _justificacion_texto(cls, valor: Any) -> str:
        if isinstance(valor, (list, tuple)):
            valor = " ".join(str(item) for item in valor)
        texto = str(valor or "").strip()
        return texto if texto else "El modelo no entrego justificacion textual."

    @property
    def email_valido(self) -> bool:
        return email_es_valido(self.email)


# ---------------------------------------------------------------------------
# Resultados de la Fase 1
# ---------------------------------------------------------------------------
class CandidatoEvaluado(BaseModel):
    """Candidato con su evaluacion, listo para el ranking."""

    id: int
    vacante_id: int
    nombre_candidato: str
    email: str = ""
    email_valido: bool = False
    archivo_cv: str
    estado: str = ESTADO_PENDIENTE
    puntaje: Optional[int] = None
    cumple_minimos: Optional[bool] = None
    anios_experiencia: Optional[float] = None
    habilidades_detectadas: List[str] = Field(default_factory=list)
    justificacion_evidencia: str = ""
    latencia_segundos: Optional[float] = None
    throughput_tokens_seg: Optional[float] = None
    creado_en: str = ""
    actualizado_en: str = ""
    detalle: str = ""


class ResultadoLote(BaseModel):
    """Respuesta del endpoint de analisis en lote."""

    vacante_id: int
    sesion: str
    total_recibidos: int
    procesados_ok: int
    revision_manual: int
    errores: int
    duracion_total_segundos: float
    ranking: List[CandidatoEvaluado] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Fase 2: Human-in-the-Loop
# ---------------------------------------------------------------------------
class AprobacionRequest(BaseModel):
    """Decision del reclutador al aprobar un candidato."""

    model_config = ConfigDict(str_strip_whitespace=True)

    fecha_hora_entrevista: datetime = Field(
        ..., description="Fecha y hora local de la entrevista (ISO 8601)"
    )
    duracion_minutos: int = Field(default=45, ge=15, le=240)
    modalidad: str = Field(default="Videollamada", max_length=120)
    notas: str = Field(default="", max_length=1000)
    sesion: str = Field(default="desconocida", max_length=80)
    confirmar_envio_real: bool = Field(
        default=False, description="Confirmacion explicita antes de crear eventos o enviar invitaciones reales"
    )
    email_override: str = Field(
        default="", description="Correo corregido manualmente por el reclutador"
    )

    @field_validator("email_override", mode="before")
    @classmethod
    def _limpiar_override(cls, valor: Any) -> str:
        return str(valor or "").strip().lower()


class RechazoRequest(BaseModel):
    """Decision del reclutador al descartar un candidato."""

    motivo: str = Field(default="", max_length=1000)
    sesion: str = Field(default="desconocida", max_length=80)


class ResultadoAgendamiento(BaseModel):
    """Resultado de la Fase 2 (Calendar + Gmail o modo simulacion)."""

    candidato_id: int
    estado: str
    modo: str = Field(description="'real' cuando se uso la API de Google, 'simulacion' en mock")
    email_destino: str = ""
    email_valido: bool = False
    calendar_ok: bool = False
    gmail_ok: bool = False
    evento_id: str = ""
    enlace_evento: str = ""
    mensaje: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Metricas tecnicas
# ---------------------------------------------------------------------------
class LogMetrica(BaseModel):
    """Una fila de la tabla logs_metricas."""

    id: int
    timestamp: str
    usuario_o_sesion: str
    archivo_cv: str
    latencia_segundos: float
    throughput_tokens_seg: float
    cpu_porcentaje: float
    ram_porcentaje: float
    estado: str
    detalle: str = ""
    vacante_id: Optional[int] = None
    candidato_id: Optional[int] = None
    tokens_generados: Optional[int] = None
    modelo: str = ""


class ResumenMetricas(BaseModel):
    """Tarjetas KPI del dashboard en vivo."""

    total_cvs_procesados: int = 0
    total_exitos: int = 0
    total_errores: int = 0
    latencia_promedio_segundos: float = 0.0
    latencia_maxima_segundos: float = 0.0
    throughput_promedio_tokens_seg: float = 0.0
    tokens_generados_total: int = 0
    cpu_porcentaje_actual: float = 0.0
    ram_porcentaje_actual: float = 0.0
    sesiones_activas: int = 0
    tasa_exito_porcentaje: float = 0.0
    modelo: str = ""


class EstadoSalud(BaseModel):
    """Respuesta de /api/health para verificar el entorno antes de la demo."""

    api_ok: bool = True
    ollama_disponible: bool = False
    modelo_descargado: bool = False
    modelos_disponibles: List[str] = Field(default_factory=list)
    base_datos_ok: bool = False
    google_modo: str = "simulacion"
    cpu_porcentaje: float = 0.0
    ram_porcentaje: float = 0.0
    configuracion: Dict[str, Any] = Field(default_factory=dict)
    mensaje: str = ""


__all__ = [
    "EvaluacionCV",
    "VacanteCreate",
    "VacanteOut",
    "CandidatoEvaluado",
    "ResultadoLote",
    "AprobacionRequest",
    "RechazoRequest",
    "ResultadoAgendamiento",
    "LogMetrica",
    "ResumenMetricas",
    "EstadoSalud",
    "email_es_valido",
    "ESTADO_PENDIENTE",
    "ESTADO_REVISION_MANUAL",
    "ESTADO_APROBADO",
    "ESTADO_RECHAZADO",
    "ESTADO_ENTREVISTA",
    "ESTADO_CONTACTO_PENDIENTE",
    "ESTADO_ERROR",
    "ESTADOS_VALIDOS",
]
