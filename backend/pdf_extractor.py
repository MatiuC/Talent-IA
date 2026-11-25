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
from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple

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


_NUMEROS_ES = {
    "cero": 0.0,
    "medio": 0.5,
    "media": 0.5,
    "un": 1.0,
    "uno": 1.0,
    "una": 1.0,
    "dos": 2.0,
    "tres": 3.0,
    "cuatro": 4.0,
    "cinco": 5.0,
    "seis": 6.0,
    "siete": 7.0,
    "ocho": 8.0,
    "nueve": 9.0,
    "diez": 10.0,
    "once": 11.0,
    "doce": 12.0,
    "trece": 13.0,
    "catorce": 14.0,
    "quince": 15.0,
    "dieciseis": 16.0,
    "diecisiete": 17.0,
    "dieciocho": 18.0,
    "diecinueve": 19.0,
    "veinte": 20.0,
    "veintiun": 21.0,
    "veintiuno": 21.0,
    "veintiuna": 21.0,
    "veintidos": 22.0,
    "veintitres": 23.0,
    "veinticuatro": 24.0,
    "veinticinco": 25.0,
    "veintiseis": 26.0,
    "veintisiete": 27.0,
    "veintiocho": 28.0,
    "veintinueve": 29.0,
    "treinta": 30.0,
}

_MESES_ES = {
    "enero": 1, "ene": 1, "jan": 1,
    "febrero": 2, "feb": 2,
    "marzo": 3, "mar": 3,
    "abril": 4, "abr": 4, "apr": 4,
    "mayo": 5, "may": 5,
    "junio": 6, "jun": 6,
    "julio": 7, "jul": 7,
    "agosto": 8, "ago": 8, "aug": 8,
    "septiembre": 9, "setiembre": 9, "sep": 9, "sept": 9,
    "octubre": 10, "oct": 10,
    "noviembre": 11, "nov": 11,
    "diciembre": 12, "dic": 12, "dec": 12,
}

_TOKEN_NUM = r"(?:\d+(?:[.,]\d+)?|" + "|".join(sorted(_NUMEROS_ES, key=len, reverse=True)) + r")"
_PATRON_EXP_FRASES = (
    re.compile(
        rf"\b(?:con|mas\s+de|aproximadamente|alrededor\s+de|cerca\s+de)?\s*(?P<num>{_TOKEN_NUM})\s*\+?\s*"
        r"anos?(?P<medio>\s+y\s+medi[oa])?\s+de\s+(?:experiencia|trayectoria)\b"
    ),
    re.compile(
        r"\b(?:experiencia|trayectoria)(?:\s+(?:laboral|profesional|comprobada|previa))*\s+"
        rf"(?:de|superior\s+a|mayor\s+a|minima\s+de)\s+(?:mas\s+de\s+|alrededor\s+de\s+)?(?P<num>{_TOKEN_NUM})\s*\+?\s*"
        r"anos?(?P<medio>\s+y\s+medi[oa])?\b"
    ),
)
_PATRON_EXP_MESES = re.compile(
    rf"\b(?P<num>{_TOKEN_NUM})\s+mes(?:es)?\s+de\s+(?:experiencia|trayectoria)\b"
)
_PATRON_TITULO_EXP = re.compile(
    r"^(?:experiencia(?:\s+(?:laboral|profesional))?|trayectoria(?:\s+(?:laboral|profesional))?|historial\s+laboral)\b"
)
_PATRON_OTRA_SECCION = re.compile(
    r"^(?:formacion(?:\s+academica)?|educacion|estudios|habilidades(?:\s+tecnicas)?|competencias|"
    r"idiomas(?:\s+y\s+competencias)?|certificaciones|cursos|proyectos|referencias|logros|perfil(?:\s+profesional)?|resumen)\b"
)
_FECHA_MES_PAT = r"(?:[a-z]+\s*(?:de\s*)?(?:19|20)\d{2}|(?:0?[1-9]|1[0-2])[/\-](?:19|20)\d{2})"
_FECHA_FIN_PAT = rf"(?:{_FECHA_MES_PAT}|actualidad|presente|actual|hoy)"
_PATRON_RANGO_MES = re.compile(
    rf"(?P<ini>{_FECHA_MES_PAT})\s*(?:-|–|—|hasta|a|al)\s*(?P<fin>{_FECHA_FIN_PAT})"
)
_PATRON_RANGO_ANIO = re.compile(
    r"\b(?P<ini>(?:19|20)\d{2})\s*(?:-|–|—|hasta|a|al)\s*(?P<fin>(?:19|20)\d{2}|actualidad|presente|actual|hoy)\b"
)


def _sin_tildes(texto: str) -> str:
    nfd = unicodedata.normalize("NFD", str(texto or ""))
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn").lower()


def parsear_numero_espanol(texto: str) -> Optional[float]:
    """Convierte digitos o numerales en espanol (ej. 'dos anos', 'un ano y medio') a float."""
    norm = _sin_tildes(texto)
    tiene_medio = bool(re.search(r"\by\s+medi[oa]\b", norm))
    limpio = re.sub(r"\by\s+medi[oa]\b", " ", norm)
    m_num = re.search(r"\d+(?:[.,]\d+)?", limpio)
    if m_num:
        valor = float(m_num.group(0).replace(",", "."))
        if tiene_medio and valor.is_integer():
            valor += 0.5
        return valor
    for palabra in re.findall(r"[a-z]+", limpio):
        if palabra in _NUMEROS_ES:
            valor = _NUMEROS_ES[palabra]
            if tiene_medio and valor.is_integer():
                valor += 0.5
            return valor
    return None


def _extraer_bloque_experiencia(texto_norm: str) -> str:
    lineas = [re.sub(r"[:|\-]+$", "", ln.strip()) for ln in texto_norm.splitlines()]
    capturando = False
    recolectadas: List[str] = []
    for ln in lineas:
        if not ln:
            continue
        if _PATRON_TITULO_EXP.match(ln) and len(ln) <= 45:
            capturando = True
            continue
        if capturando and _PATRON_OTRA_SECCION.match(ln) and len(ln) <= 45:
            break
        if capturando:
            recolectadas.append(ln)
    return "\n".join(recolectadas)


def _parsear_fecha_mes(token: str, es_fin: bool) -> Optional[int]:
    tok = token.strip()
    if tok in {"actualidad", "presente", "actual", "hoy"}:
        hoy = date.today()
        return hoy.year * 12 + (hoy.month - 1)
    m_num = re.fullmatch(r"(0?[1-9]|1[0-2])[/\-]((?:19|20)\d{2})", tok)
    if m_num:
        return int(m_num.group(2)) * 12 + (int(m_num.group(1)) - 1)
    m_txt = re.fullmatch(r"([a-z]+)\s*(?:de\s*)?((?:19|20)\d{2})", tok)
    if m_txt and m_txt.group(1) in _MESES_ES:
        return int(m_txt.group(2)) * 12 + (_MESES_ES[m_txt.group(1)] - 1)
    if re.fullmatch(r"(?:19|20)\d{2}", tok):
        anio = int(tok)
        return anio * 12 + (11 if es_fin else 0)
    return None


def _anios_desde_rangos(bloque_exp: str) -> Optional[float]:
    if not bloque_exp:
        return None
    intervalos: List[Tuple[int, int]] = []
    posiciones_mes: List[Tuple[int, int]] = []
    hoy_idx = date.today().year * 12 + (date.today().month - 1)

    for m in _PATRON_RANGO_MES.finditer(bloque_exp):
        ini = _parsear_fecha_mes(m.group("ini"), es_fin=False)
        fin = _parsear_fecha_mes(m.group("fin"), es_fin=True)
        if ini is not None and fin is not None and fin >= ini and (fin - ini) <= 600:
            intervalos.append((ini, min(fin, hoy_idx + 12)))
            posiciones_mes.append((m.start(), m.end()))

    for m in _PATRON_RANGO_ANIO.finditer(bloque_exp):
        if any(not (m.end() <= s or m.start() >= e) for s, e in posiciones_mes):
            continue
        ini = _parsear_fecha_mes(m.group("ini"), es_fin=False)
        fin = _parsear_fecha_mes(m.group("fin"), es_fin=True)
        if ini is not None and fin is not None and fin >= ini and (fin - ini) <= 600:
            intervalos.append((ini, min(fin, hoy_idx + 12)))

    if not intervalos:
        return None
    intervalos.sort()
    unidos: List[Tuple[int, int]] = [intervalos[0]]
    for ini, fin in intervalos[1:]:
        prev_ini, prev_fin = unidos[-1]
        if ini <= prev_fin + 1:
            unidos[-1] = (prev_ini, max(prev_fin, fin))
        else:
            unidos.append((ini, fin))
    meses_totales = sum((fin - ini) + 1 for ini, fin in unidos)
    return round(meses_totales / 12.0, 1) if meses_totales > 0 else None


def estimar_anios_experiencia(texto_cv: str) -> Optional[float]:
    """Estima anios de experiencia laboral a partir de frases explicitas y rangos de fechas.

    Combina menciones textuales (incluidos numerales en espanol como 'dos anos')
    y rangos de fechas dentro de la seccion de experiencia laboral (excluyendo
    formacion academica). Devuelve None si no hay evidencia positiva.
    """
    if not texto_cv:
        return None
    norm = _sin_tildes(texto_cv)
    candidatos: List[float] = []

    for patron in _PATRON_EXP_FRASES:
        for m in patron.finditer(norm):
            bruto = m.group("num") + (m.group("medio") or "")
            val = parsear_numero_espanol(bruto)
            if val is not None and 0.0 < val <= 50.0:
                candidatos.append(round(val, 1))

    for m in _PATRON_EXP_MESES.finditer(norm):
        val = parsear_numero_espanol(m.group("num"))
        if val is not None and 0.0 < val <= 600.0:
            candidatos.append(round(val / 12.0, 1))

    bloque_exp = _extraer_bloque_experiencia(norm)
    por_rangos = _anios_desde_rangos(bloque_exp)
    if por_rangos is not None and 0.0 < por_rangos <= 50.0:
        candidatos.append(por_rangos)

    return round(max(candidatos), 1) if candidatos else None


__all__ = [
    "ResultadoExtraccion",
    "extraer_texto",
    "recortar_para_llm",
    "parsear_numero_espanol",
    "estimar_anios_experiencia",
    "limpiar_texto",
    "contar_caracteres_utiles",
    "PYMUPDF_DISPONIBLE",
]
