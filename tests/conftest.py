"""Datos compartidos por los tests (≈ los fixtures/beforeEach de Jest)."""

from __future__ import annotations

import json
from pathlib import Path

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


# ---------------------------------------------------------------- solo en el repo full
# Los tests marcados con @pytest.mark.solo_full necesitan el corpus real de la mesa, que no se
# publica en el repo del curso (bot/scripts/publicar_lite.py). Sin él, se saltean con el motivo.
CORPUS_MESA = Path(__file__).resolve().parents[1] / "corpus" / "mesa"


def pytest_collection_modifyitems(config, items):
    if CORPUS_MESA.is_dir():
        return
    saltear = pytest.mark.skip(reason="solo en el repo full: necesita el corpus real de la mesa, que no se publica")
    for item in items:
        if "solo_full" in item.keywords:
            item.add_marker(saltear)


@pytest.fixture(autouse=True)
def auditoria_temporal(tmp_path, monkeypatch):
    """PE7: cada test escribe su auditoría en una carpeta temporal, nunca en datos/seguridad/."""
    monkeypatch.setenv("AUDITORIA_RUTA", str(tmp_path / "auditoria.jsonl"))
    monkeypatch.setenv("AUDITORIA_SAL", "sal-de-los-tests")
