"""Capa de persistencia SQLite de TalentIA.

La base de datos se crea automaticamente en la primera ejecucion, de modo que
cada integrante del equipo trabaja con su propio archivo local (ignorado por Git).

Notas de concurrencia:
  * Se habilita WAL para permitir lecturas simultaneas durante las escrituras.
  * Cada operacion abre y cierra su propia conexion, de modo que es seguro
    ejecutarlas desde distintos hilos mediante `asyncio.to_thread`.
  * `busy_timeout` evita el error "database is locked" cuando los 3 usuarios
    concurrentes escriben al mismo tiempo.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

from backend.config import settings

# ---------------------------------------------------------------------------
# Definicion del esquema
# ---------------------------------------------------------------------------
ESQUEMA_SQL = """
CREATE TABLE IF NOT EXISTS vacantes (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo                  TEXT    NOT NULL,
    descripcion             TEXT    NOT NULL DEFAULT '',
    habilidades_obligatorias TEXT   NOT NULL DEFAULT '[]',
    anios_experiencia_min   INTEGER NOT NULL DEFAULT 0,
    creada_en               TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS candidatos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    vacante_id      INTEGER NOT NULL,
    nombre_candidato TEXT   NOT NULL DEFAULT 'No identificado',
    email           TEXT    NOT NULL DEFAULT '',
    email_valido    INTEGER NOT NULL DEFAULT 0,
    archivo_cv      TEXT    NOT NULL,
    ruta_archivo    TEXT    NOT NULL DEFAULT '',
    estado          TEXT    NOT NULL DEFAULT 'PENDIENTE_REVISION',
    detalle         TEXT    NOT NULL DEFAULT '',
    sesion          TEXT    NOT NULL DEFAULT '',
    creado_en       TEXT    NOT NULL,
    actualizado_en  TEXT    NOT NULL,
    FOREIGN KEY (vacante_id) REFERENCES vacantes (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evaluaciones (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    candidato_id            INTEGER NOT NULL UNIQUE,
    vacante_id              INTEGER NOT NULL,
    anios_experiencia       REAL    NOT NULL DEFAULT 0,
    habilidades_detectadas  TEXT    NOT NULL DEFAULT '[]',
    puntaje                 INTEGER NOT NULL DEFAULT 1,
    cumple_minimos          INTEGER NOT NULL DEFAULT 0,
    justificacion_evidencia TEXT    NOT NULL DEFAULT '',
    modelo                  TEXT    NOT NULL DEFAULT '',
    latencia_segundos       REAL    NOT NULL DEFAULT 0,
    throughput_tokens_seg   REAL    NOT NULL DEFAULT 0,
    creada_en               TEXT    NOT NULL,
    FOREIGN KEY (candidato_id) REFERENCES candidatos (id) ON DELETE CASCADE,
    FOREIGN KEY (vacante_id)   REFERENCES vacantes (id)   ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS entrevistas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    candidato_id    INTEGER NOT NULL,
    fecha_hora      TEXT    NOT NULL,
    duracion_minutos INTEGER NOT NULL DEFAULT 45,
    modalidad       TEXT    NOT NULL DEFAULT '',
    modo            TEXT    NOT NULL DEFAULT 'simulacion',
    evento_id       TEXT    NOT NULL DEFAULT '',
    enlace_evento   TEXT    NOT NULL DEFAULT '',
    email_destino   TEXT    NOT NULL DEFAULT '',
    calendar_ok     INTEGER NOT NULL DEFAULT 0,
    gmail_ok        INTEGER NOT NULL DEFAULT 0,
    payload         TEXT    NOT NULL DEFAULT '{}',
    creada_en       TEXT    NOT NULL,
    FOREIGN KEY (candidato_id) REFERENCES candidatos (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS logs_metricas (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp             TEXT    NOT NULL,
    usuario_o_sesion      TEXT    NOT NULL DEFAULT 'desconocida',
    archivo_cv            TEXT    NOT NULL DEFAULT '',
    latencia_segundos     REAL    NOT NULL DEFAULT 0,
    throughput_tokens_seg REAL    NOT NULL DEFAULT 0,
    cpu_porcentaje        REAL    NOT NULL DEFAULT 0,
    ram_porcentaje        REAL    NOT NULL DEFAULT 0,
    estado                TEXT    NOT NULL DEFAULT 'EXITO',
    detalle               TEXT    NOT NULL DEFAULT '',
    vacante_id            INTEGER,
    candidato_id          INTEGER,
    tokens_generados      INTEGER NOT NULL DEFAULT 0,
    modelo                TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_candidatos_vacante ON candidatos (vacante_id);
CREATE INDEX IF NOT EXISTS idx_candidatos_estado  ON candidatos (estado);
CREATE INDEX IF NOT EXISTS idx_eval_candidato     ON evaluaciones (candidato_id);
CREATE INDEX IF NOT EXISTS idx_logs_timestamp     ON logs_metricas (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_logs_sesion        ON logs_metricas (usuario_o_sesion);
"""


def ahora_iso() -> str:
    """Marca de tiempo local en formato ISO 8601 con segundos."""
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Conexion
# ---------------------------------------------------------------------------
@contextmanager
def conexion() -> Iterator[sqlite3.Connection]:
    """Entrega una conexion configurada para uso concurrente."""
    conn = sqlite3.connect(
        str(settings.database_path),
        timeout=settings.sqlite_timeout,
        isolation_level=None,  # autocommit: controlamos las transacciones a mano
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={int(settings.sqlite_timeout * 1000)}")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
    finally:
        conn.close()


@contextmanager
def transaccion() -> Iterator[sqlite3.Connection]:
    """Conexion con transaccion explicita (BEGIN IMMEDIATE / COMMIT / ROLLBACK)."""
    with conexion() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise


def init_db() -> None:
    """Crea las tablas e indices si no existen. Idempotente."""
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    with conexion() as conn:
        conn.executescript(ESQUEMA_SQL)
        if "activa" not in [r[1] for r in conn.execute("PRAGMA table_info(vacantes)")]:
            conn.execute("ALTER TABLE vacantes ADD COLUMN activa INTEGER NOT NULL DEFAULT 1")


def base_datos_disponible() -> bool:
    """Comprueba que la base responde (usado por /api/health)."""
    try:
        with conexion() as conn:
            conn.execute("SELECT 1 FROM vacantes LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False


def _json_cargar(texto: Any, por_defecto: Any) -> Any:
    try:
        valor = json.loads(texto) if texto else por_defecto
        return valor if valor is not None else por_defecto
    except (TypeError, ValueError):
        return por_defecto


# ---------------------------------------------------------------------------
# Vacantes
# ---------------------------------------------------------------------------
def crear_vacante(
    titulo: str,
    descripcion: str,
    habilidades_obligatorias: List[str],
    anios_experiencia_min: int,
) -> int:
    with transaccion() as conn:
        cursor = conn.execute(
            """
            INSERT INTO vacantes
                (titulo, descripcion, habilidades_obligatorias, anios_experiencia_min, creada_en)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                titulo,
                descripcion,
                json.dumps(habilidades_obligatorias, ensure_ascii=False),
                int(anios_experiencia_min),
                ahora_iso(),
            ),
        )
        return int(cursor.lastrowid)


def _fila_a_vacante(fila: sqlite3.Row, total: int = 0) -> Dict[str, Any]:
    return {
        "id": fila["id"],
        "titulo": fila["titulo"],
        "activa": bool(fila["activa"]),
        "descripcion": fila["descripcion"],
        "habilidades_obligatorias": _json_cargar(fila["habilidades_obligatorias"], []),
        "anios_experiencia_min": fila["anios_experiencia_min"],
        "creada_en": fila["creada_en"],
        "total_candidatos": total,
    }


def obtener_vacante(vacante_id: int) -> Optional[Dict[str, Any]]:
    with conexion() as conn:
        fila = conn.execute("SELECT * FROM vacantes WHERE id = ?", (vacante_id,)).fetchone()
        if fila is None:
            return None
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM candidatos WHERE vacante_id = ?", (vacante_id,)
        ).fetchone()["n"]
        return _fila_a_vacante(fila, total)


def listar_vacantes() -> List[Dict[str, Any]]:
    with conexion() as conn:
        filas = conn.execute(
            """
            SELECT v.*, (SELECT COUNT(*) FROM candidatos c WHERE c.vacante_id = v.id) AS total
            FROM vacantes v
            ORDER BY v.id DESC
            """
        ).fetchall()
        return [_fila_a_vacante(fila, fila["total"]) for fila in filas]


def eliminar_vacante(vacante_id: int) -> bool:
    with transaccion() as conn:
        cursor = conn.execute("DELETE FROM vacantes WHERE id = ?", (vacante_id,))
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Candidatos y evaluaciones
# ---------------------------------------------------------------------------
def crear_candidato(
    vacante_id: int,
    archivo_cv: str,
    ruta_archivo: str,
    estado: str,
    sesion: str,
    nombre_candidato: str = "No identificado",
    email: str = "",
    email_valido: bool = False,
    detalle: str = "",
) -> int:
    marca = ahora_iso()
    with transaccion() as conn:
        cursor = conn.execute(
            """
            INSERT INTO candidatos
                (vacante_id, nombre_candidato, email, email_valido, archivo_cv,
                 ruta_archivo, estado, detalle, sesion, creado_en, actualizado_en)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vacante_id,
                nombre_candidato,
                email,
                int(bool(email_valido)),
                archivo_cv,
                ruta_archivo,
                estado,
                detalle,
                sesion,
                marca,
                marca,
            ),
        )
        return int(cursor.lastrowid)


def guardar_evaluacion(
    candidato_id: int,
    vacante_id: int,
    anios_experiencia: float,
    habilidades_detectadas: List[str],
    puntaje: int,
    cumple_minimos: bool,
    justificacion_evidencia: str,
    modelo: str,
    latencia_segundos: float,
    throughput_tokens_seg: float,
) -> int:
    with transaccion() as conn:
        cursor = conn.execute(
            """
            INSERT INTO evaluaciones
                (candidato_id, vacante_id, anios_experiencia, habilidades_detectadas,
                 puntaje, cumple_minimos, justificacion_evidencia, modelo,
                 latencia_segundos, throughput_tokens_seg, creada_en)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidato_id) DO UPDATE SET
                anios_experiencia       = excluded.anios_experiencia,
                habilidades_detectadas  = excluded.habilidades_detectadas,
                puntaje                 = excluded.puntaje,
                cumple_minimos          = excluded.cumple_minimos,
                justificacion_evidencia = excluded.justificacion_evidencia,
                modelo                  = excluded.modelo,
                latencia_segundos       = excluded.latencia_segundos,
                throughput_tokens_seg   = excluded.throughput_tokens_seg,
                creada_en               = excluded.creada_en
            """,
            (
                candidato_id,
                vacante_id,
                float(anios_experiencia),
                json.dumps(habilidades_detectadas, ensure_ascii=False),
                int(puntaje),
                int(bool(cumple_minimos)),
                justificacion_evidencia,
                modelo,
                float(latencia_segundos),
                float(throughput_tokens_seg),
                ahora_iso(),
            ),
        )
        return int(cursor.lastrowid)


def actualizar_candidato(
    candidato_id: int,
    *,
    nombre_candidato: Optional[str] = None,
    email: Optional[str] = None,
    email_valido: Optional[bool] = None,
    estado: Optional[str] = None,
    detalle: Optional[str] = None,
) -> bool:
    campos: List[str] = []
    valores: List[Any] = []
    if nombre_candidato is not None:
        campos.append("nombre_candidato = ?")
        valores.append(nombre_candidato)
    if email is not None:
        campos.append("email = ?")
        valores.append(email)
    if email_valido is not None:
        campos.append("email_valido = ?")
        valores.append(int(bool(email_valido)))
    if estado is not None:
        campos.append("estado = ?")
        valores.append(estado)
    if detalle is not None:
        campos.append("detalle = ?")
        valores.append(detalle)
    if not campos:
        return False
    campos.append("actualizado_en = ?")
    valores.append(ahora_iso())
    valores.append(candidato_id)
    with transaccion() as conn:
        cursor = conn.execute(
            f"UPDATE candidatos SET {', '.join(campos)} WHERE id = ?", valores
        )
        return cursor.rowcount > 0


def _fila_a_candidato(fila: sqlite3.Row) -> Dict[str, Any]:
    claves = fila.keys()
    return {
        "id": fila["id"],
        "vacante_id": fila["vacante_id"],
        "nombre_candidato": fila["nombre_candidato"],
        "email": fila["email"],
        "email_valido": bool(fila["email_valido"]),
        "archivo_cv": fila["archivo_cv"],
        "estado": fila["estado"],
        "detalle": fila["detalle"],
        "creado_en": fila["creado_en"],
        "actualizado_en": fila["actualizado_en"],
        "puntaje": fila["puntaje"] if "puntaje" in claves else None,
        "cumple_minimos": (
            bool(fila["cumple_minimos"])
            if "cumple_minimos" in claves and fila["cumple_minimos"] is not None
            else None
        ),
        "anios_experiencia": fila["anios_experiencia"] if "anios_experiencia" in claves else None,
        "habilidades_detectadas": _json_cargar(
            fila["habilidades_detectadas"] if "habilidades_detectadas" in claves else None, []
        ),
        "justificacion_evidencia": (
            fila["justificacion_evidencia"] if "justificacion_evidencia" in claves else ""
        ) or "",
        "latencia_segundos": fila["latencia_segundos"] if "latencia_segundos" in claves else None,
        "throughput_tokens_seg": (
            fila["throughput_tokens_seg"] if "throughput_tokens_seg" in claves else None
        ),
    }


_SQL_RANKING = """
SELECT c.*, e.anios_experiencia, e.habilidades_detectadas, e.puntaje,
       e.cumple_minimos, e.justificacion_evidencia, e.latencia_segundos,
       e.throughput_tokens_seg
FROM candidatos c
LEFT JOIN evaluaciones e ON e.candidato_id = c.id
WHERE c.vacante_id = ?
ORDER BY (e.puntaje IS NULL) ASC,
         e.puntaje DESC,
         e.cumple_minimos DESC,
         e.anios_experiencia DESC,
         c.id ASC
"""


def obtener_ranking(vacante_id: int) -> List[Dict[str, Any]]:
    """Candidatos de una vacante ordenados de mayor a menor puntaje."""
    with conexion() as conn:
        filas = conn.execute(_SQL_RANKING, (vacante_id,)).fetchall()
        return [_fila_a_candidato(fila) for fila in filas]


def obtener_candidatos_por_ids(ids: List[int]) -> List[Dict[str, Any]]:
    if not ids:
        return []
    marcadores = ",".join("?" for _ in ids)
    consulta = f"""
        SELECT c.*, e.anios_experiencia, e.habilidades_detectadas, e.puntaje,
               e.cumple_minimos, e.justificacion_evidencia, e.latencia_segundos,
               e.throughput_tokens_seg
        FROM candidatos c
        LEFT JOIN evaluaciones e ON e.candidato_id = c.id
        WHERE c.id IN ({marcadores})
        ORDER BY (e.puntaje IS NULL) ASC, e.puntaje DESC, e.anios_experiencia DESC, c.id ASC
    """
    with conexion() as conn:
        filas = conn.execute(consulta, ids).fetchall()
        return [_fila_a_candidato(fila) for fila in filas]


def obtener_candidato(candidato_id: int) -> Optional[Dict[str, Any]]:
    with conexion() as conn:
        fila = conn.execute(
            """
            SELECT c.*, e.anios_experiencia, e.habilidades_detectadas, e.puntaje,
                   e.cumple_minimos, e.justificacion_evidencia, e.latencia_segundos,
                   e.throughput_tokens_seg
            FROM candidatos c
            LEFT JOIN evaluaciones e ON e.candidato_id = c.id
            WHERE c.id = ?
            """,
            (candidato_id,),
        ).fetchone()
        return _fila_a_candidato(fila) if fila else None


# ---------------------------------------------------------------------------
# Entrevistas
# ---------------------------------------------------------------------------
def registrar_entrevista(
    candidato_id: int,
    fecha_hora: str,
    duracion_minutos: int,
    modalidad: str,
    modo: str,
    evento_id: str,
    enlace_evento: str,
    email_destino: str,
    calendar_ok: bool,
    gmail_ok: bool,
    payload: Dict[str, Any],
) -> int:
    with transaccion() as conn:
        cursor = conn.execute(
            """
            INSERT INTO entrevistas
                (candidato_id, fecha_hora, duracion_minutos, modalidad, modo, evento_id,
                 enlace_evento, email_destino, calendar_ok, gmail_ok, payload, creada_en)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidato_id,
                fecha_hora,
                int(duracion_minutos),
                modalidad,
                modo,
                evento_id,
                enlace_evento,
                email_destino,
                int(bool(calendar_ok)),
                int(bool(gmail_ok)),
                json.dumps(payload, ensure_ascii=False, default=str),
                ahora_iso(),
            ),
        )
        return int(cursor.lastrowid)


def listar_entrevistas(vacante_id: Optional[int] = None) -> List[Dict[str, Any]]:
    consulta = """
        SELECT en.*, c.nombre_candidato, c.vacante_id
        FROM entrevistas en
        JOIN candidatos c ON c.id = en.candidato_id
    """
    parametros: List[Any] = []
    if vacante_id is not None:
        consulta += " WHERE c.vacante_id = ?"
        parametros.append(vacante_id)
    consulta += " ORDER BY en.id DESC"
    with conexion() as conn:
        filas = conn.execute(consulta, parametros).fetchall()
        resultado = []
        for fila in filas:
            registro = dict(fila)
            registro["payload"] = _json_cargar(registro.get("payload"), {})
            registro["calendar_ok"] = bool(registro.get("calendar_ok"))
            registro["gmail_ok"] = bool(registro.get("gmail_ok"))
            resultado.append(registro)
        return resultado


# ---------------------------------------------------------------------------
# Metricas
# ---------------------------------------------------------------------------
def registrar_metrica(
    usuario_o_sesion: str,
    archivo_cv: str,
    latencia_segundos: float,
    throughput_tokens_seg: float,
    cpu_porcentaje: float,
    ram_porcentaje: float,
    estado: str,
    detalle: str = "",
    vacante_id: Optional[int] = None,
    candidato_id: Optional[int] = None,
    tokens_generados: int = 0,
    modelo: str = "",
) -> int:
    """Inserta una fila de auditoria en logs_metricas."""
    with transaccion() as conn:
        cursor = conn.execute(
            """
            INSERT INTO logs_metricas
                (timestamp, usuario_o_sesion, archivo_cv, latencia_segundos,
                 throughput_tokens_seg, cpu_porcentaje, ram_porcentaje, estado,
                 detalle, vacante_id, candidato_id, tokens_generados, modelo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ahora_iso(),
                usuario_o_sesion,
                archivo_cv,
                round(float(latencia_segundos), 4),
                round(float(throughput_tokens_seg), 4),
                round(float(cpu_porcentaje), 2),
                round(float(ram_porcentaje), 2),
                estado,
                detalle[:2000],
                vacante_id,
                candidato_id,
                int(tokens_generados),
                modelo,
            ),
        )
        return int(cursor.lastrowid)


def listar_metricas(limite: int = 100, sesion: Optional[str] = None) -> List[Dict[str, Any]]:
    consulta = "SELECT * FROM logs_metricas"
    parametros: List[Any] = []
    if sesion:
        consulta += " WHERE usuario_o_sesion = ?"
        parametros.append(sesion)
    consulta += " ORDER BY id DESC LIMIT ?"
    parametros.append(int(limite))
    with conexion() as conn:
        filas = conn.execute(consulta, parametros).fetchall()
        return [dict(fila) for fila in filas]


def resumen_metricas() -> Dict[str, Any]:
    """Agregados para las tarjetas KPI del dashboard."""
    with conexion() as conn:
        fila = conn.execute(
            """
            SELECT
                COUNT(*)                                             AS total,
                SUM(CASE WHEN estado = 'EXITO' THEN 1 ELSE 0 END)    AS exitos,
                SUM(CASE WHEN estado <> 'EXITO' THEN 1 ELSE 0 END)   AS errores,
                AVG(CASE WHEN estado = 'EXITO' THEN latencia_segundos END)      AS lat_prom,
                MAX(CASE WHEN estado = 'EXITO' THEN latencia_segundos END)      AS lat_max,
                AVG(CASE WHEN throughput_tokens_seg > 0 THEN throughput_tokens_seg END) AS tps_prom,
                SUM(tokens_generados)                                AS tokens,
                COUNT(DISTINCT usuario_o_sesion)                     AS sesiones
            FROM logs_metricas
            """
        ).fetchone()
        ultimo = conn.execute(
            "SELECT modelo FROM logs_metricas WHERE modelo IS NOT NULL AND TRIM(modelo) <> '' ORDER BY id DESC LIMIT 1"
        ).fetchone()

    total = int(fila["total"] or 0)
    exitos = int(fila["exitos"] or 0)
    return {
        "total_cvs_procesados": total,
        "total_exitos": exitos,
        "total_errores": int(fila["errores"] or 0),
        "latencia_promedio_segundos": round(float(fila["lat_prom"] or 0.0), 2),
        "latencia_maxima_segundos": round(float(fila["lat_max"] or 0.0), 2),
        "throughput_promedio_tokens_seg": round(float(fila["tps_prom"] or 0.0), 2),
        "tokens_generados_total": int(fila["tokens"] or 0),
        "sesiones_activas": int(fila["sesiones"] or 0),
        "tasa_exito_porcentaje": round((exitos / total * 100.0), 1) if total else 0.0,
        "modelo": str(ultimo["modelo"]) if ultimo else "",
    }


def limpiar_metricas() -> int:
    """Vacia logs_metricas. Util antes de iniciar la demo en vivo."""
    with transaccion() as conn:
        cursor = conn.execute("DELETE FROM logs_metricas")
        return cursor.rowcount


__all__ = [
    "init_db",
    "conexion",
    "transaccion",
    "base_datos_disponible",
    "ahora_iso",
    "crear_vacante",
    "obtener_vacante",
    "listar_vacantes",
    "eliminar_vacante",
    "crear_candidato",
    "guardar_evaluacion",
    "actualizar_candidato",
    "obtener_ranking",
    "obtener_candidato",
    "obtener_candidatos_por_ids",
    "registrar_entrevista",
    "listar_entrevistas",
    "registrar_metrica",
    "listar_metricas",
    "resumen_metricas",
    "limpiar_metricas",
]
