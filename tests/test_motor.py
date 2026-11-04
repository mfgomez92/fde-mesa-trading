"""Tests del motor con el LLM simulado: corren sin claves y sin red.

`async def test_...` funciona gracias a pytest-asyncio (≈ un test async de Vitest).
"""

from datetime import datetime, timedelta, timezone

from src.bitacora import Etapa, kpi_tecnico
from src.motor import Motor
from src.proveedores import ErrorProveedor, ProveedorSimulado

from .conftest import como_json


def ahora_mas(minutos: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutos)


async def test_salida_valida_se_aprueba_y_queda_en_la_bitacora(entrada_valida, salida_compra, bitacora):
    motor = Motor([ProveedorSimulado([como_json(salida_compra)])], bitacora)

    resultado = await motor.procesar(entrada_valida)

    assert resultado.ok and resultado.intentos == 1
    assert resultado.riesgo.decision.value == "aprobada_auto"
    assert (resultado.tokens_entrada, resultado.tokens_salida) == (900, 150)
    etapas = [r.etapa for r in bitacora.leer()]
    assert etapas == [Etapa.PROPUESTA, Etapa.DECISION_RIESGO, Etapa.DECISION_FINAL]


async def test_salida_invalida_se_reintenta_con_el_campo_que_fallo(entrada_valida, salida_compra, bitacora):
    rota = {**salida_compra, "confianza": 1.4}
    llm = ProveedorSimulado([como_json(rota), como_json(salida_compra)])
    motor = Motor([llm], bitacora)

    resultado = await motor.procesar(entrada_valida)

    assert resultado.ok and resultado.intentos == 2
    assert resultado.tokens_entrada == 1800  # se suman los tokens de los dos intentos
    correccion = llm.llamadas[1][-1]["content"]
    assert "confianza" in correccion  # el reintento le dice al modelo qué campo falló


async def test_dos_salidas_invalidas_devuelven_error_estructurado(entrada_valida, salida_compra, bitacora):
    rota = como_json({**salida_compra, "stop_loss": 780.0})
    motor = Motor([ProveedorSimulado([rota, rota])], bitacora)

    resultado = await motor.procesar(entrada_valida)

    assert not resultado.ok and resultado.intentos == 2
    assert resultado.error.tipo == "salida_invalida"
    assert "stop_loss < precio_entrada" in resultado.error.campos[0].mensaje
    final = [r for r in bitacora.leer() if r.etapa == Etapa.DECISION_FINAL][0]
    assert (final.decision, final.categoria_motivo.value) == ("descartada", "operativo")


async def test_json_mal_formado_es_salida_invalida(entrada_valida, bitacora):
    motor = Motor([ProveedorSimulado(["no es json", "{"])], bitacora)
    resultado = await motor.procesar(entrada_valida)
    assert resultado.error.tipo == "salida_invalida"
    assert resultado.error.campos[0].tipo == "json_invalid"


async def test_entrada_invalida_no_llama_al_modelo(entrada_valida, bitacora):
    llm = ProveedorSimulado([])
    resultado = await Motor([llm], bitacora).procesar({**entrada_valida, "precio": 0})

    assert resultado.error.tipo == "entrada_invalida"
    assert resultado.error.campos[0].campo == "precio"
    assert llm.llamadas == []


async def test_si_falla_la_api_del_principal_usa_el_fallback(entrada_valida, salida_compra, bitacora):
    principal = ProveedorSimulado([ErrorProveedor("limite_de_uso", "429", "anthropic")], nombre="anthropic")
    respaldo = ProveedorSimulado([como_json(salida_compra)], nombre="openai")

    resultado = await Motor([principal, respaldo], bitacora).procesar(entrada_valida)

    assert resultado.ok and resultado.proveedor == "openai"


async def test_si_fallan_todas_las_apis_devuelve_error_api(entrada_valida, bitacora):
    caido = ProveedorSimulado([ErrorProveedor("conexion", "Sin conexión con la API", "anthropic")])

    resultado = await Motor([caido], bitacora).procesar(entrada_valida)

    assert resultado.error.tipo == "error_api"
    assert "conexion" in resultado.error.mensaje


async def test_presupuesto_de_tokens_corta_el_reintento(entrada_valida, salida_compra, bitacora):
    rota = como_json({**salida_compra, "confianza": 2})
    llm = ProveedorSimulado([rota, como_json(salida_compra)], tokens_entrada=5000, tokens_salida=1000)

    resultado = await Motor([llm], bitacora, presupuesto_tokens=6000).procesar(entrada_valida)

    assert resultado.error.tipo == "presupuesto_agotado"
    assert len(llm.llamadas) == 1


async def test_stop_lejano_se_descarta_con_decision_final(entrada_valida, salida_compra, bitacora):
    # reglas-v0.3: el stop lejano ya no espera a una persona, se descarta (y queda registrado).
    lejos = {**salida_compra, "stop_loss": 760.0, "take_profit": 790.0}
    motor = Motor([ProveedorSimulado([como_json(lejos), como_json(salida_compra)])], bitacora)

    await motor.procesar(entrada_valida)
    await motor.procesar({**entrada_valida, "id_propuesta": "PRP-20261028-0002"})

    # Las dos propuestas tienen decisión de riesgo y final: el KPI técnico da 100 %.
    kpi = kpi_tecnico(bitacora.leer(), ahora_mas(-5), ahora_mas(5), muestra_minima=2)
    assert (kpi.numerador, kpi.denominador, kpi.valor_pct) == (2, 2, 100.0)


async def test_no_operar_registra_confianza_cero(entrada_valida, salida_no_operar, bitacora):
    """Sin señal, la confianza que inventa el modelo (0,95 o 0,0 según la consulta, PE6) se fija en 0."""
    motor = Motor([ProveedorSimulado([como_json({**salida_no_operar, "confianza": 0.95})])], bitacora)

    resultado = await motor.procesar(entrada_valida)

    assert resultado.salida.accion.value == "no_operar"
    assert resultado.salida.confianza == 0.0
    propuesta = [r for r in bitacora.leer() if r.etapa == Etapa.PROPUESTA][0]
    assert '"confianza":0.0' in propuesta.detalle.replace(" ", "")


async def test_una_compra_conserva_la_confianza_del_modelo(entrada_valida, salida_compra, bitacora):
    motor = Motor([ProveedorSimulado([como_json(salida_compra)])], bitacora)
    resultado = await motor.procesar(entrada_valida)
    assert resultado.salida.confianza == salida_compra["confianza"]
