"""Velas de Alpaca e indicadores de la estrategia candidata.

Las velas salen de la API de datos de Alpaca (data.alpaca.markets) con la cuenta
paper personal y el feed de ALPACA_FEED (iex por defecto: tiempo real y gratis).
Qué permite el plan gratuito está medido en docs/datos_alpaca.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from .broker import ErrorAlpaca, claves, error_estructurado, feed_datos

NUEVA_YORK = ZoneInfo("America/New_York")  # en Windows la base de zonas viene del paquete tzdata
APERTURA, CIERRE = time(9, 30), time(16, 0)

TEMPORALIDADES = {
    "5m": TimeFrame(5, TimeFrameUnit.Minute),
    "15m": TimeFrame(15, TimeFrameUnit.Minute),
    "1h": TimeFrame(1, TimeFrameUnit.Hour),
    "1d": TimeFrame(1, TimeFrameUnit.Day),
}


@dataclass
class Vela:
    hora: datetime  # apertura de la vela, en UTC
    apertura: float
    maximo: float
    minimo: float
    cierre: float


def bajar_velas(
    activo: str = "SPY",
    intervalo: str = "5m",
    rango: str = "5d",
    *,
    feed: DataFeed | None = None,
    fin: datetime | None = None,
    cliente: StockHistoricalDataClient | None = None,
) -> list[Vela]:
    """Velas de los últimos `rango` días (por ejemplo, "5d") hasta `fin` (por defecto, ahora).

    Si Alpaca falla (claves inválidas, permiso, límite de uso, sin conexión) levanta
    ErrorAlpaca, que trae el ErrorEstructurado del motor con tipo error_api.
    `cliente` existe para los tests (≈ inyectar un mock en vez del SDK real).
    """
    if cliente is None:
        clave, secreto = claves()
        cliente = StockHistoricalDataClient(clave, secreto)
    fin = fin or datetime.now(timezone.utc)
    pedido = StockBarsRequest(
        symbol_or_symbols=activo,
        timeframe=TEMPORALIDADES[intervalo],
        start=fin - timedelta(days=int(rango.removesuffix("d"))),
        end=fin,
        feed=feed or feed_datos(),
    )
    try:
        barras = cliente.get_stock_bars(pedido).data.get(activo, [])
    except (APIError, requests.RequestException) as e:
        raise ErrorAlpaca(error_estructurado(e)) from e
    velas = [Vela(a_utc(b.timestamp), b.open, b.high, b.low, b.close) for b in barras]
    if intervalo != "1d":
        # Alpaca también manda pre-market y after-hours; la estrategia es de la rueda regular
        # (Yahoo, la fuente anterior, solo daba esas velas).
        velas = [v for v in velas if en_rueda(v.hora)]
    return sorted(velas, key=lambda v: v.hora)


def a_utc(hora: datetime) -> datetime:
    """La Entrada exige zona horaria: una hora sin zona se toma como UTC (así la manda Alpaca)."""
    return hora.replace(tzinfo=timezone.utc) if hora.tzinfo is None else hora.astimezone(timezone.utc)


def en_rueda(apertura_vela: datetime) -> bool:
    """¿La vela abre dentro de la rueda regular de Nueva York (9:30 a 16:00, lunes a viernes)?

    Se calcula en la hora de Nueva York para que el cambio de horario de verano no corra la ventana.
    No contempla feriados: esos días Alpaca no manda velas.
    """
    hora = apertura_vela.astimezone(NUEVA_YORK)
    return hora.weekday() < 5 and APERTURA <= hora.time() < CIERRE


def ema(valores: list[float], periodos: int) -> list[float]:
    """Media móvil exponencial. Arranca con el primer valor."""
    k = 2 / (periodos + 1)
    resultado = [valores[0]]
    for valor in valores[1:]:
        resultado.append(valor * k + resultado[-1] * (1 - k))
    return resultado


def rsi(cierres: list[float], periodos: int = 14) -> list[float | None]:
    """RSI de Wilder. Devuelve None hasta tener `periodos` variaciones."""
    resultado: list[float | None] = [None] * len(cierres)
    if len(cierres) <= periodos:
        return resultado
    variaciones = [b - a for a, b in zip(cierres, cierres[1:])]
    suba = sum(max(v, 0) for v in variaciones[:periodos]) / periodos
    baja = sum(max(-v, 0) for v in variaciones[:periodos]) / periodos
    for i in range(periodos, len(cierres)):
        if i > periodos:
            v = variaciones[i - 1]
            suba = (suba * (periodos - 1) + max(v, 0)) / periodos
            baja = (baja * (periodos - 1) + max(-v, 0)) / periodos
        resultado[i] = 100.0 if baja == 0 else 100 - 100 / (1 + suba / baja)
    return resultado


def atr(velas: list[Vela], periodos: int = 14) -> list[float | None]:
    """Average True Range de Wilder."""
    rangos = [velas[0].maximo - velas[0].minimo]
    for anterior, vela in zip(velas, velas[1:]):
        rangos.append(max(vela.maximo - vela.minimo, abs(vela.maximo - anterior.cierre), abs(vela.minimo - anterior.cierre)))
    resultado: list[float | None] = [None] * len(velas)
    if len(velas) < periodos:
        return resultado
    actual = sum(rangos[:periodos]) / periodos
    resultado[periodos - 1] = actual
    for i in range(periodos, len(velas)):
        actual = (actual * (periodos - 1) + rangos[i]) / periodos
        resultado[i] = actual
    return resultado
