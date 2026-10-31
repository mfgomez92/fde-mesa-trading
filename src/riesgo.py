"""Reglas de riesgo EN CÓDIGO, fuera del modelo (decisión de diseño de la PE1).

El modelo propone; esta función decide. Los límites son valores iniciales
propuestos por el FDE y quedan a validar con el responsable de la mesa.

Los precios y los límites van en Decimal, y las reglas se comparan multiplicando
en vez de dividir (`beneficio >= 1.5 * riesgo`): así una relación de 1:1,5 exacta
pasa, cosa que con float no pasaba (1.62 / 1.08 daba 1.4999999999999474).
La división solo se usa para el texto del detalle.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field

from .contratos import Accion, Entrada, Salida


class DecisionRiesgo(str, Enum):
    APROBADA_AUTO = "aprobada_auto"  # dentro de los límites: sigue sola (HU2)
    REQUIERE_APROBACION = "requiere_aprobacion"  # fuera de límites: espera a una persona
    RECHAZADA = "rechazada"  # no cumple una regla dura
    SIN_OPERACION = "sin_operacion"  # el modelo no propuso operar


class ReglasRiesgo(BaseModel):
    # v0.2: mismos límites que v0.1, pero en Decimal (antes rechazaba una relación 1:1,5 exacta).
    version: str = "reglas-v0.2"
    # La confianza no es un precio: queda en float (0.6 del JSON y 0.6 del código son el mismo float).
    confianza_minima: float = Field(default=0.6, ge=0, le=1)
    relacion_minima_riesgo_beneficio: Decimal = Field(default=Decimal("1.5"), gt=0)
    distancia_maxima_entrada_pct: Decimal = Field(default=Decimal("0.3"), gt=0)  # entrada vs. último precio
    stop_maximo_pct: Decimal = Field(default=Decimal("1.0"), gt=0)  # más lejos que esto, aprueba una persona


class ResultadoRiesgo(BaseModel):
    decision: DecisionRiesgo
    codigo_motivo: str
    detalle: str
    version_reglas: str


def evaluar(entrada: Entrada, salida: Salida, reglas: ReglasRiesgo) -> ResultadoRiesgo:
    def resultado(decision: DecisionRiesgo, codigo: str, detalle: str) -> ResultadoRiesgo:
        return ResultadoRiesgo(
            decision=decision, codigo_motivo=codigo, detalle=detalle, version_reglas=reglas.version
        )

    if salida.accion == Accion.NO_OPERAR:
        return resultado(DecisionRiesgo.SIN_OPERACION, "SIN_SENAL", f"Clasificación: {salida.clasificacion.value}")

    if salida.confianza < reglas.confianza_minima:
        return resultado(
            DecisionRiesgo.RECHAZADA,
            "CONFIANZA_BAJA",
            f"Confianza {salida.confianza:.2f} < mínimo {reglas.confianza_minima:.2f}",
        )

    distancia_entrada = abs(salida.precio_entrada - entrada.precio)
    if distancia_entrada * 100 > reglas.distancia_maxima_entrada_pct * entrada.precio:
        return resultado(
            DecisionRiesgo.RECHAZADA,
            "ENTRADA_LEJANA",
            f"Entrada a {distancia_entrada / entrada.precio * 100:.2f} % del precio; "
            f"máximo {reglas.distancia_maxima_entrada_pct} %",
        )

    riesgo = abs(salida.precio_entrada - salida.stop_loss)  # nunca es 0: Salida exige stop != entrada
    beneficio = abs(salida.take_profit - salida.precio_entrada)
    relacion = beneficio / riesgo  # solo para el detalle
    if beneficio < reglas.relacion_minima_riesgo_beneficio * riesgo:
        return resultado(
            DecisionRiesgo.RECHAZADA,
            "RR_INSUFICIENTE",
            f"Riesgo/beneficio 1:{relacion:.2f} < 1:{reglas.relacion_minima_riesgo_beneficio}",
        )

    stop_pct = riesgo / salida.precio_entrada * 100  # solo para el detalle
    if riesgo * 100 > reglas.stop_maximo_pct * salida.precio_entrada:
        return resultado(
            DecisionRiesgo.REQUIERE_APROBACION,
            "FUERA_DE_LIMITES",
            f"Stop a {stop_pct:.2f} %; el máximo automático es {reglas.stop_maximo_pct} %",
        )

    return resultado(
        DecisionRiesgo.APROBADA_AUTO,
        "DENTRO_DE_LIMITES",
        f"Riesgo/beneficio 1:{relacion:.2f}, stop a {stop_pct:.2f} %",
    )
