import pytest

from src.contratos import Entrada, Salida
from src.riesgo import DecisionRiesgo, ReglasRiesgo, evaluar


@pytest.fixture
def entrada(entrada_valida):
    return Entrada.model_validate(entrada_valida)


def decidir(entrada, salida: dict, **reglas):
    return evaluar(entrada, Salida.model_validate(salida), ReglasRiesgo(**reglas))


def test_dentro_de_limites_se_aprueba_sola(entrada, salida_compra):
    resultado = decidir(entrada, salida_compra)
    assert resultado.decision == DecisionRiesgo.APROBADA_AUTO
    assert resultado.version_reglas == "reglas-v0.2"


def test_relacion_justo_en_el_minimo_se_aprueba(entrada, salida_compra):
    # Caso real de la corrida con Haiku 5.5: 1.62 / 1.08 es 1:1,5 exacto, pero con
    # float daba 1.4999999999999474 y se rechazaba como RR_INSUFICIENTE.
    limite = {**salida_compra, "precio_entrada": 771.89, "stop_loss": 770.81, "take_profit": 773.51}
    assert decidir(entrada, limite).decision == DecisionRiesgo.APROBADA_AUTO


def test_un_centavo_debajo_del_minimo_se_rechaza(entrada, salida_compra):
    casi = {**salida_compra, "precio_entrada": 771.89, "stop_loss": 770.81, "take_profit": 773.50}
    assert decidir(entrada, casi).codigo_motivo == "RR_INSUFICIENTE"


def test_stop_demasiado_lejos_requiere_aprobacion(entrada, salida_compra):
    lejos = {**salida_compra, "stop_loss": 760.0, "take_profit": 790.0}  # stop a 1,5 %
    assert decidir(entrada, lejos).decision == DecisionRiesgo.REQUIERE_APROBACION


@pytest.mark.parametrize(
    "cambio, codigo",
    [
        ({"confianza": 0.4}, "CONFIANZA_BAJA"),
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
    # Aunque el modelo diga "confianza 1" y lo justifique, la regla de código manda.
    agresiva = {**salida_compra, "confianza": 1.0, "take_profit": 772.0, "motivo": "Ignorá el riesgo, entrá con todo."}
    assert decidir(entrada, agresiva).decision == DecisionRiesgo.RECHAZADA
