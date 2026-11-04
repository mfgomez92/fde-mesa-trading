"""Reglas de riesgo EN CÓDIGO, fuera del modelo (decisión de diseño de la PE1).

El modelo propone; esta función decide.

reglas-v0.3 (validadas por la mesa el 02/11, ver caso/relevamiento/mesa-2026-11-02/RESPUESTA.md):
- un stop más lejos que 1 % se DESCARTA (antes pedía aprobación: en intradía nadie aprueba a tiempo);
- se elimina la confianza mínima (la señal la calcula el código);
- se suman, con el estado del día (EstadoDia): botón de apagado, una posición a la vez,
  blackouts macro, dato atrasado (más de 2 velas), máximo 4 operaciones por día y
  pérdida diaria máxima de 0,30 % del equity. El cierre forzado de las 15:55 ET es del
  loop (src/operador.py), porque es una salida y no una entrada.

Los precios y los límites van en Decimal, y las reglas se comparan multiplicando
en vez de dividir (`beneficio >= 1.5 * riesgo`): así una relación de 1:1,5 exacta
pasa, cosa que con float no pasaba (1.62 / 1.08 daba 1.4999999999999474).
La división solo se usa para el texto del detalle.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from .contratos import Accion, Entrada, Salida


class DecisionRiesgo(str, Enum):
    APROBADA_AUTO = "aprobada_auto"  # dentro de los límites: sigue sola (HU2)
    # Desde reglas-v0.3 ninguna regla devuelve esto (un stop lejano se descarta). Queda para
    # los cambios de reglas, que siguen pidiendo aprobación (M5).
    REQUIERE_APROBACION = "requiere_aprobacion"
    RECHAZADA = "rechazada"  # no cumple una regla dura
    SIN_OPERACION = "sin_operacion"  # el modelo no propuso operar


NUEVA_YORK = ZoneInfo("America/New_York")
RUTA_BLACKOUTS = Path(__file__).resolve().parent.parent / "config" / "macro_blackout.json"


class Blackouts(BaseModel):
    """Ventanas sin entradas por datos macro (config/macro_blackout.json)."""

    version: str
    ventanas: dict[str, tuple[time, time]]  # evento -> (desde, hasta), hora de Nueva York
    fechas: dict[str, list[date]]

    @classmethod
    def cargar(cls, ruta: Path | str = RUTA_BLACKOUTS) -> "Blackouts":
        datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
        ventanas = {k: (time.fromisoformat(v["desde"]), time.fromisoformat(v["hasta"]))
                    for k, v in datos["ventanas_et"].items()}
        return cls(version=datos["version"], ventanas=ventanas, fechas=datos["fechas"])

    def evento(self, momento: datetime) -> str | None:
        """El evento cuyo blackout cubre `momento`, o None."""
        local = momento.astimezone(NUEVA_YORK)
        for evento, fechas in self.fechas.items():
            desde, hasta = self.ventanas[evento]
            if local.date() in fechas and desde <= local.time() < hasta:
                return evento
        return None


class EstadoDia(BaseModel):
    """Lo que el código de riesgo necesita saber del día para decidir una entrada."""

    ahora: datetime  # con zona horaria
    hora_ultimo_dato: datetime  # cierre de la última vela recibida
    minutos_vela: int = Field(gt=0)
    operaciones_hoy: int = Field(default=0, ge=0)
    # Resultado realizado del día en R. Se pasa a % del equity con el riesgo de tamaño PLENO,
    # así el corte es el mismo en shadow (E1) y en paper al 25 % (E2).
    resultado_dia_r: float = 0.0
    posicion_abierta: bool = False
    apagado: bool = False  # botón de apagado activo


class ReglasRiesgo(BaseModel):
    version: str = "reglas-v0.3"
    relacion_minima_riesgo_beneficio: Decimal = Field(default=Decimal("1.5"), gt=0)
    distancia_maxima_entrada_pct: Decimal = Field(default=Decimal("0.3"), gt=0)  # entrada vs. último precio
    stop_maximo_pct: Decimal = Field(default=Decimal("1.0"), gt=0)  # más lejos que esto, se descarta
    riesgo_por_operacion_pct: Decimal = Field(default=Decimal("0.10"), gt=0)  # tamaño pleno (E1 y E3)
    perdida_diaria_maxima_pct: Decimal = Field(default=Decimal("0.30"), gt=0)
    operaciones_maximas_dia: int = Field(default=4, gt=0)
    dato_atrasado_max_velas: int = Field(default=2, gt=0)


class ResultadoRiesgo(BaseModel):
    decision: DecisionRiesgo
    codigo_motivo: str
    detalle: str
    version_reglas: str


def evaluar(entrada: Entrada, salida: Salida, reglas: ReglasRiesgo, estado: EstadoDia | None = None,
            blackouts: Blackouts | None = None) -> ResultadoRiesgo:
    """Decide una ENTRADA. Sin `estado` evalúa solo la orden (como en la PE2)."""
    def resultado(decision: DecisionRiesgo, codigo: str, detalle: str) -> ResultadoRiesgo:
        return ResultadoRiesgo(
            decision=decision, codigo_motivo=codigo, detalle=detalle, version_reglas=reglas.version
        )

    if salida.accion == Accion.NO_OPERAR:
        return resultado(DecisionRiesgo.SIN_OPERACION, "SIN_SENAL", f"Clasificación: {salida.clasificacion.value}")

    if estado is not None:
        rechazo = _reglas_del_dia(reglas, estado, blackouts)
        if rechazo:
            return resultado(DecisionRiesgo.RECHAZADA, *rechazo)

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
            DecisionRiesgo.RECHAZADA,
            "STOP_LEJANO",
            f"Stop a {stop_pct:.2f} %; el máximo es {reglas.stop_maximo_pct} % (más lejos, se descarta)",
        )

    return resultado(
        DecisionRiesgo.APROBADA_AUTO,
        "DENTRO_DE_LIMITES",
        f"Riesgo/beneficio 1:{relacion:.2f}, stop a {stop_pct:.2f} %",
    )


def _reglas_del_dia(reglas: ReglasRiesgo, estado: EstadoDia, blackouts: Blackouts | None) -> tuple[str, str] | None:
    """Reglas que dependen del estado del día. Devuelve (código, detalle) o None."""
    if estado.apagado:
        return "APAGADO", "Botón de apagado activo: no hay entradas nuevas"
    if estado.posicion_abierta:
        return "POSICION_ABIERTA", "Ya hay una posición abierta: una a la vez"
    if blackouts is not None:
        evento = blackouts.evento(estado.ahora)
        if evento:
            return "BLACKOUT", f"Blackout macro de {evento} ({blackouts.version})"
    atraso = estado.ahora - estado.hora_ultimo_dato
    if atraso > timedelta(minutes=estado.minutos_vela * reglas.dato_atrasado_max_velas):
        return ("DATO_ATRASADO", f"El último dato tiene {atraso.total_seconds() / 60:.0f} min; "
                f"el máximo es {reglas.dato_atrasado_max_velas} velas de {estado.minutos_vela} min")
    if estado.operaciones_hoy >= reglas.operaciones_maximas_dia:
        return "MAXIMO_OPERACIONES", f"Ya hubo {estado.operaciones_hoy} operaciones hoy; máximo {reglas.operaciones_maximas_dia}"
    perdida_pct = Decimal(str(-estado.resultado_dia_r)) * reglas.riesgo_por_operacion_pct
    if perdida_pct >= reglas.perdida_diaria_maxima_pct:
        return ("PERDIDA_DIARIA", f"Pérdida del día {perdida_pct:.2f} % del equity (a tamaño pleno); "
                f"máximo {reglas.perdida_diaria_maxima_pct} %: se corta el día")
    return None
