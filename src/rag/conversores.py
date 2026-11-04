"""Conversores deterministas de datos estructurados a Markdown (CSV, JSONL y JSON).

Es el mismo patrón que la clase del M3: primero normalizar todo a Markdown y
después cortar por la estructura. Así el fragmentado estructural no cambia y
cada registro queda como una sección con título propio:

    # operaciones
    ## P01 · 2026-10-13 · prueba · SPY
    - id: P01; fecha_entrada_et: 2026-10-13; simbolo: SPY; ...

- El id del registro va en el título: BM25 lo encuentra con la búsqueda exacta
  (ids de órdenes, claves de rechazos, nombres de estrategia).
- Los campos vacíos no se escriben: no son evidencia y meten ruido.
- El Markdown se genera en cada `ingestar` y no se guarda: no hay una copia que
  se desactualice respecto del export.
- Un JSON anidado (por ejemplo, el mandato) pasa a títulos por nivel y una
  línea «clave: valor» por dato, así cada sección del mandato es citable.

Equivalencia TS: es un `map` de cada fila a un string, como `rows.map(toMarkdown).join("\\n")`.
"""

from __future__ import annotations

import csv
import io
import json
import logging

log = logging.getLogger("rag.conversores")

# Campos que identifican un registro, en orden de preferencia.
CAMPOS_ID = ("id", "key", "clave", "id_orden", "order_id")
# Campos que, si están, se suman al título para que se lea y se busque mejor.
CAMPOS_TITULO = ("fecha", "fecha_senal", "fecha_entrada_et", "day", "ts_et", "ts_art",
                 "estrategia", "strategy", "sub_libro", "simbolo", "symbol", "tipo", "code")


def _texto(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, (dict, list)):
        return json.dumps(valor, ensure_ascii=False)
    return str(valor).strip()


def registro_a_markdown(registro: dict, numero: int, nombre: str) -> str:
    """Un registro -> «## titulo» + una línea con los campos que tienen valor."""
    campos = {str(k).strip(): _texto(v) for k, v in registro.items() if k is not None and str(k).strip()}
    campos = {k: v for k, v in campos.items() if v}
    if not campos:
        return ""
    ident = next((campos[c] for c in CAMPOS_ID if c in campos), f"{nombre} #{numero}")
    extra = [campos[c] for c in CAMPOS_TITULO if c in campos and campos[c] != ident]
    titulo = " · ".join([ident, *dict.fromkeys(extra)])  # sin repetir valores
    cuerpo = "; ".join(f"{k}: {v}" for k, v in campos.items())
    return f"## {titulo}\n\n- {cuerpo}.\n"


def _registros_a_markdown(registros: list[dict], nombre: str) -> str:
    secciones = [registro_a_markdown(r, i, nombre) for i, r in enumerate(registros, start=1)]
    return f"# {nombre}\n\n" + "\n".join(s for s in secciones if s)


def csv_a_markdown(texto: str, nombre: str) -> str:
    texto = texto.lstrip("﻿")  # BOM de Excel
    if not texto.strip():
        return ""
    # El delimitador sale del encabezado: csv.Sniffer falla con filas desparejas (un export
    # sucio de Excel en español usa «;»). Si no aparece ninguno, coma.
    encabezado = texto.splitlines()[0]
    delimitador = max(",;\t", key=encabezado.count) if any(d in encabezado for d in ",;\t") else ","
    filas = list(csv.DictReader(io.StringIO(texto), delimiter=delimitador))
    # Una fila con más celdas que encabezados deja el sobrante en la clave None: se descarta.
    return _registros_a_markdown(filas, nombre)


def jsonl_a_markdown(texto: str, nombre: str) -> str:
    registros = []
    for n, linea in enumerate(texto.lstrip("﻿").splitlines(), start=1):
        if not linea.strip():
            continue
        try:
            dato = json.loads(linea)
        except json.JSONDecodeError as e:
            log.warning("%s, línea %d: JSON inválido (%s); se saltea", nombre, n, e.msg)
            continue
        registros.append(dato if isinstance(dato, dict) else {"valor": dato})
    return _registros_a_markdown(registros, nombre)


def _json_anidado(dato, nivel: int, lineas: list[str]) -> None:
    """Diccionarios -> títulos por nivel; escalares y listas de escalares -> «- clave: valor»."""
    if isinstance(dato, dict):
        hojas = {k: v for k, v in dato.items() if not isinstance(v, (dict, list)) or
                 (isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v))}
        for k, v in hojas.items():
            valor = ", ".join(_texto(x) for x in v) if isinstance(v, list) else _texto(v)
            if valor:
                lineas.append(f"- {k}: {valor}")
        for k, v in dato.items():
            if k in hojas:
                continue
            lineas.append(f"\n{'#' * min(nivel, 6)} {k}\n")
            _json_anidado(v, nivel + 1, lineas)
    elif isinstance(dato, list):
        for i, item in enumerate(dato, start=1):
            if isinstance(item, dict):
                ident = next((_texto(item[c]) for c in (*CAMPOS_ID, "nombre", "name", "evento") if item.get(c)), "")
                lineas.append(f"\n{'#' * min(nivel, 6)} {ident or f'Elemento {i}'}\n")
                _json_anidado(item, nivel + 1, lineas)
            else:
                lineas.append(f"- {_texto(item)}")


def json_a_markdown(texto: str, nombre: str) -> str:
    dato = json.loads(texto.lstrip("﻿"))
    if isinstance(dato, list) and all(isinstance(x, dict) for x in dato):
        return _registros_a_markdown(dato, nombre)  # una lista de registros, como un JSONL
    lineas = [f"# {nombre}"]
    _json_anidado(dato, 2, lineas)
    return "\n".join(lineas) + "\n"
