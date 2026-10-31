import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.contratos import Accion, Entrada, Salida


def campos_con_error(error: ValidationError) -> set[str]:
    return {".".join(str(p) for p in e["loc"]) for e in error.errors()}


def test_entrada_valida(entrada_valida):
    entrada = Entrada.model_validate(entrada_valida)
    assert entrada.activo == "SPY"
    assert entrada.temporalidad.value == "5m"


@pytest.mark.parametrize(
    "cambio, campo",
    [
        ({"id_propuesta": "123"}, "id_propuesta"),  # patrón
        ({"activo": "spy500"}, "activo"),  # patrón
        ({"temporalidad": "1m"}, "temporalidad"),  # Enum
        ({"precio": -1}, "precio"),  # rango
        ({"consulta": "hola"}, "consulta"),  # largo mínimo
    ],
)
def test_entrada_invalida_indica_el_campo(entrada_valida, cambio, campo):
    with pytest.raises(ValidationError) as error:
        Entrada.model_validate({**entrada_valida, **cambio})
    assert campo in campos_con_error(error.value)


def test_rsi_fuera_de_rango_indica_la_ruta_anidada(entrada_valida):
    datos = {**entrada_valida, "indicadores": {**entrada_valida["indicadores"], "rsi": 140}}
    with pytest.raises(ValidationError) as error:
        Entrada.model_validate(datos)
    assert "indicadores.rsi" in campos_con_error(error.value)


def test_hora_sin_zona_horaria_es_invalida(entrada_valida):
    with pytest.raises(ValidationError, match="zona horaria"):
        Entrada.model_validate({**entrada_valida, "hora_vela": "2026-10-28T19:50:00"})


def test_campos_inventados_se_rechazan(salida_compra):
    with pytest.raises(ValidationError):
        Salida.model_validate({**salida_compra, "cantidad": 100})


def test_salida_de_compra_valida(salida_compra):
    assert Salida.model_validate(salida_compra).accion == Accion.COMPRAR


def test_salida_no_operar_valida(salida_no_operar):
    assert Salida.model_validate(salida_no_operar).precio_entrada is None


@pytest.mark.parametrize(
    "cambio, mensaje",
    [
        ({"stop_loss": 772.50}, "stop_loss < precio_entrada < take_profit"),
        ({"clasificacion": "sin_senal"}, "requiere clasificacion=senal"),
        ({"take_profit": None}, "requiere precio_entrada, stop_loss y take_profit"),
    ],
)
def test_compra_incoherente_es_invalida(salida_compra, cambio, mensaje):
    with pytest.raises(ValidationError, match=mensaje):
        Salida.model_validate({**salida_compra, **cambio})


def test_venta_coherente_es_valida(salida_compra):
    venta = {**salida_compra, "accion": "vender", "stop_loss": 773.0, "take_profit": 769.5}
    assert Salida.model_validate(venta).accion == Accion.VENDER


def test_no_operar_con_precios_es_invalido(salida_no_operar):
    with pytest.raises(ValidationError, match="tienen que ser null"):
        Salida.model_validate({**salida_no_operar, "precio_entrada": 771.0})


@pytest.mark.parametrize("cambio", [{"confianza": 1.4}, {"motivo": "porque sí"}, {"accion": "mantener"}])
def test_reglas_de_campo_de_la_salida(salida_compra, cambio):
    with pytest.raises(ValidationError):
        Salida.model_validate({**salida_compra, **cambio})


def test_los_precios_son_decimal_exactos(salida_compra):
    # Pydantic convierte el float vía str(): 771.89 queda exacto, sin el error binario.
    salida = Salida.model_validate(salida_compra)
    assert salida.precio_entrada == Decimal("771.89")


@pytest.mark.parametrize("propuesto, en_grilla", [(774.106, "774.11"), (0.1 + 0.2, "0.30")])
def test_los_precios_se_llevan_a_centavos(entrada_valida, propuesto, en_grilla):
    assert Entrada.model_validate({**entrada_valida, "precio": propuesto}).precio == Decimal(en_grilla)


def test_precio_que_redondea_a_cero_es_invalido(entrada_valida):
    with pytest.raises(ValidationError, match="mayor a 0"):
        Entrada.model_validate({**entrada_valida, "precio": 0.004})


def test_en_json_los_precios_siguen_siendo_numeros(salida_compra):
    # La bitácora y la evidencia guardan números, no strings como "771.89".
    assert json.loads(Salida.model_validate(salida_compra).model_dump_json())["precio_entrada"] == 771.89
