"""La señal la calcula el código y el LLM solo confirma o veta (lección de la PE2)."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from src.bitacora import Etapa
from src.contratos import Entrada
from src.motor import Motor
from src.proveedores import ErrorProveedor, ProveedorSimulado
from src.senal import ParametrosEstrategia, calcular_senal


V01 = ParametrosEstrategia.v01()  # la estrategia de la PE2, para los tests del motor de ese camino


def _entrada(datos: dict, **ind) -> Entrada:
    return Entrada.model_validate({**datos, "indicadores": {**datos["indicadores"], **ind}})


def _veredicto(tipo: str, fuentes: list[str] | None = None) -> str:
    return json.dumps({"veredicto": tipo, "motivo": f"Simulado: {tipo} según el mandato de la mesa.",
                       "fuentes": fuentes or []})


# ---------------------------------------------------------------- la señal (v0.1, camino de la PE2)

def test_compra_con_ema_rapida_arriba_y_rsi_en_zona(entrada_valida):
    s = calcular_senal(_entrada(entrada_valida), V01).salida  # EMA 771.23 > 771.19, RSI 57.2, ATR 1.084
    assert s.accion.value == "comprar"
    riesgo = Decimal("1.63")  # 1,5 ATR, en centavos
    assert (s.precio_entrada, s.stop_loss, s.take_profit) == (
        Decimal("771.89"), Decimal("771.89") - riesgo, Decimal("771.89") + 2 * riesgo)


def test_venta_con_ema_rapida_abajo_y_rsi_en_zona(entrada_valida):
    s = calcular_senal(_entrada(entrada_valida, ema_rapida=770.0, rsi=42), V01).salida
    assert s.accion.value == "vender" and s.take_profit < s.precio_entrada < s.stop_loss


@pytest.mark.parametrize("rsi", [49.9, 70.1, 85])
def test_sin_senal_si_el_rsi_no_acompana(entrada_valida, rsi):
    s = calcular_senal(_entrada(entrada_valida, rsi=rsi), V01).salida
    assert s.accion.value == "no_operar" and s.precio_entrada is None


def test_la_misma_entrada_da_siempre_la_misma_senal(entrada_valida):
    """Lo que el LLM no garantizaba: en la PE2 la misma consulta dio confianza 0,45 y 0,60."""
    e = _entrada(entrada_valida)
    assert len({calcular_senal(e, V01).model_dump_json() for _ in range(20)}) == 1


def test_los_parametros_se_cambian_sin_tocar_codigo(entrada_valida):
    p = V01.model_copy(update={"version": "estrategia-prueba", "rsi_compra_max": 55})
    assert calcular_senal(_entrada(entrada_valida), p).salida.accion.value == "no_operar"


# ---------------------------------------------------------------- la señal v0.2 (respuesta de la mesa del 02/11)

# 11:00 ET del 28/10: cruce hacia arriba en la última vela, RSI 57, precio sobre el VWAP y régimen largo.
CRUCE = {"hora_vela": "2026-10-28T15:00:00+00:00", "ema_rapida_anterior": 771.10, "ema_lenta_anterior": 771.15,
         "vwap": 771.00, "cierre_diario_anterior": 769.66, "sma_diaria": 767.53}


def _v02(entrada_valida, **cambios) -> Entrada:
    ind = {k: v for k, v in {**CRUCE, **cambios}.items() if k != "hora_vela"}
    datos = {**entrada_valida, "hora_vela": cambios.get("hora_vela", CRUCE["hora_vela"])}
    return _entrada(datos, **ind)


def test_v02_compra_en_el_cruce_con_filtros(entrada_valida):
    r = calcular_senal(_v02(entrada_valida))
    assert r.salida.accion.value == "comprar" and r.version_estrategia == "estrategia-v0.2"
    assert "cruzó sobre" in r.regla and "VWAP" in r.regla and "régimen largo" in r.regla


def test_v02_sin_cruce_no_hay_senal_aunque_la_rapida_este_arriba(entrada_valida):
    # La rápida ya estaba arriba en la vela anterior: no es el momento del cruce.
    r = calcular_senal(_v02(entrada_valida, ema_rapida_anterior=771.20))
    assert r.salida.accion.value == "no_operar"


def test_v02_venta_en_el_cruce_hacia_abajo(entrada_valida):
    r = calcular_senal(_v02(entrada_valida, ema_rapida=771.0, ema_lenta=771.19, ema_rapida_anterior=771.3,
                            ema_lenta_anterior=771.2, rsi=42, vwap=772.5, cierre_diario_anterior=760.0))
    assert r.salida.accion.value == "vender" and "régimen corto" in r.regla


@pytest.mark.parametrize("cambios, motivo", [
    ({"vwap": 772.0}, "VWAP"),  # precio 771,89 bajo el VWAP: no hay largo
    ({"cierre_diario_anterior": 760.0}, "régimen"),  # cerró bajo la SMA 50: no hay largos
    ({"hora_vela": "2026-10-28T13:55:00+00:00"}, "horario"),  # 09:55 ET
    ({"hora_vela": "2026-10-28T19:35:00+00:00"}, "horario"),  # 15:35 ET
    ({"ema_rapida_anterior": None}, "vela anterior"),
])
def test_v02_filtros_frenan_la_senal(entrada_valida, cambios, motivo):
    r = calcular_senal(_v02(entrada_valida, **cambios))
    assert r.salida.accion.value == "no_operar" and motivo in r.regla


@pytest.mark.parametrize("hora", ["2026-10-28T14:00:00+00:00", "2026-10-28T19:30:00+00:00"])  # 10:00 y 15:30 ET
def test_v02_bordes_del_horario_entran(entrada_valida, hora):
    assert calcular_senal(_v02(entrada_valida, hora_vela=hora)).salida.accion.value == "comprar"


# ---------------------------------------------------------------- el motor con la señal en código

async def test_sin_senal_no_se_llama_al_modelo(entrada_valida, bitacora):
    llm = ProveedorSimulado([])
    r = await Motor([llm], bitacora, estrategia=V01).procesar_con_senal({**entrada_valida, "indicadores": {
        **entrada_valida["indicadores"], "rsi": 80}})
    assert llm.llamadas == [] and r.tokens_entrada == 0
    assert r.riesgo.decision.value == "sin_operacion" and r.decision_final == "no_operar"


async def test_senal_confirmada_se_simula_la_orden_y_queda_todo_en_la_bitacora(entrada_valida, bitacora):
    llm = ProveedorSimulado([_veredicto("confirmar")])
    r = await Motor([llm], bitacora, estrategia=V01).procesar_con_senal(entrada_valida)
    assert r.riesgo.decision.value == "aprobada_auto" and r.decision_final == "orden_simulada"
    registros = bitacora.leer()
    assert [x.etapa for x in registros] == [Etapa.PROPUESTA, Etapa.DECISION_RIESGO, Etapa.DECISION_FINAL]
    assert registros[0].autor == "codigo-senal" and registros[0].version_prompt.startswith("estrategia-v0.1+")
    # El modelo recibe la operación ya calculada, no las reglas para calcularla.
    assert "771.89" in llm.llamadas[0][0]["content"]


async def test_veto_con_motivo_descarta_y_cuenta_como_riesgo_no_como_falla(entrada_valida, bitacora):
    r = await Motor([ProveedorSimulado([_veredicto("vetar")])], bitacora, estrategia=V01).procesar_con_senal(entrada_valida)
    assert r.decision_final == "vetada"
    final = [x for x in bitacora.leer() if x.etapa == Etapa.DECISION_FINAL][0]
    assert (final.autor, final.categoria_motivo.value, final.codigo_motivo) == ("llm-veto", "riesgo", "VETO_LLM")


async def test_el_veto_solo_puede_citar_fragmentos_que_recibio(entrada_valida, bitacora):
    class Frag:
        fragmento_id, titulo, seccion, texto = "mandato.md#001", "Mandato", "Horarios", "No operar en la apertura."

    llm = ProveedorSimulado([_veredicto("vetar", ["mandato.md#001", "inventado.md#9"])])
    r = await Motor([llm], bitacora, estrategia=V01).procesar_con_senal(entrada_valida, contexto=lambda e, s: [Frag()])
    assert r.veredicto.fuentes == ["mandato.md#001"]
    assert "No operar en la apertura." in llm.llamadas[0][0]["content"]


async def test_con_el_veto_opcional_un_llm_caido_deja_seguir_la_senal(entrada_valida, bitacora):
    # Camino de la PE2: el veto era opcional. Desde el 02/11 la mesa lo pidió obligatorio.
    caido = ErrorProveedor("conexion", "sin red", "simulado")
    motor = Motor([ProveedorSimulado([caido])], bitacora, estrategia=V01, veto_obligatorio=False)
    r = await motor.procesar_con_senal(entrada_valida)
    assert r.decision_final == "orden_simulada"  # el riesgo en código ya la aprobó
    final = [x for x in bitacora.leer() if x.etapa == Etapa.DECISION_FINAL][0]
    assert "Veto no disponible" in final.detalle


async def test_por_defecto_el_veto_es_obligatorio_y_un_llm_caido_descarta(entrada_valida, bitacora):
    caido = ErrorProveedor("conexion", "sin red", "simulado")
    motor = Motor([ProveedorSimulado([caido])], bitacora, estrategia=V01)  # falla cerrada por defecto
    r = await motor.procesar_con_senal(entrada_valida)
    assert r.decision_final == "descartada"
    final = [x for x in bitacora.leer() if x.etapa == Etapa.DECISION_FINAL][0]
    assert final.categoria_motivo.value == "operativo"


async def test_stop_lejano_se_descarta_sin_llamar_al_modelo(entrada_valida, bitacora):
    # ATR alto: stop a 1,5 ATR = 15 USD, más del 1 % del precio -> reglas-v0.3 lo descarta.
    datos = {**entrada_valida, "indicadores": {**entrada_valida["indicadores"], "atr": 10.0}}
    llm = ProveedorSimulado([])
    r = await Motor([llm], bitacora, estrategia=V01).procesar_con_senal(datos)
    assert r.riesgo.codigo_motivo == "STOP_LEJANO" and r.decision_final == "descartada"
    assert llm.llamadas == []
