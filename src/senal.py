"""La señal de la estrategia, calculada por el CÓDIGO (lección de la PE2).

En la PE2 el modelo decidía si había señal aplicando reglas que estaban en el
prompt (EMA 9 sobre EMA 21 y RSI entre 50 y 70 para comprar). Eso es mecánico:
el código lo hace siempre igual, en microsegundos y sin tokens. Además, la
confianza que declaraba el modelo cambiaba entre corridas (0,45 y 0,60 para la
misma consulta) y, con un umbral de 0,60, la decisión de operar quedaba al azar.

Ahora el reparto es:
- el código calcula la operación (acción, entrada, stop y take profit);
- el código de riesgo la evalúa (src/riesgo.py), igual que antes;
- el LLM solo la confirma o la veta con un motivo, mirando el contexto y el
  mandato de la mesa (que le llega del RAG). No puede cambiar precios.

estrategia-v0.2 (respuesta de la mesa del 02/11, config/estrategia-v0.2.json):
- el CRUCE de EMAs se detecta con las dos últimas velas (no alcanza con que la
  rápida esté arriba: tiene que haber cruzado en la última vela);
- filtro de VWAP del día: largos con el cierre sobre el VWAP, cortos debajo;
- filtro de régimen diario: largos solo si el último cierre diario quedó sobre su
  SMA 50; cortos solo si quedó debajo;
- entradas solo con la vela cerrada entre las 10:00 y las 15:30 ET.
La misma función la usan el bot en vivo y el backtest (scripts/backtest.py).

El camino de la PE2 (estrategia-v0.1: «EMA rápida por encima», sin filtros) sigue
disponible con ParametrosEstrategia.v01(), para reproducir aquella entrega.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from .contratos import Accion, Clasificacion, Entrada, Salida, a_la_grilla


NUEVA_YORK = ZoneInfo("America/New_York")


class ParametrosEstrategia(BaseModel):
    version: str = "estrategia-v0.2"
    rsi_compra_min: float = Field(default=50, ge=0, le=100)
    rsi_compra_max: float = Field(default=70, ge=0, le=100)
    rsi_venta_min: float = Field(default=30, ge=0, le=100)
    rsi_venta_max: float = Field(default=50, ge=0, le=100)
    stop_en_atr: Decimal = Field(default=Decimal("1.5"), gt=0)  # el stop, a cuántos ATR de la entrada
    relacion_objetivo: Decimal = Field(default=Decimal("2"), gt=0)  # take profit = relación × riesgo
    # v0.2: el cruce, los filtros y el horario. Apagados reproducen la v0.1 de la PE2.
    exigir_cruce: bool = True
    filtro_vwap: bool = True
    filtro_regimen: bool = True
    entradas_desde: time | None = time(10, 0)  # hora de Nueva York del cierre de la vela
    entradas_hasta: time | None = time(15, 30)

    @classmethod
    def v01(cls) -> "ParametrosEstrategia":
        """La estrategia de la PE2: EMA rápida arriba + RSI, sin cruce, filtros ni horario."""
        return cls(version="estrategia-v0.1", exigir_cruce=False, filtro_vwap=False, filtro_regimen=False,
                   entradas_desde=None, entradas_hasta=None)


class SenalCodigo(BaseModel):
    salida: Salida  # la misma forma que proponía el modelo en la PE2: el código de riesgo no cambia
    regla: str  # qué condición se cumplió (o por qué no hay señal), para la bitácora
    version_estrategia: str


def _sin_senal(regla: str, valores: str, p: ParametrosEstrategia) -> SenalCodigo:
    salida = Salida(clasificacion=Clasificacion.SIN_SENAL, accion=Accion.NO_OPERAR, confianza=1.0,
                    motivo=f"{regla} ({valores}).")
    return SenalCodigo(salida=salida, regla=regla, version_estrategia=p.version)


def calcular_senal(entrada: Entrada, p: ParametrosEstrategia | None = None) -> SenalCodigo:
    p = p or ParametrosEstrategia()
    ind = entrada.indicadores
    valores = f"EMA 9 {ind.ema_rapida:.2f}, EMA 21 {ind.ema_lenta:.2f}, RSI {ind.rsi:.1f}, ATR {ind.atr:.2f}"
    if ind.vwap is not None:
        valores += f", VWAP {ind.vwap:.2f}"

    # Horario de entradas: la vela tiene que cerrar dentro de la ventana (hora de Nueva York).
    if p.entradas_desde and p.entradas_hasta:
        hora = entrada.hora_vela.astimezone(NUEVA_YORK).time()
        if not (p.entradas_desde <= hora <= p.entradas_hasta):
            return _sin_senal(f"Sin señal: fuera del horario de entradas ({p.entradas_desde:%H:%M} a "
                              f"{p.entradas_hasta:%H:%M} ET)", valores, p)

    if p.exigir_cruce:
        if ind.ema_rapida_anterior is None or ind.ema_lenta_anterior is None:
            return _sin_senal("Sin señal: faltan las EMAs de la vela anterior para detectar el cruce", valores, p)
        sube = ind.ema_rapida_anterior <= ind.ema_lenta_anterior and ind.ema_rapida > ind.ema_lenta
        baja = ind.ema_rapida_anterior >= ind.ema_lenta_anterior and ind.ema_rapida < ind.ema_lenta
        nombre_largo, nombre_corto = "EMA 9 cruzó sobre EMA 21", "EMA 9 cruzó bajo EMA 21"
    else:
        sube, baja = ind.ema_rapida > ind.ema_lenta, ind.ema_rapida < ind.ema_lenta
        nombre_largo, nombre_corto = "EMA 9 > EMA 21", "EMA 9 < EMA 21"

    largo = sube and p.rsi_compra_min <= ind.rsi <= p.rsi_compra_max
    corto = baja and p.rsi_venta_min <= ind.rsi <= p.rsi_venta_max
    if not (largo or corto):
        return _sin_senal("Sin señal: las EMAs y el RSI no cumplen las condiciones de compra ni de venta",
                          valores, p)
    accion = Accion.COMPRAR if largo else Accion.VENDER
    if largo:
        regla = f"{nombre_largo} y RSI entre {p.rsi_compra_min:g} y {p.rsi_compra_max:g}"
    else:
        regla = f"{nombre_corto} y RSI entre {p.rsi_venta_min:g} y {p.rsi_venta_max:g}"

    precio_f = float(entrada.precio)
    if p.filtro_vwap:
        if ind.vwap is None:
            return _sin_senal(f"Sin señal: {regla}, pero falta el VWAP del día", valores, p)
        if (largo and not precio_f > ind.vwap) or (corto and not precio_f < ind.vwap):
            lado = "sobre" if largo else "bajo"
            return _sin_senal(f"Sin señal: {regla}, pero el precio no está {lado} el VWAP", valores, p)
        regla += f", precio {'sobre' if largo else 'bajo'} el VWAP"
    if p.filtro_regimen:
        if ind.cierre_diario_anterior is None or ind.sma_diaria is None:
            return _sin_senal(f"Sin señal: {regla}, pero falta el régimen diario (SMA 50)", valores, p)
        regimen_largo = ind.cierre_diario_anterior > ind.sma_diaria
        regimen_corto = ind.cierre_diario_anterior < ind.sma_diaria
        if (largo and not regimen_largo) or (corto and not regimen_corto):
            return _sin_senal(f"Sin señal: {regla}, pero el régimen diario no lo permite (cierre "
                              f"{ind.cierre_diario_anterior:.2f}, SMA 50 {ind.sma_diaria:.2f})", valores, p)
        regla += f", régimen {'largo' if largo else 'corto'}"

    # Precios en Decimal y en la grilla del bróker (centavos), como en la PE2.
    precio = entrada.precio
    riesgo = a_la_grilla(Decimal(str(ind.atr)) * p.stop_en_atr)
    signo = 1 if accion == Accion.COMPRAR else -1
    stop = a_la_grilla(precio - signo * riesgo)
    objetivo = a_la_grilla(precio + signo * riesgo * p.relacion_objetivo)
    salida = Salida(
        clasificacion=Clasificacion.SENAL, accion=accion, precio_entrada=precio, stop_loss=stop,
        take_profit=objetivo,
        # Señal mecánica: las condiciones se cumplen o no. No hay una confianza que estimar.
        confianza=1.0,
        motivo=f"{regla} ({valores}). Stop a {p.stop_en_atr} ATR y objetivo a {p.relacion_objetivo} veces el riesgo.",
    )
    return SenalCodigo(salida=salida, regla=regla, version_estrategia=p.version)
