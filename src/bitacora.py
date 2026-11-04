"""Bitácora: un formato único que registra cada decisión y calcula los KPIs solo.

Responde al punto 6 de la devolución de la PE1: cada fila tiene id de la
propuesta, etapa, decisión, categoría y código de motivo, hora, versiones y
autor.

Dos implementaciones con la misma interfaz (registrar / leer):
- Bitacora: archivo JSONL, la de la PE2 y los tests;
- BitacoraSQLite: la del bot operando (desde el 02/11), en un volumen. Además de
  los registros guarda las operaciones, los vetos con su contrafáctico, los
  latidos del loop (KPI de disponibilidad), el equity diario (T-Bill 3M) y el
  estado del botón de apagado. Equivalencia TS: como better-sqlite3.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Etapa(str, Enum):
    PROPUESTA = "propuesta"  # arrancó un ciclo con una entrada válida
    DECISION_RIESGO = "decision_riesgo"  # lo que decidió el código de riesgo
    DECISION_FINAL = "decision_final"  # qué pasó al final con la propuesta
    ERROR = "error"  # error antes de tener una propuesta (por ejemplo, entrada inválida)


class CategoriaMotivo(str, Enum):
    RIESGO = "riesgo"  # la frenó una regla de riesgo: es el sistema funcionando
    OPERATIVO = "operativo"  # la frenó una falla (API, validación, VM): es lo que hay que llevar a 0
    NINGUNO = "ninguno"


class RegistroBitacora(BaseModel):
    id_propuesta: str
    etapa: Etapa
    hora: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decision: str
    categoria_motivo: CategoriaMotivo = CategoriaMotivo.NINGUNO
    codigo_motivo: str = ""
    detalle: str = ""
    autor: str  # motor | codigo-riesgo | operador
    version_reglas: str
    version_prompt: str
    # Solo en la etapa "propuesta": medición del ciclo completo, reintentos incluidos.
    proveedor: str | None = None
    modelo: str | None = None
    intentos: int | None = None
    tokens_entrada: int | None = None
    tokens_salida: int | None = None
    costo_usd: float | None = None
    latencia_ms: int | None = None


class Bitacora:
    def __init__(self, ruta: Path | str):
        self.ruta = Path(ruta)

    def registrar(self, registro: RegistroBitacora) -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with self.ruta.open("a", encoding="utf-8") as archivo:
            archivo.write(registro.model_dump_json() + "\n")

    def leer(self) -> list[RegistroBitacora]:
        if not self.ruta.exists():
            return []
        with self.ruta.open(encoding="utf-8") as archivo:
            return [RegistroBitacora.model_validate_json(linea) for linea in archivo if linea.strip()]


ESQUEMA_SQL = """
CREATE TABLE IF NOT EXISTS registros (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hora TEXT NOT NULL, id_propuesta TEXT NOT NULL, etapa TEXT NOT NULL, decision TEXT NOT NULL,
    categoria_motivo TEXT NOT NULL, codigo_motivo TEXT NOT NULL, autor TEXT NOT NULL,
    costo_usd REAL, json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS registros_hora ON registros (hora);
CREATE TABLE IF NOT EXISTS operaciones (
    id_propuesta TEXT PRIMARY KEY,
    etapa TEXT NOT NULL,                 -- E2 (paper al 25 %) o E3 (paper pleno); E1 (shadow) se calcula en R
    multiplicador REAL NOT NULL,
    lado TEXT NOT NULL, cantidad INTEGER NOT NULL, equity_al_entrar REAL NOT NULL,
    hora_senal TEXT NOT NULL, precio_senal REAL NOT NULL, stop REAL NOT NULL, objetivo REAL NOT NULL,
    riesgo_accion REAL NOT NULL, orden_id TEXT, orden_cierre_id TEXT,
    estado TEXT NOT NULL,                -- enviada | abierta | cerrada | rechazada
    hora_entrada TEXT, llenado REAL, hora_salida TEXT, salida REAL, tipo_salida TEXT,
    r_bruto REAL, costo_r_modelado REAL, r_neto REAL,
    slippage_entrada_bps REAL, slippage_salida_bps REAL, pnl_usd REAL
);
CREATE TABLE IF NOT EXISTS vetos (
    id_propuesta TEXT PRIMARY KEY,
    hora_senal TEXT NOT NULL, lado TEXT NOT NULL, precio_senal REAL NOT NULL, stop REAL NOT NULL,
    objetivo REAL NOT NULL, veredicto TEXT NOT NULL,  -- vetar | sin_respuesta
    motivo TEXT, r_contrafactico REAL, tipo_salida_contrafactico TEXT, calculado_en TEXT
);
CREATE TABLE IF NOT EXISTS ciclos (
    hora TEXT PRIMARY KEY, mercado_abierto INTEGER NOT NULL, ok INTEGER NOT NULL, motivo TEXT
);
CREATE TABLE IF NOT EXISTS equity_diaria (
    fecha TEXT PRIMARY KEY, equity REAL NOT NULL, tbill_3m_pct REAL
);
CREATE TABLE IF NOT EXISTS disponibilidad (
    fecha TEXT PRIMARY KEY, minutos_rueda INTEGER NOT NULL, minutos_ok INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS frescura (
    vela_esperada TEXT PRIMARY KEY,  -- cierre de la vela que debería estar disponible
    ultima_recibida TEXT NOT NULL,   -- cierre de la última vela que devolvió Alpaca
    consultado TEXT NOT NULL,        -- cuándo se pidió
    al_dia INTEGER NOT NULL          -- 1 si la vela esperada ya estaba
);
CREATE TABLE IF NOT EXISTS estado (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
"""


class BitacoraSQLite:
    """Bitácora persistente. Una conexión por operación: la leen el bot y el vigía a la vez."""

    def __init__(self, ruta: Path | str):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with self._conectar() as c:
            c.executescript(ESQUEMA_SQL)

    def _conectar(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.ruta, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")  # el vigía lee mientras el bot escribe
        return c

    def _ejecutar(self, sql: str, parametros: tuple | dict = ()) -> None:
        with self._conectar() as c:
            c.execute(sql, parametros)

    def consultar(self, sql: str, parametros: tuple | dict = ()) -> list[dict[str, Any]]:
        with self._conectar() as c:
            return [dict(f) for f in c.execute(sql, parametros).fetchall()]

    # --- la misma interfaz que Bitacora
    def registrar(self, registro: RegistroBitacora) -> None:
        self._ejecutar(
            "INSERT INTO registros (hora, id_propuesta, etapa, decision, categoria_motivo, codigo_motivo, autor, "
            "costo_usd, json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (registro.hora.isoformat(), registro.id_propuesta, registro.etapa.value, registro.decision,
             registro.categoria_motivo.value, registro.codigo_motivo, registro.autor, registro.costo_usd,
             registro.model_dump_json()))

    def leer(self) -> list[RegistroBitacora]:
        return [RegistroBitacora.model_validate_json(f["json"])
                for f in self.consultar("SELECT json FROM registros ORDER BY id")]

    # --- operaciones, vetos, latidos y estado
    def guardar_operacion(self, **campos) -> None:
        columnas = ", ".join(campos)
        marcas = ", ".join(f":{k}" for k in campos)
        actualizar = ", ".join(f"{k} = excluded.{k}" for k in campos if k != "id_propuesta")
        self._ejecutar(f"INSERT INTO operaciones ({columnas}) VALUES ({marcas}) "
                       f"ON CONFLICT (id_propuesta) DO UPDATE SET {actualizar}", campos)

    def actualizar_operacion(self, id_propuesta: str, **campos) -> None:
        asignaciones = ", ".join(f"{k} = :{k}" for k in campos)
        self._ejecutar(f"UPDATE operaciones SET {asignaciones} WHERE id_propuesta = :id_propuesta",
                       {**campos, "id_propuesta": id_propuesta})

    def operaciones(self, estado: str | None = None) -> list[dict]:
        if estado:
            return self.consultar("SELECT * FROM operaciones WHERE estado = ? ORDER BY hora_senal", (estado,))
        return self.consultar("SELECT * FROM operaciones ORDER BY hora_senal")

    def guardar_veto(self, **campos) -> None:
        columnas = ", ".join(campos)
        marcas = ", ".join(f":{k}" for k in campos)
        actualizar = ", ".join(f"{k} = excluded.{k}" for k in campos if k != "id_propuesta")
        self._ejecutar(f"INSERT INTO vetos ({columnas}) VALUES ({marcas}) "
                       f"ON CONFLICT (id_propuesta) DO UPDATE SET {actualizar}", campos)

    def actualizar_veto(self, id_propuesta: str, **campos) -> None:
        asignaciones = ", ".join(f"{k} = :{k}" for k in campos)
        self._ejecutar(f"UPDATE vetos SET {asignaciones} WHERE id_propuesta = :id_propuesta",
                       {**campos, "id_propuesta": id_propuesta})

    def latido(self, hora: datetime, mercado_abierto: bool, ok: bool, motivo: str = "") -> None:
        self._ejecutar("INSERT OR REPLACE INTO ciclos (hora, mercado_abierto, ok, motivo) VALUES (?, ?, ?, ?)",
                       (hora.astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat(),
                        int(mercado_abierto), int(ok), motivo))

    def ultimo_latido(self) -> datetime | None:
        filas = self.consultar("SELECT MAX(hora) AS hora FROM ciclos")
        return datetime.fromisoformat(filas[0]["hora"]) if filas and filas[0]["hora"] else None

    def guardar_equity(self, fecha: str, equity: float, tbill_3m_pct: float | None) -> None:
        self._ejecutar("INSERT OR REPLACE INTO equity_diaria (fecha, equity, tbill_3m_pct) VALUES (?, ?, ?)",
                       (fecha, equity, tbill_3m_pct))

    def guardar_disponibilidad(self, fecha: str, minutos_rueda: int, minutos_ok: int) -> None:
        self._ejecutar("INSERT OR REPLACE INTO disponibilidad (fecha, minutos_rueda, minutos_ok) VALUES (?, ?, ?)",
                       (fecha, minutos_rueda, minutos_ok))

    def guardar_frescura(self, esperada: datetime, recibida: datetime, consultado: datetime) -> None:
        self._ejecutar("INSERT OR REPLACE INTO frescura (vela_esperada, ultima_recibida, consultado, al_dia) "
                       "VALUES (?, ?, ?, ?)", (esperada.isoformat(), recibida.isoformat(), consultado.isoformat(),
                                               int(recibida >= esperada)))

    def poner_estado(self, clave: str, valor: Any) -> None:
        self._ejecutar("INSERT OR REPLACE INTO estado (clave, valor) VALUES (?, ?)", (clave, json.dumps(valor)))

    def estado(self, clave: str, defecto: Any = None) -> Any:
        filas = self.consultar("SELECT valor FROM estado WHERE clave = ?", (clave,))
        return json.loads(filas[0]["valor"]) if filas else defecto


class ResultadoKpi(BaseModel):
    valor_pct: float | None  # None si no hay muestra suficiente
    numerador: int
    denominador: int
    muestra_minima: int
    concluyente: bool


def kpi_tecnico(
    registros: list[RegistroBitacora],
    desde: datetime,
    hasta: datetime,
    muestra_minima: int = 20,
) -> ResultadoKpi:
    """% de propuestas con la decisión de riesgo y la final registradas con hora.

    Ventana y muestra mínima: ver docs/kpis.md.
    """
    en_ventana = [r for r in registros if desde <= r.hora < hasta]
    propuestas = {r.id_propuesta for r in en_ventana if r.etapa == Etapa.PROPUESTA}
    con_riesgo = {r.id_propuesta for r in en_ventana if r.etapa == Etapa.DECISION_RIESGO}
    con_final = {r.id_propuesta for r in en_ventana if r.etapa == Etapa.DECISION_FINAL}
    completas = propuestas & con_riesgo & con_final

    total = len(propuestas)
    return ResultadoKpi(
        valor_pct=round(100 * len(completas) / total, 1) if total else None,
        numerador=len(completas),
        denominador=total,
        muestra_minima=muestra_minima,
        concluyente=total >= muestra_minima,
    )


def kpi_negocio(
    registros: list[RegistroBitacora],
    desde: datetime,
    hasta: datetime,
    muestra_minima: int = 10,
) -> ResultadoKpi:
    """% de ciclos frenados por una causa operativa, no de riesgo (meta: 0 %).

    Aproximación del KPI de negocio de la PE1 mientras el bot no opera: cuenta
    ciclos con decisión final. Cuando opere en paper, el denominador pasan a ser
    las señales decididas.
    """
    finales = [
        r for r in registros if desde <= r.hora < hasta and r.etapa == Etapa.DECISION_FINAL
    ]
    operativos = [r for r in finales if r.categoria_motivo == CategoriaMotivo.OPERATIVO]
    total = len(finales)
    return ResultadoKpi(
        valor_pct=round(100 * len(operativos) / total, 1) if total else None,
        numerador=len(operativos),
        denominador=total,
        muestra_minima=muestra_minima,
        concluyente=total >= muestra_minima,
    )
