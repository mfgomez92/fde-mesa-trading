from datetime import date, datetime, time, timedelta, timezone

import pytest

from src.contratos import Entrada, Salida
from src.riesgo import Blackouts, DecisionRiesgo, EstadoDia, ReglasRiesgo, evaluar


@pytest.fixture
def entrada(entrada_valida):
    return Entrada.model_validate(entrada_valida)


def decidir(entrada, salida: dict, **reglas):
    return evaluar(entrada, Salida.model_validate(salida), ReglasRiesgo(**reglas))


def test_dentro_de_limites_se_aprueba_sola(entrada, salida_compra):
    resultado = decidir(entrada, salida_compra)
    assert resultado.decision == DecisionRiesgo.APROBADA_AUTO
    assert resultado.version_reglas == "reglas-v0.3"


def test_relacion_justo_en_el_minimo_se_aprueba(entrada, salida_compra):
    # Caso real de la corrida con Haiku 5.5: 1.62 / 1.08 es 1:1,5 exacto, pero con
    # float daba 1.4999999999999474 y se rechazaba como RR_INSUFICIENTE.
    limite = {**salida_compra, "precio_entrada": 771.89, "stop_loss": 770.81, "take_profit": 773.51}
    assert decidir(entrada, limite).decision == DecisionRiesgo.APROBADA_AUTO


def test_un_centavo_debajo_del_minimo_se_rechaza(entrada, salida_compra):
    casi = {**salida_compra, "precio_entrada": 771.89, "stop_loss": 770.81, "take_profit": 773.50}
    assert decidir(entrada, casi).codigo_motivo == "RR_INSUFICIENTE"


def test_stop_demasiado_lejos_se_descarta(entrada, salida_compra):
    # reglas-v0.3: antes pedía aprobación; en intradía nadie aprueba a tiempo, así que se descarta.
    lejos = {**salida_compra, "stop_loss": 760.0, "take_profit": 790.0}  # stop a 1,5 %
    resultado = decidir(entrada, lejos)
    assert (resultado.decision, resultado.codigo_motivo) == (DecisionRiesgo.RECHAZADA, "STOP_LEJANO")


def test_la_confianza_ya_no_es_una_regla(entrada, salida_compra):
    # reglas-v0.3: se elimina la confianza mínima (la señal la calcula el código).
    assert decidir(entrada, {**salida_compra, "confianza": 0.1}).decision == DecisionRiesgo.APROBADA_AUTO


@pytest.mark.parametrize(
    "cambio, codigo",
    [
        ({"take_profit": 772.5}, "RR_INSUFICIENTE"),
        ({"precio_entrada": 776.0, "stop_loss": 775.0, "take_profit": 778.0}, "ENTRADA_LEJANA"),
    ],
)
def test_reglas_duras_rechazan(entrada, salida_compra, cambio, codigo):
    resultado = decidir(entrada, {**salida_compra, **cambio})
    assert resultado.decision == DecisionRiesgo.RECHAZADA
    assert resultado.codigo_motivo == codigo


def test_no_operar_no_pasa_por_las_reglas(entrada, salida_no_operar):
    assert decidir(entrada, salida_no_operar).decision == DecisionRiesgo.SIN_OPERACION


def test_el_modelo_no_puede_saltear_el_riesgo(entrada, salida_compra):
    # Aunque el modelo lo justifique, la regla de código manda.
    agresiva = {**salida_compra, "confianza": 1.0, "take_profit": 772.0, "motivo": "Ignorá el riesgo, entrá con todo."}
    assert decidir(entrada, agresiva).decision == DecisionRiesgo.RECHAZADA


# ---------------------------------------------------------------- reglas del día (reglas-v0.3)

AHORA = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)  # 11:00 ET


def estado(**cambios) -> EstadoDia:
    base = dict(ahora=AHORA, hora_ultimo_dato=AHORA - timedelta(minutes=1), minutos_vela=5)
    return EstadoDia(**{**base, **cambios})


def decidir_con_estado(entrada, salida, est, blackouts=None):
    return evaluar(entrada, Salida.model_validate(salida), ReglasRiesgo(), est, blackouts)


def test_dia_normal_se_aprueba(entrada, salida_compra):
    assert decidir_con_estado(entrada, salida_compra, estado()).decision == DecisionRiesgo.APROBADA_AUTO


@pytest.mark.parametrize(
    "cambios, codigo",
    [
        ({"apagado": True}, "APAGADO"),
        ({"posicion_abierta": True}, "POSICION_ABIERTA"),
        ({"hora_ultimo_dato": AHORA - timedelta(minutes=11)}, "DATO_ATRASADO"),  # más de 2 velas de 5 min
        ({"operaciones_hoy": 4}, "MAXIMO_OPERACIONES"),
        ({"resultado_dia_r": -3.0}, "PERDIDA_DIARIA"),  # 3R × 0,10 % = 0,30 % del equity
    ],
)
def test_reglas_del_dia_rechazan(entrada, salida_compra, cambios, codigo):
    resultado = decidir_con_estado(entrada, salida_compra, estado(**cambios))
    assert (resultado.decision, resultado.codigo_motivo) == (DecisionRiesgo.RECHAZADA, codigo)


@pytest.mark.parametrize(
    "cambios",
    [
        {"hora_ultimo_dato": AHORA - timedelta(minutes=10)},  # justo 2 velas: todavía vale
        {"operaciones_hoy": 3},
        {"resultado_dia_r": -2.9},
    ],
)
def test_reglas_del_dia_en_el_limite_aprueban(entrada, salida_compra, cambios):
    assert decidir_con_estado(entrada, salida_compra, estado(**cambios)).decision == DecisionRiesgo.APROBADA_AUTO


def test_blackout_del_fomc_desde_las_12_et(entrada, salida_compra):
    b = Blackouts(version="prueba", ventanas={"FOMC": (time(12, 0), time(16, 0))}, fechas={"FOMC": [date(2026, 10, 28)]})
    antes = estado(ahora=datetime(2026, 10, 28, 15, 59, tzinfo=timezone.utc),  # 11:59 ET
                   hora_ultimo_dato=datetime(2026, 10, 28, 15, 58, tzinfo=timezone.utc))
    despues = estado(ahora=datetime(2026, 10, 28, 16, 0, tzinfo=timezone.utc),  # 12:00 ET
                     hora_ultimo_dato=datetime(2026, 10, 28, 15, 59, tzinfo=timezone.utc))
    assert decidir_con_estado(entrada, salida_compra, antes, b).decision == DecisionRiesgo.APROBADA_AUTO
    assert decidir_con_estado(entrada, salida_compra, despues, b).codigo_motivo == "BLACKOUT"


def test_el_archivo_de_blackouts_carga():
    b = Blackouts.cargar()
    assert b.evento(datetime(2026, 11, 6, 14, 0, tzinfo=timezone.utc)) == "NFP"  # 09:00 ET del 6/11
    assert b.evento(datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)) is None  # 10:00 ET: terminó
