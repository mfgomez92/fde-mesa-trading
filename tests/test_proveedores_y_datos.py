import pytest

from src.contratos import Salida
from src.datos_mercado import ema, rsi
from src.precios import costo_usd
from src.proveedores import esquema_para_llm


def test_esquema_para_llm_fija_la_forma_y_deja_las_reglas_a_pydantic():
    esquema = esquema_para_llm(Salida)
    texto = str(esquema)
    assert esquema["additionalProperties"] is False
    assert set(esquema["required"]) == set(Salida.model_fields)
    assert esquema["properties"]["accion"]["enum"] == ["comprar", "vender", "no_operar"]
    assert "$ref" not in texto and "minimum" not in texto and "pattern" not in texto
    # Los precios son Decimal en Python, pero al modelo le llegan como number (sin string).
    assert esquema["properties"]["precio_entrada"]["anyOf"] == [{"type": "number"}, {"type": "null"}]


def test_costo_usa_los_precios_publicados():
    # 1.000 tokens de entrada y 500 de salida con Opus 5.5 (4 y 20 USD por millón)
    assert costo_usd("claude-opus-5-5", 1_000, 500) == pytest.approx(0.014)


def test_costo_de_un_modelo_sin_precio_falla_claro():
    with pytest.raises(KeyError, match="src/precios.py"):
        costo_usd("modelo-inventado", 1, 1)


def test_ema_de_una_serie_constante_es_la_constante():
    assert ema([10.0] * 30, 9)[-1] == pytest.approx(10.0)


def test_rsi_de_una_serie_que_solo_sube_es_100():
    assert rsi([float(i) for i in range(30)])[-1] == 100.0
