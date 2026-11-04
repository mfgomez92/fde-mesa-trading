"""Fragmentado (chunking) de los documentos: dos estrategias para comparar.

- "estructural" (la base que recomienda el curso): corta por los títulos del
  Markdown, así cada fragmento queda dentro de una sección. Si una sección es
  más larga que `tamano`, la parte de forma recursiva por párrafos, líneas,
  oraciones y palabras, en ese orden, y repite la última oración del fragmento
  anterior (solapamiento por oraciones y no por caracteres sueltos).
- "fijo": ventanas de `tamano` caracteres, sin mirar la estructura. Es la línea
  de base barata, para medir cuánto aporta respetar las secciones.

Cada fragmento lleva un prefijo con el documento y la sección (la técnica de
"Contextual Retrieval"): «los límites son 1,5» solo no dice de qué; con
«Reglas de riesgo > Límites» adelante, el embedding y el BM25 lo encuentran.

Los tamaños van en caracteres: en español, 1 token ≈ 4 caracteres.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .documentos import Documento

SEPARADORES = ["\n\n", "\n", ". ", " "]


@dataclass(frozen=True)
class ConfigFragmentado:
    nombre: str
    estrategia: Literal["estructural", "fijo"]
    tamano: int  # máximo de caracteres del cuerpo del fragmento (sin el prefijo)
    solape: int  # caracteres que se repiten del fragmento anterior


CONFIGS: dict[str, ConfigFragmentado] = {
    "estructural": ConfigFragmentado("estructural", "estructural", tamano=900, solape=120),
    "fijo": ConfigFragmentado("fijo", "fijo", tamano=300, solape=0),
}
CONFIG_POR_DEFECTO = "estructural"


@dataclass(frozen=True)
class FragmentoCrudo:
    documento: str
    indice: int  # posición dentro del documento (para recuperar los vecinos: small-to-big)
    seccion: str  # ruta de títulos, por ejemplo "Ficha de KPIs > Ventana de cálculo"
    texto: str  # con el prefijo de contexto: es lo que se indexa y lo que se le pasa al modelo
    inicio: int  # rango de caracteres en el documento limpio, para trazabilidad
    fin: int


_TITULO = re.compile(r"^(#{1,6}) +(.+?) *#*$", flags=re.MULTILINE)


def _secciones(texto: str) -> list[tuple[str, int, int]]:
    """Divide el Markdown por títulos. Devuelve (ruta de títulos, inicio, fin) por sección."""
    titulos = list(_TITULO.finditer(texto))
    secciones: list[tuple[str, int, int]] = []
    if not titulos or titulos[0].start() > 0:
        secciones.append(("", 0, titulos[0].start() if titulos else len(texto)))
    pila: list[tuple[int, str]] = []
    for i, m in enumerate(titulos):
        nivel, nombre = len(m.group(1)), m.group(2).strip()
        while pila and pila[-1][0] >= nivel:
            pila.pop()
        pila.append((nivel, nombre))
        fin = titulos[i + 1].start() if i + 1 < len(titulos) else len(texto)
        secciones.append((" > ".join(n for _, n in pila), m.start(), fin))
    return secciones


def _partir_recursivo(texto: str, tamano: int, separadores: list[str]) -> list[str]:
    """Parte un texto en piezas de hasta `tamano`, probando primero los cortes más "grandes"."""
    if len(texto) <= tamano:
        return [texto]
    if not separadores:  # sin separador posible: corte duro
        return [texto[i:i + tamano] for i in range(0, len(texto), tamano)]
    sep, resto = separadores[0], separadores[1:]
    piezas: list[str] = []
    actual = ""
    for parte in texto.split(sep):
        candidato = f"{actual}{sep}{parte}" if actual else parte
        if len(candidato) <= tamano:
            actual = candidato
            continue
        if actual:
            piezas.append(actual)
        if len(parte) > tamano:
            piezas.extend(_partir_recursivo(parte, tamano, resto))
            actual = ""
        else:
            actual = parte
    if actual:
        piezas.append(actual)
    return piezas


def _ultima_oracion(texto: str, maximo: int) -> str:
    """El final del fragmento anterior, cortado en un borde de oración o de línea."""
    if maximo <= 0:
        return ""
    cola = texto[-maximo:]
    corte = max(cola.find(". ") + 2 if ". " in cola else -1, cola.find("\n") + 1 if "\n" in cola else -1)
    return cola[corte:].strip() if corte > 0 else ""


def _prefijo(doc: Documento, seccion: str) -> str:
    return f"[{doc.titulo}" + (f" > {seccion}" if seccion and seccion != doc.titulo else "") + "]\n"


def _seccion_en(secciones: list[tuple[str, int, int]], posicion: int) -> str:
    for nombre, inicio, fin in secciones:
        if inicio <= posicion < fin:
            return nombre
    return secciones[-1][0] if secciones else ""


def fragmentar(doc: Documento, config: ConfigFragmentado) -> list[FragmentoCrudo]:
    secciones = _secciones(doc.texto)
    fragmentos: list[FragmentoCrudo] = []

    def agregar(cuerpo: str, seccion: str, inicio: int, fin: int) -> None:
        cuerpo = cuerpo.strip()
        if len(cuerpo) < 20:  # un título suelto o una línea vacía no son evidencia
            return
        fragmentos.append(FragmentoCrudo(doc.id, len(fragmentos), seccion, _prefijo(doc, seccion) + cuerpo,
                                         inicio, fin))

    if config.estrategia == "fijo":
        paso = config.tamano - config.solape
        for inicio in range(0, len(doc.texto), paso):
            fin = min(inicio + config.tamano, len(doc.texto))
            agregar(doc.texto[inicio:fin], _seccion_en(secciones, inicio), inicio, fin)
        return fragmentos

    for seccion, inicio, fin in secciones:
        texto = doc.texto[inicio:fin]
        anterior = ""
        posicion = inicio
        for pieza in _partir_recursivo(texto, config.tamano, SEPARADORES):
            solape = _ultima_oracion(anterior, config.solape)
            cuerpo = f"{solape}\n{pieza}" if solape else pieza
            desde = doc.texto.find(pieza, posicion)
            desde = desde if desde >= 0 else posicion
            agregar(cuerpo, seccion, desde, desde + len(pieza))
            posicion, anterior = desde + len(pieza), pieza
    return fragmentos
