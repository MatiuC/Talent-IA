"""Extraccion de texto de currículums en PDF con PyMuPDF (fitz).

Compuerta de legibilidad: si el texto extraido tiene menos de
`MIN_CARACTERES_PDF` caracteres utiles (PDF escaneado como imagen, archivo
corrupto o protegido), el CV se marca como REVISION_MANUAL y NUNCA se envia al
LLM, ahorrando una inferencia inutil.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

try:  # PyMuPDF >= 1.24 expone el paquete como "pymupdf"; "fitz" sigue siendo el alias clasico
    import pymupdf as fitz  # type: ignore[import-not-found]
    PYMUPDF_DISPONIBLE = True
    _ERROR_IMPORT_PYMUPDF = ""
except ImportError:
    try:
        import fitz  # type: ignore[no-redef]
        PYMUPDF_DISPONIBLE = True
        _ERROR_IMPORT_PYMUPDF = ""
    except ImportError as exc:  # pragma: no cover - ruta defensiva
        fitz = None  # type: ignore[assignment]
        PYMUPDF_DISPONIBLE = False
        _ERROR_IMPORT_PYMUPDF = str(exc)

from backend.config import settings

# Caracteres de control y marcas invisibles que ensucian el conteo de legibilidad
_PATRON_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PATRON_ESPACIOS = re.compile(r"[ \t ]+")
_PATRON_SALTOS = re.compile(r"\n{3,}")


@dataclass
class ResultadoExtraccion:
    """Resultado de procesar un PDF."""

    texto: str = ""
    legible: bool = False
    num_caracteres: int = 0
    num_paginas: int = 0
    motivo: str = ""
    advertencias: List[str] = field(default_factory=list)

    @property
    def requiere_revision_manual(self) -> bool:
        return not self.legible


def limpiar_texto(bruto: str) -> str:
    """Normaliza el texto extraido conservando la estructura por lineas."""
    if not bruto:
        return ""
    texto = unicodedata.normalize("NFKC", bruto)
    texto = _PATRON_CONTROL.sub(" ", texto)
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    texto = _PATRON_ESPACIOS.sub(" ", texto)
    lineas = [linea.strip() for linea in texto.split("\n")]
    texto = "\n".join(lineas)
    texto = _PATRON_SALTOS.sub("\n\n", texto)
    return texto.strip()


def contar_caracteres_utiles(texto: str) -> int:
    """Cuenta solo caracteres alfanumericos: evita que un PDF con cientos de
    espacios o vinetas pase el filtro de legibilidad."""
    return sum(1 for caracter in texto if caracter.isalnum())


def extraer_texto(ruta_pdf: str | Path) -> ResultadoExtraccion:
    """Extrae el texto de un PDF y determina si es legible.

    Es una funcion sincronica y bloqueante (CPU): el backend la invoca mediante
    `asyncio.to_thread` para no bloquear el event loop.
    """
    ruta = Path(ruta_pdf)

    if not PYMUPDF_DISPONIBLE:
        return ResultadoExtraccion(
            motivo=(
                "PyMuPDF no esta instalado. Ejecute: pip install -r requirements.txt "
                f"(detalle: {_ERROR_IMPORT_PYMUPDF})"
            )
        )

    if not ruta.exists():
        return ResultadoExtraccion(motivo=f"El archivo no existe: {ruta.name}")

    if ruta.stat().st_size == 0:
        return ResultadoExtraccion(motivo=f"El archivo esta vacio (0 bytes): {ruta.name}")

    documento = None
    try:
        documento = fitz.open(str(ruta))

        if getattr(documento, "needs_pass", False):
            return ResultadoExtraccion(
                num_paginas=documento.page_count,
                motivo="El PDF esta protegido con contrasena y no puede leerse.",
            )

        partes: List[str] = []
        advertencias: List[str] = []
        for indice in range(documento.page_count):
            try:
                pagina = documento.load_page(indice)
                partes.append(pagina.get_text("text") or "")
            except Exception as exc:  # pagina corrupta puntual
                advertencias.append(f"Pagina {indice + 1} ilegible: {exc}")

        texto = limpiar_texto("\n".join(partes))
        utiles = contar_caracteres_utiles(texto)
        legible = utiles >= settings.min_caracteres_pdf

        motivo = ""
        if not legible:
            motivo = (
                f"Texto extraido insuficiente ({utiles} caracteres utiles, minimo "
                f"{settings.min_caracteres_pdf}). Probable PDF escaneado como imagen "
                "o archivo corrupto: requiere OCR o revision manual."
            )

        return ResultadoExtraccion(
            texto=texto,
            legible=legible,
            num_caracteres=utiles,
            num_paginas=documento.page_count,
            motivo=motivo,
            advertencias=advertencias,
        )

    except Exception as exc:
        return ResultadoExtraccion(motivo=f"Error al abrir el PDF: {type(exc).__name__}: {exc}")
    finally:
        if documento is not None:
            try:
                documento.close()
            except Exception:  # pragma: no cover
                pass


def recortar_para_llm(texto: str, maximo: int | None = None) -> str:
    """Recorta el CV al limite de caracteres admitido por la ventana de contexto.

    Conserva el inicio del documento (datos personales, perfil y experiencia
    reciente), que es donde se concentra la informacion que evalua el agente.
    """
    limite = maximo if maximo is not None else settings.llm_max_caracteres_cv
    if limite <= 0 or len(texto) <= limite:
        return texto
    recorte = texto[:limite]
    ultimo_salto = recorte.rfind("\n")
    if ultimo_salto > limite * 0.6:
        recorte = recorte[:ultimo_salto]
    return recorte.rstrip() + "\n\n[... texto truncado por limite de contexto ...]"


__all__ = [
    "ResultadoExtraccion",
    "extraer_texto",
    "recortar_para_llm",
    "limpiar_texto",
    "contar_caracteres_utiles",
    "PYMUPDF_DISPONIBLE",
]
