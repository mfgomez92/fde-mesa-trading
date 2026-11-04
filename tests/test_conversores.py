"""Conversores de CSV, JSONL y JSON a Markdown y patrones del manifiesto de permisos (PE3)."""

from __future__ import annotations

import json

from src.rag.conversores import csv_a_markdown, json_a_markdown, jsonl_a_markdown
from src.rag.documentos import cargar_documentos
from src.rag.fragmentado import CONFIGS, fragmentar
from src.rag.permisos import Rol


def test_csv_un_titulo_por_registro_con_su_id():
    md = csv_a_markdown("id,fecha,simbolo,pnl\nP01,2026-10-13,IWM,-0.16\nP02,2026-10-13,IWM,-0.03\n", "trades")
    assert md.startswith("# trades")
    assert "## P01 · 2026-10-13 · IWM" in md and "## P02 · 2026-10-13 · IWM" in md
    assert "- id: P01; fecha: 2026-10-13; simbolo: IWM; pnl: -0.16." in md


def test_csv_sucio_bom_punto_y_coma_vacios_y_celdas_de_mas():
    sucio = "﻿id;estado;nota\nP01;cerrada;\n;abierta;sin id\nP03;cerrada;a;b;c\n\n"
    md = csv_a_markdown(sucio, "trades")
    assert "﻿" not in md
    assert "nota:" not in md.split("## P01")[1].split("##")[0]  # el campo vacío no se escribe
    assert "## trades #2" in md  # sin id: número de registro
    assert "## P03" in md and "None" not in md  # las celdas de más se descartan


def test_csv_vacio_no_genera_nada():
    assert csv_a_markdown("", "x") == "" and csv_a_markdown("﻿  \n", "x") == ""


def test_jsonl_usa_key_y_saltea_lineas_rotas():
    texto = ('{"key": "2026-10-14|EST-A|AAA|100.10|95.20", "symbol": "AAA", "code": "CODIGO_X", "level": null}\n'
             "esto no es json\n\n"
             '{"symbol": "XLE", "code": "CODIGO_Y"}\n'
             '["no", "es", "un", "objeto"]\n')
    md = jsonl_a_markdown(texto, "rejected")
    assert "## 2026-10-14|EST-A|AAA|100.10|95.20 · AAA · CODIGO_X" in md
    assert "level" not in md  # null no es evidencia
    assert "## rejected #2 · XLE · CODIGO_Y" in md
    assert "valor: [" in md  # un valor que no es objeto igual queda, como dato
    assert "esto no es json" not in md


def test_json_anidado_pasa_a_titulos_por_nivel():
    mandato = {"version": "mandato-v1.1", "limites_duros": {"freno_drawdown_pct": -0.04, "stop_nativo": True},
               "universo": {"etfs": ["SPY", "QQQ"], "cortos": {"habilitados": True}},
               "eventos": [{"id": 1, "evento": "Escalada en el Golfo"}, {"evento": "Sin id"}]}
    md = json_a_markdown(json.dumps(mandato), "mandato")
    assert md.startswith("# mandato\n- version: mandato-v1.1")
    assert "## limites_duros" in md and "- freno_drawdown_pct: -0.04" in md
    assert "- etfs: SPY, QQQ" in md and "### cortos" in md
    assert "### 1" in md and "### Sin id" in md


def test_json_lista_de_registros_se_trata_como_jsonl():
    md = json_a_markdown(json.dumps([{"id": "A1", "x": 1}, {"id": "A2", "x": 2}]), "lista")
    assert "## A1" in md and "## A2" in md


def test_cada_registro_es_un_fragmento_citable_y_el_id_queda_en_el_texto(tmp_path):
    (tmp_path / "operaciones.csv").write_text("id,simbolo\nP01,IWM\nP02,SPY\n", encoding="utf-8")
    doc = cargar_documentos(tmp_path)[0]
    fragmentos = fragmentar(doc, CONFIGS["estructural"])
    assert [f.seccion for f in fragmentos] == ["operaciones > P01 · IWM", "operaciones > P02 · SPY"]
    assert "P01" in fragmentos[0].texto  # BM25 lo encuentra por id


def test_patrones_de_carpeta_en_el_manifiesto(tmp_path):
    (tmp_path / "guias").mkdir()
    (tmp_path / "registros").mkdir()
    (tmp_path / "guias" / "riesgo.md").write_text("# Riesgo\n\nEl veto es de Riesgo.\n", encoding="utf-8")
    (tmp_path / "registros" / "operaciones.csv").write_text("id,simbolo\nP01,IWM\n", encoding="utf-8")
    (tmp_path / "registros" / "especial.csv").write_text("id,simbolo\nP09,SPY\n", encoding="utf-8")
    (tmp_path / "suelto.md").write_text("# Suelto\n\nNo figura en el manifiesto.\n", encoding="utf-8")
    (tmp_path / "permisos.json").write_text(json.dumps({"documentos": {
        "guias/*": {"roles": ["operador", "lector"]},
        "registros/*": {"roles": ["operador"]},
        "registros/especial.csv": {"titulo": "Especial", "roles": ["operador", "lector"]},  # la exacta manda
    }}), encoding="utf-8")
    docs = {d.id: d for d in cargar_documentos(tmp_path)}
    assert docs["guias/riesgo.md"].roles == [Rol.OPERADOR, Rol.LECTOR]
    assert docs["registros/operaciones.csv"].roles == [Rol.OPERADOR]
    assert docs["registros/especial.csv"].roles == [Rol.OPERADOR, Rol.LECTOR]
    assert docs["registros/especial.csv"].titulo == "Especial" and docs["registros/operaciones.csv"].titulo == "operaciones"
    assert docs["suelto.md"].roles == [Rol.OPERADOR]  # sin entrada: solo Operador
    assert "permisos.json" not in docs  # el manifiesto no se ingesta como documento


def test_un_archivo_roto_no_frena_a_los_demas(tmp_path):
    (tmp_path / "roto.json").write_text("{ esto no cierra", encoding="utf-8")
    (tmp_path / "bien.md").write_text("# Bien\n\nEste sí se ingesta.\n", encoding="utf-8")
    assert [d.id for d in cargar_documentos(tmp_path)] == ["bien.md"]


# ---------------------------------------------------------------- confidencialidad: rutas internas
# Los casos con la estructura real de la mesa están en tests/test_rutas_internas.py (solo en el repo full).

def test_la_ingesta_rechaza_rutas_a_archivos_internos(tmp_path):
    import pytest

    from src.rag.documentos import RutaInternaError

    (tmp_path / "export.csv").write_text("id,fuente\nP01,sistema/registro/libro.jsonl\n", encoding="utf-8")  # ruta-ficticia
    (tmp_path / "bien.md").write_text("# Bien\n\nSin rutas.\n", encoding="utf-8")
    with pytest.raises(RutaInternaError, match="Redactalas"):
        cargar_documentos(tmp_path)


def test_el_detector_suma_las_carpetas_internas_de_la_configuracion(tmp_path):
    from src.rag.documentos import _patron_rutas

    config = tmp_path / "rutas_internas.json"
    config.write_text(json.dumps({"absolutas": ["/datos-privados/"], "carpetas": ["secreta"]}), encoding="utf-8")
    con, sin = _patron_rutas(config), _patron_rutas(tmp_path / "no-existe.json")
    for texto in ["ver /datos-privados/diag", "en secreta/sub/"]:  # sin extensión: solo con la configuración
        assert con.search(texto) and not sin.search(texto)
    for texto in ["fuente: sistema/registro/libro.jsonl", "lista de politica.py"]:  # siempre (ruta-ficticia)
        assert con.search(texto) and sin.search(texto)
    for texto in ["orden ORD-20261023-XYZ-E-0000", "R/B de 1:1,5", "GET v2/orders", "America/New_York"]:
        assert not con.search(texto)
