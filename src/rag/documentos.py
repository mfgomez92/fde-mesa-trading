"""Lectura y limpieza de los documentos del corpus, con sus permisos.

La carpeta del corpus tiene los documentos y un `permisos.json` que dice quién
puede leer cada uno:

    {
      "documentos": {
        "reglas-riesgo.md": {"titulo": "Reglas de riesgo", "roles": ["operador"]},
        "kpis.md":          {"titulo": "Ficha de KPIs",    "roles": ["operador", "lector"]}
      }
    }

Para sumar los documentos de la mesa no hay que tocar código: se copian a la
carpeta (ya anonimizados) y se agrega una línea al manifiesto. Un archivo que
no figura queda solo para el Operador (ver ROLES_POR_DEFECTO).

Formatos: Markdown, texto, HTML (por ejemplo, un export de Notion), PDF y datos
estructurados (CSV, JSONL y JSON), que pasan a Markdown con src/rag/conversores.py.

Las claves del manifiesto pueden ser un archivo exacto o un patrón de carpeta
(`"agentes/*"`, `"exports/**"`): manda la entrada exacta y,
si no hay, el patrón más largo que coincida.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

from pydantic import BaseModel, Field

from .conversores import csv_a_markdown, json_a_markdown, jsonl_a_markdown
from .permisos import ROLES_POR_DEFECTO, Rol

log = logging.getLogger("rag.documentos")

MANIFIESTO = "permisos.json"

# Rutas del sistema interno de la mesa (confidencialidad, CLAUDE.md): la estructura del repo
# de la empresa no entra al corpus. Si una exportación nueva las trae, la ingesta falla
# y hay que redactarlas (en el repo full: `python -m scripts.redactar_rutas`). Los ids de
# negocio (ACC-…) no son rutas y quedan. Grupo 1: lo que va antes de la ruta; grupo 2: la ruta.
# Las carpetas propias de la mesa viven en config/rutas_internas.json, que no se publica: sin
# ese archivo el detector sigue marcando cualquier ruta a un archivo y cualquier módulo .py.
RUTAS_INTERNAS = Path(__file__).resolve().parents[2] / "config" / "rutas_internas.json"
_ANTES = r"(^|[\s\"'`(\[=,;:|→])"
_SEGMENTO = r"[A-Za-z0-9_.<>{}*~-]+"
_EXTENSIONES = r"md|json|jsonl|csv|py|log|patch|txt|ya?ml|sh|sql"
# Carpetas de nuestro propio corpus y endpoints públicos: no son estructura de la empresa.
_PROPIAS = r"(?!https?:|www\.|0[1-9]-|journal/|revisiones/|caso/|bot/|curso/|v1|v2/|public/)"


def _patron_rutas(config: Path = RUTAS_INTERNAS) -> re.Pattern:
    datos = json.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
    propias = [rf"{re.escape(a)}[^\s\"'`)\],;|]*" for a in datos.get("absolutas", [])]  # rutas absolutas
    if datos.get("carpetas"):  # carpetas del repo de la mesa
        propias.append(rf"(?:{'|'.join(map(re.escape, datos['carpetas']))})/[^\s\"'`)\],;|]*")
    return re.compile(
        _ANTES + "("
        + "".join(p + "|" for p in propias)
        + rf"{_PROPIAS}{_SEGMENTO}(?:/{_SEGMENTO})+\.(?:{_EXTENSIONES})\b"  # cualquier ruta a un archivo
        r"|[A-Za-z_][A-Za-z0-9_]*\.py\b"  # módulos de código de la mesa
        ")",
        flags=re.MULTILINE,
    )


RUTA_INTERNA = _patron_rutas()


class RutaInternaError(ValueError):
    """Un documento trae rutas internas del sistema de la mesa: no se ingesta nada."""
EXTENSIONES = {".md", ".txt", ".html", ".htm", ".pdf", ".csv", ".jsonl", ".json"}


class Documento(BaseModel):
    id: str  # nombre del archivo dentro del corpus: es lo que se cita como fuente
    titulo: str
    texto: str  # ya limpio; en Markdown cuando el original tiene estructura
    roles: list[Rol] = Field(min_length=1)
    hash: str  # sha256 del texto: si el documento cambia, se sabe qué fragmentos invalidar


class _HtmlATexto(HTMLParser):
    """Pasa HTML a un Markdown mínimo: conserva los títulos (h1-h3) para poder seccionar."""

    def __init__(self) -> None:
        super().__init__()
        self.partes: list[str] = []
        self._ignorar = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._ignorar += 1
        elif tag in ("h1", "h2", "h3"):
            self.partes.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in ("p", "div", "li", "tr", "br", "section", "table"):
            self.partes.append("\n")
        elif tag in ("td", "th"):
            self.partes.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._ignorar -= 1
        elif tag in ("h1", "h2", "h3", "p", "li", "tr"):
            self.partes.append("\n")

    def handle_data(self, data):
        if not self._ignorar:
            self.partes.append(data)


def _leer_crudo(ruta: Path) -> str:
    ext = ruta.suffix.lower()
    if ext in (".md", ".txt"):
        return ruta.read_text(encoding="utf-8")
    if ext in (".html", ".htm"):
        parser = _HtmlATexto()
        parser.feed(ruta.read_text(encoding="utf-8"))
        return "".join(parser.partes)
    if ext == ".pdf":
        from pypdf import PdfReader  # import local: solo hace falta si hay PDFs

        paginas = [p.extract_text() or "" for p in PdfReader(ruta).pages]
        # Cada página como sección, para poder citar "página N".
        return "\n\n".join(f"## Página {i}\n\n{texto}" for i, texto in enumerate(paginas, start=1))
    # Datos estructurados (exports, mandato): se pasan a Markdown en cada ingesta (src/rag/conversores.py).
    if ext == ".csv":
        return csv_a_markdown(ruta.read_text(encoding="utf-8"), ruta.stem)
    if ext == ".jsonl":
        return jsonl_a_markdown(ruta.read_text(encoding="utf-8"), ruta.stem)
    if ext == ".json":
        return json_a_markdown(ruta.read_text(encoding="utf-8"), ruta.stem)
    raise ValueError(f"Formato no soportado: {ruta.name}")


def _celdas(linea: str) -> list[str]:
    return [c.strip() for c in linea.strip().strip("|").split("|")]


def tablas_a_texto(texto: str) -> str:
    """Pasa cada fila de una tabla Markdown a «Encabezado: valor; ...».

    Una tabla cruda («| Stop máximo | 1.0 % |» con su fila de guiones) da un
    embedding pobre: en la primera medición, la tabla de límites no aparecía
    para «¿cuál es el stop máximo?». Con cada fila como oración, sí. El mandato
    de la mesa va a tener tablas (semáforos, límites por operación).
    """
    lineas, salida, i = texto.split("\n"), [], 0
    while i < len(lineas):
        es_tabla = (i + 1 < len(lineas) and lineas[i].lstrip().startswith("|")
                    and re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", lineas[i + 1]) and "-" in lineas[i + 1])
        if not es_tabla:
            salida.append(lineas[i])
            i += 1
            continue
        encabezados = _celdas(lineas[i])
        i += 2
        while i < len(lineas) and lineas[i].lstrip().startswith("|"):
            pares = [f"{e or 'Dato'}: {v}" for e, v in zip(encabezados, _celdas(lineas[i])) if v]
            salida.append("- " + "; ".join(pares) + ".")
            i += 1
    return "\n".join(salida)


def limpiar(texto: str) -> str:
    """Normaliza sin perder estructura: Unicode NFC, tablas como texto, sin espacios ni líneas vacías de más."""
    texto = unicodedata.normalize("NFC", texto).replace("\r\n", "\n").replace("\xa0", " ")
    texto = tablas_a_texto(texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r" *\n *", "\n", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def _titulo(texto: str, ruta: Path) -> str:
    primero = re.search(r"^# (.+)$", texto, flags=re.MULTILINE)
    return primero.group(1).strip() if primero else ruta.stem


def _entrada_manifiesto(entradas: dict, id_doc: str) -> dict | None:
    if id_doc in entradas:
        return entradas[id_doc]
    patrones = [p for p in entradas if any(c in p for c in "*?[") and fnmatch.fnmatch(id_doc, p)]
    return entradas[max(patrones, key=len)] if patrones else None


def cargar_documentos(carpeta: Path) -> list[Documento]:
    """Lee todos los documentos de la carpeta, con los roles del manifiesto."""
    carpeta = Path(carpeta)
    ruta_manifiesto = carpeta / MANIFIESTO
    manifiesto = json.loads(ruta_manifiesto.read_text(encoding="utf-8")) if ruta_manifiesto.exists() else {}
    entradas: dict = manifiesto.get("documentos", {})

    documentos = []
    for ruta in sorted(p for p in carpeta.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONES
                        and p.name != MANIFIESTO):
        id_doc = ruta.relative_to(carpeta).as_posix()
        entrada = _entrada_manifiesto(entradas, id_doc)
        if entrada is None:
            log.warning("%s no está en %s: queda solo para %s", id_doc, MANIFIESTO,
                        ", ".join(r.value for r in ROLES_POR_DEFECTO))
            entrada = {}
        try:
            texto = limpiar(_leer_crudo(ruta))
        except (ValueError, UnicodeDecodeError) as e:  # json.JSONDecodeError es un ValueError
            # Un archivo roto no frena a los demás (el script de la clase cortaba todo el directorio).
            log.error("%s no se pudo leer (%s): no se ingesta", id_doc, e)
            continue
        if not texto:
            log.warning("%s está vacío: no se ingesta", id_doc)
            continue
        documentos.append(
            Documento(
                id=id_doc,
                # El título solo sale de una entrada exacta: un patrón de carpeta da roles, no nombres.
                titulo=(entradas.get(id_doc) or {}).get("titulo") or _titulo(texto, ruta),
                texto=texto,
                roles=[Rol(r) for r in entrada.get("roles", [r.value for r in ROLES_POR_DEFECTO])],
                hash=hashlib.sha256(texto.encode("utf-8")).hexdigest(),
            )
        )

    con_rutas = {d.id: sorted({m.group(2) for m in RUTA_INTERNA.finditer(d.texto)})[:3]
                 for d in documentos if RUTA_INTERNA.search(d.texto)}
    if con_rutas:
        detalle = "; ".join(f"{doc}: {', '.join(rutas)}" for doc, rutas in con_rutas.items())
        raise RutaInternaError(f"Hay rutas internas de la mesa en {len(con_rutas)} documento(s) ({detalle}). "
                               "Redactalas antes de ingestar (en el repo full: python -m scripts.redactar_rutas).")

    sobrantes = {e for e in entradas if not any(c in e for c in "*?[")} - {d.id for d in documentos}
    if sobrantes:
        log.warning("El manifiesto nombra archivos que no están en la carpeta: %s", ", ".join(sorted(sobrantes)))
    return documentos
