"""Bitácora: un formato único que registra cada decisión y calcula los KPIs solo.

Responde al punto 6 de la devolución de la PE1: cada fila tiene id de la
propuesta, etapa, decisión, categoría y código de motivo, hora, versiones y
autor. Por ahora es un archivo JSONL (una fila JSON por línea); en el M8 pasa a
una base de datos con el mismo esquema.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

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
