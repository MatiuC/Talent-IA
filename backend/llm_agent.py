"""Cliente asincrono del modelo local (Ollama) y nucleo cognitivo del agente.

Responsabilidades:
  * Mantener un unico `httpx.AsyncClient` para todo el backend.
  * Limitar las inferencias simultaneas a `OLLAMA_NUM_PARALLEL` mediante un
    semaforo, de modo que 3 usuarios concurrentes encolen de forma ordenada en
    lugar de saturar el motor.
  * Enviar el CV y los requisitos de la vacante con Structured Outputs
    (`format = EvaluacionCV.model_json_schema()`), `stream: False` y
    `options: {"temperature": 0.1}`.
  * Calcular el throughput real en tokens/s a partir de la telemetria que
    devuelve Ollama (`eval_count` / (`eval_duration` / 1e9)).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx
from pydantic import ValidationError

from backend.config import settings
from backend.schemas import EvaluacionCV

logger = logging.getLogger("talentia.llm")

# ---------------------------------------------------------------------------
# System Prompt del agente
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """Eres un analista objetivo de RR.HH. para PYMEs. Responde SOLO con un JSON valido en espanol que cumpla el esquema.
Reglas:
1. Usa unicamente datos literales del CV; prohibido inventar o suponer. Si falta un dato usa "" o 0.
2. anios_experiencia: suma real de experiencia profesional evidenciada (0 si no consta).
3. habilidades_detectadas: solo tecnologias/herramientas mencionadas literalmente en el CV.
4. cumple_minimos: true SOLO si cumple TODAS las habilidades obligatorias Y los anios minimos.
5. puntaje (1-5): 5=cumple todo + diferencial relevante; 4=cumple todos los minimos; 3=cumple mayoria (falta 1 requisito); 2=parcial (faltan varios); 1=no corresponde al perfil.
6. justificacion_evidencia: 2-3 frases en espanol citando fragmentos literales del CV entre comillas.
7. Sin texto adicional ni markdown."""

PLANTILLA_USUARIO = """### VACANTE A CUBRIR
Titulo: {titulo}
Descripcion: {descripcion}
Habilidades obligatorias: {habilidades}
Anios de experiencia minima requeridos: {anios_min}

### CURRICULUM DEL CANDIDATO (texto extraido del PDF)
<<<INICIO_CV
{texto_cv}
FIN_CV>>>

### TAREA
Analiza el CV frente a la vacante y devuelve el JSON del esquema con: \
nombre_candidato, email, anios_experiencia, habilidades_detectadas, puntaje (1-5), \
cumple_minimos y justificacion_evidencia con evidencia textual del CV."""

REFUERZO_REINTENTO = (
    "\n\n### AVISO IMPORTANTE\n"
    "El intento anterior no produjo un JSON valido. Devuelve UNICAMENTE el objeto JSON "
    "con las claves exactas nombre_candidato, email, anios_experiencia, "
    "habilidades_detectadas, puntaje, cumple_minimos y justificacion_evidencia. "
    "Sin markdown, sin comentarios y sin texto adicional."
)

# Etiquetas de razonamiento que algunos modelos emiten dentro del contenido
_PATRON_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_PATRON_CERCA_CODIGO = re.compile(r"^```(?:json)?|```$", re.MULTILINE)


class LLMError(RuntimeError):
    """Error controlado durante la inferencia."""


@dataclass
class MetricasInferencia:
    """Telemetria devuelta por Ollama para una inferencia."""

    latencia_llm_segundos: float = 0.0
    tokens_generados: int = 0
    tokens_prompt: int = 0
    throughput_tokens_seg: float = 0.0
    modelo: str = ""
    intentos: int = 1
    total_duration_segundos: float = 0.0
    load_duration_segundos: float = 0.0
    advertencias: List[str] = field(default_factory=list)

    def como_dict(self) -> Dict[str, Any]:
        return {
            "latencia_llm_segundos": round(self.latencia_llm_segundos, 4),
            "tokens_generados": self.tokens_generados,
            "tokens_prompt": self.tokens_prompt,
            "throughput_tokens_seg": round(self.throughput_tokens_seg, 2),
            "modelo": self.modelo,
            "intentos": self.intentos,
            "total_duration_segundos": round(self.total_duration_segundos, 4),
            "load_duration_segundos": round(self.load_duration_segundos, 4),
            "advertencias": self.advertencias,
        }


# ---------------------------------------------------------------------------
# Cliente HTTP compartido y control de concurrencia
# ---------------------------------------------------------------------------
_cliente: Optional[httpx.AsyncClient] = None
_semaforo: Optional[asyncio.Semaphore] = None
_bloqueo_init = asyncio.Lock()
# Se desactiva si Ollama rechaza el parametro "think" (modelos sin modo razonamiento)
_soporta_think: bool = True


async def iniciar_cliente() -> httpx.AsyncClient:
    """Crea el AsyncClient y el semaforo. Se invoca en el arranque de FastAPI."""
    global _cliente, _semaforo
    async with _bloqueo_init:
        if _cliente is None or _cliente.is_closed:
            limites = httpx.Limits(
                max_connections=settings.ollama_num_parallel * 4,
                max_keepalive_connections=settings.ollama_num_parallel * 2,
            )
            _cliente = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.llm_timeout, connect=10.0),
                limits=limites,
            )
        if _semaforo is None:
            _semaforo = asyncio.Semaphore(settings.ollama_num_parallel)
    return _cliente


async def cerrar_cliente() -> None:
    """Cierra el AsyncClient en el apagado de FastAPI."""
    global _cliente
    async with _bloqueo_init:
        if _cliente is not None and not _cliente.is_closed:
            await _cliente.aclose()
        _cliente = None


async def obtener_cliente() -> httpx.AsyncClient:
    if _cliente is None or _cliente.is_closed:
        return await iniciar_cliente()
    return _cliente


async def obtener_semaforo() -> asyncio.Semaphore:
    global _semaforo
    if _semaforo is None:
        await iniciar_cliente()
    assert _semaforo is not None
    return _semaforo


def inferencias_en_curso() -> int:
    """Cuantas inferencias estan ocupando un cupo del semaforo."""
    if _semaforo is None:
        return 0
    return max(0, settings.ollama_num_parallel - _semaforo._value)  # noqa: SLF001


# ---------------------------------------------------------------------------
# Utilidades de parseo
# ---------------------------------------------------------------------------
def limpiar_contenido(contenido: str) -> str:
    """Quita bloques de razonamiento y cercas markdown del contenido devuelto."""
    texto = _PATRON_THINK.sub("", contenido or "")
    texto = _PATRON_CERCA_CODIGO.sub("", texto)
    return texto.strip()


def extraer_json(contenido: str) -> Dict[str, Any]:
    """Obtiene el primer objeto JSON equilibrado dentro del texto."""
    texto = limpiar_contenido(contenido)
    if not texto:
        raise LLMError("El modelo devolvio una respuesta vacia.")

    try:
        datos = json.loads(texto)
        if isinstance(datos, dict):
            return datos
    except json.JSONDecodeError:
        pass

    inicio = texto.find("{")
    if inicio == -1:
        raise LLMError("La respuesta del modelo no contiene un objeto JSON.")

    profundidad = 0
    dentro_de_cadena = False
    escape = False
    for posicion in range(inicio, len(texto)):
        caracter = texto[posicion]
        if escape:
            escape = False
            continue
        if caracter == "\\":
            escape = True
            continue
        if caracter == '"':
            dentro_de_cadena = not dentro_de_cadena
            continue
        if dentro_de_cadena:
            continue
        if caracter == "{":
            profundidad += 1
        elif caracter == "}":
            profundidad -= 1
            if profundidad == 0:
                fragmento = texto[inicio : posicion + 1]
                try:
                    datos = json.loads(fragmento)
                except json.JSONDecodeError as exc:
                    raise LLMError(f"JSON malformado devuelto por el modelo: {exc}") from exc
                if not isinstance(datos, dict):
                    raise LLMError("El JSON devuelto no es un objeto.")
                return datos

    raise LLMError("La respuesta del modelo contiene un JSON incompleto.")


def calcular_throughput(eval_count: Any, eval_duration: Any) -> float:
    """tokens/s = eval_count / (eval_duration / 1e9), segun la telemetria de Ollama."""
    try:
        tokens = int(eval_count or 0)
        duracion_ns = float(eval_duration or 0)
    except (TypeError, ValueError):
        return 0.0
    if tokens <= 0 or duracion_ns <= 0:
        return 0.0
    return tokens / (duracion_ns / 1e9)


def construir_prompt_usuario(
    texto_cv: str,
    titulo: str,
    descripcion: str,
    habilidades: List[str],
    anios_min: int,
) -> str:
    return PLANTILLA_USUARIO.format(
        titulo=titulo or "(sin titulo)",
        descripcion=(descripcion or "(sin descripcion)").strip(),
        habilidades=", ".join(habilidades) if habilidades else "(no especificadas)",
        anios_min=anios_min,
        texto_cv=texto_cv.strip(),
    )


# ---------------------------------------------------------------------------
# Llamada al modelo
# ---------------------------------------------------------------------------
async def _llamar_ollama(mensajes: List[Dict[str, str]]) -> Dict[str, Any]:
    """Una peticion a /api/chat con Structured Outputs. Devuelve el JSON crudo."""
    global _soporta_think

    cliente = await obtener_cliente()
    carga: Dict[str, Any] = {
        "model": settings.ollama_model,
        "messages": mensajes,
        "stream": False,
        "format": EvaluacionCV.model_json_schema(),
        "options": {
            "temperature": settings.llm_temperatura,
            "num_ctx": settings.llm_num_ctx,
        },
        "keep_alive": settings.ollama_keep_alive,
    }
    # Los modelos de la familia Qwen3 razonan por defecto; se desactiva para que
    # el contenido sea JSON puro. Si el modelo no admite el parametro, se reintenta sin el.
    if _soporta_think and not settings.ollama_think:
        carga["think"] = False

    try:
        respuesta = await cliente.post(settings.ollama_chat_url, json=carga)
    except httpx.ConnectError as exc:
        raise LLMError(
            f"No se pudo conectar con Ollama en {settings.ollama_base_url}. "
            "Verifique que el servicio este iniciado (ollama serve)."
        ) from exc
    except httpx.ReadTimeout as exc:
        raise LLMError(
            f"Ollama no respondio en {settings.llm_timeout:.0f} s. "
            "Aumente LLM_TIMEOUT_SEGUNDOS o reduzca el tamano del lote."
        ) from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"Error HTTP al contactar Ollama: {type(exc).__name__}: {exc}") from exc

    if respuesta.status_code != 200:
        detalle = respuesta.text[:500]
        # Reintento automatico si el modelo no soporta el modo "think"
        if "think" in detalle.lower() and "think" in carga:
            _soporta_think = False
            logger.warning(
                "El modelo %s no admite el parametro 'think'; se reintenta sin el.",
                settings.ollama_model,
            )
            carga.pop("think", None)
            respuesta = await cliente.post(settings.ollama_chat_url, json=carga)
            if respuesta.status_code == 200:
                return respuesta.json()
            detalle = respuesta.text[:500]
        raise LLMError(f"Ollama devolvio HTTP {respuesta.status_code}: {detalle}")

    try:
        return respuesta.json()
    except ValueError as exc:
        raise LLMError("Ollama devolvio una respuesta que no es JSON.") from exc


async def evaluar_cv(
    texto_cv: str,
    titulo: str,
    descripcion: str,
    habilidades_obligatorias: List[str],
    anios_experiencia_min: int,
) -> tuple[EvaluacionCV, MetricasInferencia]:
    """Evalua un CV contra una vacante y devuelve la evaluacion validada.

    Lanza `LLMError` si tras `LLM_MAX_REINTENTOS` intentos no se obtiene un JSON
    que cumpla el esquema `EvaluacionCV`.
    """
    prompt_usuario = construir_prompt_usuario(
        texto_cv, titulo, descripcion, habilidades_obligatorias, anios_experiencia_min
    )

    metricas = MetricasInferencia(modelo=settings.ollama_model)
    ultimo_error: Optional[Exception] = None
    semaforo = await obtener_semaforo()

    for intento in range(1, settings.llm_max_reintentos + 1):
        metricas.intentos = intento
        contenido_usuario = prompt_usuario if intento == 1 else prompt_usuario + REFUERZO_REINTENTO
        mensajes = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": contenido_usuario},
        ]

        inicio = time.perf_counter()
        try:
            # El semaforo alinea la concurrencia del backend con OLLAMA_NUM_PARALLEL
            async with semaforo:
                datos = await _llamar_ollama(mensajes)
            transcurrido = time.perf_counter() - inicio

            metricas.latencia_llm_segundos = transcurrido
            metricas.tokens_generados = int(datos.get("eval_count") or 0)
            metricas.tokens_prompt = int(datos.get("prompt_eval_count") or 0)
            metricas.throughput_tokens_seg = calcular_throughput(
                datos.get("eval_count"), datos.get("eval_duration")
            )
            metricas.total_duration_segundos = float(datos.get("total_duration") or 0) / 1e9
            metricas.load_duration_segundos = float(datos.get("load_duration") or 0) / 1e9
            metricas.modelo = str(datos.get("model") or settings.ollama_model)

            # Si Ollama no reporto eval_duration, se estima con el tiempo de pared
            if metricas.throughput_tokens_seg <= 0 and metricas.tokens_generados > 0 and transcurrido > 0:
                metricas.throughput_tokens_seg = metricas.tokens_generados / transcurrido
                metricas.advertencias.append("throughput estimado con tiempo de pared")

            contenido = (datos.get("message") or {}).get("content", "")
            crudo = extraer_json(contenido)
            evaluacion = EvaluacionCV.model_validate(crudo)
            return evaluacion, metricas

        except (LLMError, ValidationError) as exc:
            ultimo_error = exc
            metricas.advertencias.append(f"intento {intento}: {type(exc).__name__}")
            logger.warning(
                "Fallo de inferencia (intento %s/%s): %s",
                intento,
                settings.llm_max_reintentos,
                exc,
            )
            if intento < settings.llm_max_reintentos:
                await asyncio.sleep(0.4 * intento)

    raise LLMError(
        f"No se obtuvo una evaluacion valida tras {settings.llm_max_reintentos} intento(s). "
        f"Ultimo error: {ultimo_error}"
    )


# ---------------------------------------------------------------------------
# Diagnostico del motor
# ---------------------------------------------------------------------------
async def verificar_ollama() -> Dict[str, Any]:
    """Comprueba que Ollama responde y que el modelo configurado esta descargado."""
    resultado: Dict[str, Any] = {
        "disponible": False,
        "modelo_descargado": False,
        "modelos": [],
        "mensaje": "",
    }
    try:
        cliente = await obtener_cliente()
        respuesta = await cliente.get(settings.ollama_tags_url, timeout=10.0)
        if respuesta.status_code != 200:
            resultado["mensaje"] = f"Ollama respondio HTTP {respuesta.status_code}"
            return resultado

        datos = respuesta.json()
        modelos = [str(item.get("name", "")) for item in datos.get("models", [])]
        resultado["disponible"] = True
        resultado["modelos"] = modelos

        objetivo = settings.ollama_model
        base_objetivo = objetivo.split(":")[0]
        resultado["modelo_descargado"] = any(
            nombre == objetivo or nombre.split(":")[0] == base_objetivo for nombre in modelos
        )
        if resultado["modelo_descargado"]:
            resultado["mensaje"] = f"Ollama operativo con el modelo {objetivo}."
        else:
            resultado["mensaje"] = (
                f"Ollama esta activo pero el modelo '{objetivo}' no aparece descargado. "
                f"Ejecute: ollama pull {objetivo}"
            )
        return resultado

    except httpx.HTTPError as exc:
        resultado["mensaje"] = (
            f"No se pudo contactar Ollama en {settings.ollama_base_url}: "
            f"{type(exc).__name__}. Inicie el servicio con 'ollama serve'."
        )
        return resultado
    except Exception as exc:  # pragma: no cover
        resultado["mensaje"] = f"Error inesperado al verificar Ollama: {exc}"
        return resultado


__all__ = [
    "SYSTEM_PROMPT",
    "LLMError",
    "MetricasInferencia",
    "evaluar_cv",
    "verificar_ollama",
    "iniciar_cliente",
    "cerrar_cliente",
    "obtener_cliente",
    "inferencias_en_curso",
    "calcular_throughput",
    "extraer_json",
    "limpiar_contenido",
]
