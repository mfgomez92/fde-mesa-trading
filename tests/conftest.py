"""Datos compartidos por los tests (≈ los fixtures/beforeEach de Jest)."""

from __future__ import annotations

import json

import pytest

from src.bitacora import Bitacora


@pytest.fixture
def entrada_valida() -> dict:
    return {
        "id_propuesta": "PRP-20261028-0001",
        "activo": "SPY",
        "temporalidad": "5m",
        "hora_vela": "2026-10-28T19:50:00+00:00",
        "precio": 771.89,
        "indicadores": {"ema_rapida": 771.23, "ema_lenta": 771.19, "rsi": 57.2, "atr": 1.084},
        "consulta": "Se cruzaron las EMAs en la última vela. ¿Hay entrada?",
    }


@pytest.fixture
def salida_compra() -> dict:
    return {
        "clasificacion": "senal",
        "accion": "comprar",
        "precio_entrada": 771.89,
        "stop_loss": 770.80,
        "take_profit": 774.10,
        "confianza": 0.72,
        "motivo": "EMA 9 (771.23) cruzó sobre EMA 21 (771.19) con RSI 57.2.",
    }


@pytest.fixture
def salida_no_operar() -> dict:
    return {
        "clasificacion": "fuera_de_alcance",
        "accion": "no_operar",
        "precio_entrada": None,
        "stop_loss": None,
        "take_profit": None,
        "confianza": 0.95,
        "motivo": "La consulta es sobre fútbol, no sobre operar SPY.",
    }


@pytest.fixture
def bitacora(tmp_path) -> Bitacora:
    return Bitacora(tmp_path / "bitacora.jsonl")


def como_json(datos: dict) -> str:
    return json.dumps(datos)
