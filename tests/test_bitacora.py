from datetime import datetime, timezone

from src.bitacora import CategoriaMotivo, Etapa, RegistroBitacora, kpi_negocio, kpi_tecnico

LUNES = datetime(2026, 10, 26, 13, 30, tzinfo=timezone.utc)
SABADO = datetime(2026, 10, 31, 0, 0, tzinfo=timezone.utc)


def fila(id_: str, etapa: Etapa, hora: datetime, categoria=CategoriaMotivo.NINGUNO) -> RegistroBitacora:
    return RegistroBitacora(id_propuesta=id_, etapa=etapa, hora=hora, decision="x", autor="test",
                            categoria_motivo=categoria, version_reglas="reglas-v0.1", version_prompt="prompt-v1")


def test_la_bitacora_guarda_y_lee_con_hora_y_versiones(bitacora):
    bitacora.registrar(fila("PRP-20261026-0001", Etapa.PROPUESTA, LUNES))
    leida = bitacora.leer()[0]
    assert leida.hora == LUNES
    assert (leida.version_reglas, leida.version_prompt) == ("reglas-v0.1", "prompt-v1")


def test_kpi_tecnico_cuenta_solo_propuestas_completas_de_la_ventana():
    registros = [
        fila("A", Etapa.PROPUESTA, LUNES), fila("A", Etapa.DECISION_RIESGO, LUNES), fila("A", Etapa.DECISION_FINAL, LUNES),
        fila("B", Etapa.PROPUESTA, LUNES), fila("B", Etapa.DECISION_RIESGO, LUNES),  # sin decisión final
        fila("C", Etapa.PROPUESTA, SABADO),  # fuera de la ventana
    ]
    kpi = kpi_tecnico(registros, LUNES, SABADO, muestra_minima=2)
    assert (kpi.numerador, kpi.denominador, kpi.valor_pct, kpi.concluyente) == (1, 2, 50.0, True)


def test_kpi_sin_muestra_suficiente_no_es_concluyente():
    registros = [fila("A", Etapa.PROPUESTA, LUNES)]
    assert not kpi_tecnico(registros, LUNES, SABADO).concluyente
    assert kpi_tecnico([], LUNES, SABADO).valor_pct is None


def test_kpi_negocio_separa_causas_operativas_de_riesgo():
    registros = [
        fila("A", Etapa.DECISION_FINAL, LUNES, CategoriaMotivo.OPERATIVO),
        fila("B", Etapa.DECISION_FINAL, LUNES, CategoriaMotivo.RIESGO),
        fila("C", Etapa.DECISION_FINAL, LUNES),
        fila("D", Etapa.DECISION_FINAL, LUNES),
    ]
    assert kpi_negocio(registros, LUNES, SABADO).valor_pct == 25.0
