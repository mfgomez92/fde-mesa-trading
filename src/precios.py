"""Precios publicados por proveedor, en USD por millón de tokens.

Fuentes, consultadas el 29/10/2026:
- Anthropic: https://platform.claude.com/docs/en/about-claude/pricing
- OpenAI: https://developers.openai.com/api/docs/pricing

Los precios cambian: revisalos antes de usar los costos en un informe.
"""

from __future__ import annotations

# modelo -> (entrada, salida), en USD por millón de tokens
PRECIOS_USD_POR_MILLON: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "gpt-6.1-terra": (1.00, 5.00),
    "gpt-6-luna": (0.10, 0.50),
}


def costo_usd(modelo: str, tokens_entrada: int, tokens_salida: int) -> float:
    """Costo de una llamada. Los tokens de razonamiento se cobran como salida."""
    if modelo not in PRECIOS_USD_POR_MILLON:
        raise KeyError(f"No hay precio cargado para {modelo}: agregalo en src/precios.py")
    precio_entrada, precio_salida = PRECIOS_USD_POR_MILLON[modelo]
    return (tokens_entrada * precio_entrada + tokens_salida * precio_salida) / 1_000_000
