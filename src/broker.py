"""Configuración de Alpaca: claves, candado paper/real, feed de datos y errores.

Decisión del 29/10 (CLAUDE.md): los datos de mercado salen de la cuenta paper
personal de Alpaca. El endpoint no se configura como URL libre sino con
ALPACA_PAPER (true por defecto). Si vale false, el bot se niega a arrancar salvo
que también esté ALPACA_LIVE_CONFIRMADO=si, que no se usa durante el curso.

En la PE2 no se mandan órdenes: de la API de trading solo se usa get_account()
para mostrar que la conexión anda. Las órdenes con aprobación son de la PE4.
"""

from __future__ import annotations

import os

import requests
from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed
from alpaca.trading.client import TradingClient

from .contratos import ErrorEstructurado


class ErrorConfiguracion(Exception):
    """Falta una clave o hay una combinación de variables prohibida. El bot no arranca."""


class ErrorAlpaca(Exception):
    """Error de la API de Alpaca, ya traducido al ErrorEstructurado del motor."""

    def __init__(self, error: ErrorEstructurado):
        super().__init__(error.mensaje)
        self.error = error


def modo_paper() -> bool:
    """El candado: devuelve True (paper) salvo que el modo real esté confirmado dos veces."""
    valor = os.getenv("ALPACA_PAPER", "true").strip().lower()
    if valor not in ("true", "false"):
        raise ErrorConfiguracion(f"ALPACA_PAPER tiene que ser true o false, no {valor!r}")
    if valor == "true":
        return True
    if os.getenv("ALPACA_LIVE_CONFIRMADO", "").strip().lower() != "si":
        raise ErrorConfiguracion(
            "ALPACA_PAPER=false pide operar con plata real y no está ALPACA_LIVE_CONFIRMADO=si. "
            "El bot no arranca. Durante el curso se usa solo la cuenta paper."
        )
    return False


def claves() -> tuple[str, str]:
    """Lee las claves del entorno. Nunca las imprime."""
    clave, secreto = os.getenv("ALPACA_API_KEY", "").strip(), os.getenv("ALPACA_SECRET_KEY", "").strip()
    faltan = [nombre for nombre, valor in (("ALPACA_API_KEY", clave), ("ALPACA_SECRET_KEY", secreto)) if not valor]
    if faltan:
        raise ErrorConfiguracion(
            f"Faltan {' y '.join(faltan)} en bot/.env. Se crean en https://app.alpaca.markets "
            "(cuenta Paper -> API Keys)."
        )
    return clave, secreto


def feed_datos() -> DataFeed:
    """Feed de velas: iex por defecto (tiempo real y gratis en el plan Basic)."""
    valor = os.getenv("ALPACA_FEED", "iex").strip().lower()
    try:
        return DataFeed(valor)
    except ValueError:
        validos = ", ".join(f.value for f in (DataFeed.IEX, DataFeed.SIP, DataFeed.DELAYED_SIP))
        raise ErrorConfiguracion(f"ALPACA_FEED={valor!r} no es válido; usá {validos}") from None


def error_estructurado(e: Exception) -> ErrorEstructurado:
    """Traduce un error de alpaca-py o de la red al ErrorEstructurado que ya usa el motor."""
    if isinstance(e, APIError):
        codigo = e.status_code
        try:
            detalle = e.message
        except (ValueError, KeyError, TypeError):  # el cuerpo no siempre es JSON
            detalle = str(e)
        causa = {401: "autenticacion", 403: "permiso", 429: "limite_de_uso"}.get(
            codigo, "servidor" if codigo and codigo >= 500 else "otro"
        )
        textos = {
            "autenticacion": "Claves de Alpaca inválidas (401)",
            "limite_de_uso": "Límite de uso de Alpaca (429), después de los reintentos del SDK",
        }
        mensaje = textos.get(causa, f"Error {codigo} de Alpaca: {detalle}")
    elif isinstance(e, (requests.ConnectionError, requests.Timeout)):
        causa, mensaje = "conexion", "Sin conexión con Alpaca"
    else:
        causa, mensaje = "otro", f"{type(e).__name__}: {e}"
    return ErrorEstructurado(tipo="error_api", mensaje=f"alpaca/{causa}: {mensaje}")


def enmascarar(numero: str) -> str:
    """El número de cuenta no va a la evidencia: PAEJEMPLO -> PA***."""
    return numero[:2] + "***" if numero else "***"


def resumen_cuenta(cliente: TradingClient | None = None) -> dict:
    """Verifica la conexión con la API de trading. Solo lectura y solo paper."""
    if not modo_paper():
        raise ErrorConfiguracion("Hasta la PE4 el bot solo se conecta a la cuenta paper")
    if cliente is None:
        clave, secreto = claves()
        cliente = TradingClient(clave, secreto, paper=True)
    try:
        cuenta = cliente.get_account()
    except (APIError, requests.RequestException) as e:
        raise ErrorAlpaca(error_estructurado(e)) from e
    return {
        "cuenta": enmascarar(cuenta.account_number),
        "estado": getattr(cuenta.status, "value", str(cuenta.status)),
        "paper": True,
        "moneda": cuenta.currency,
    }
