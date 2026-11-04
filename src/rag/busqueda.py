"""buscar(consulta, usuario, k): búsqueda híbrida (vectorial + BM25) con permisos.

    consulta ─┬─► embedding ─► ChromaDB.query(where=rol) ─► ranking vectorial ─┐
              │                                                              ├─► RRF ─► top k
              └─► palabras ──► ChromaDB.get(where=rol) ──► BM25 ─► ranking ──┘

El filtro por rol (`where`) va en LAS DOS consultas a la base: la vectorial y la
que trae los fragmentos para el BM25. El índice BM25 se arma solo con lo que el
usuario puede leer; un fragmento restringido no existe para esa búsqueda.

Por qué híbrida: la vectorial entiende sinónimos ("límite de pérdida" ≈ "stop")
pero falla con códigos exactos (`reglas-v0.2`, `ALPACA_LIVE_CONFIRMADO`), porque
el embedding de un código se parece al de cualquier otro código. BM25 es al revés.
RRF (Reciprocal Rank Fusion) suma 1 / (60 + posición) de cada lista: no necesita
normalizar puntajes que están en escalas distintas (coseno de 0 a 1, BM25 sin techo).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel
from rank_bm25 import BM25Okapi

from .embeddings import Embedder, EmbedderLocal
from .fragmentado import CONFIG_POR_DEFECTO, CONFIGS
from .ingesta import cliente_chroma, nombre_coleccion
from .permisos import Usuario, filtro_chroma

K_RRF = 60  # la constante estándar de RRF
CANDIDATOS = 20  # cuántos trae cada rama antes de fusionar

Modo = Literal["hibrida", "vectorial", "bm25"]

# Palabras vacías: no aportan al BM25 y en un corpus chico le roban peso a las que sí.
STOPWORDS = set("""
a al algo como con cual cuales cuando de del desde donde el ella ellas ellos en entre es esa ese eso esta este
esto fue ha hay la las le les lo los mas me mi mis muy no nos o para pero por que se si sin sobre son su sus
tambien te tiene un una uno unos unas y ya qué cuál cuáles cómo dónde cuándo
""".split())


class ResultadoBusqueda(BaseModel):
    documento: str
    fragmento_id: str
    texto: str
    puntaje: float  # el de la lista final: RRF en la híbrida, coseno en la vectorial, BM25 en la léxica
    titulo: str
    seccion: str
    roles: str
    similitud: float | None = None  # coseno con la consulta (para decidir si hay evidencia)
    puntaje_bm25: float | None = None
    posicion_vectorial: int | None = None
    posicion_bm25: int | None = None


def tokenizar(texto: str) -> list[str]:
    """Minúsculas y sin tildes. Un código como `reglas-v0.2` queda entero y también en partes."""
    texto = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    tokens: list[str] = []
    for m in re.finditer(r"[a-z0-9]+(?:[._\-][a-z0-9]+)*", texto):
        palabra = m.group(0)
        partes = re.split(r"[._\-]", palabra)
        tokens.extend(t for t in ([palabra] + partes if len(partes) > 1 else [palabra]) if t not in STOPWORDS)
    return tokens


_embedder_por_defecto: Embedder | None = None


def _embedder_defecto() -> Embedder:
    global _embedder_por_defecto
    if _embedder_por_defecto is None:
        _embedder_por_defecto = EmbedderLocal()
    return _embedder_por_defecto


def coleccion_por_defecto(config: str = CONFIG_POR_DEFECTO):
    return cliente_chroma().get_collection(nombre_coleccion(CONFIGS[config]))


def _resultado(fid: str, texto: str, meta: dict) -> ResultadoBusqueda:
    return ResultadoBusqueda(documento=meta["documento"], fragmento_id=fid, texto=texto, puntaje=0.0,
                             titulo=meta["titulo"], seccion=meta["seccion"], roles=meta["roles"])


def _rama_vectorial(consulta, filtro, coleccion, embedder, n) -> list[ResultadoBusqueda]:
    r = coleccion.query(query_embeddings=[embedder.embed_consulta(consulta)], n_results=n, where=filtro,
                        include=["documents", "metadatas", "distances"])
    salida = []
    for pos, (fid, texto, meta, dist) in enumerate(
            zip(r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0]), start=1):
        res = _resultado(fid, texto, meta)
        res.similitud = round(1 - dist, 4)
        res.posicion_vectorial = pos
        res.puntaje = res.similitud
        salida.append(res)
    return salida


def _rama_bm25(consulta, filtro, coleccion, n) -> list[ResultadoBusqueda]:
    visibles = coleccion.get(where=filtro, include=["documents", "metadatas"])
    if not visibles["ids"]:
        return []
    bm25 = BM25Okapi([tokenizar(t) for t in visibles["documents"]])
    puntajes = bm25.get_scores(tokenizar(consulta))
    orden = sorted(range(len(puntajes)), key=lambda i: puntajes[i], reverse=True)
    salida = []
    for pos, i in enumerate((i for i in orden if puntajes[i] > 0), start=1):
        if pos > n:
            break
        res = _resultado(visibles["ids"][i], visibles["documents"][i], visibles["metadatas"][i])
        res.puntaje_bm25 = round(float(puntajes[i]), 4)
        res.posicion_bm25 = pos
        res.puntaje = res.puntaje_bm25
        salida.append(res)
    return salida


def fusionar_rrf(*listas: list[ResultadoBusqueda], k_rrf: int = K_RRF) -> list[ResultadoBusqueda]:
    """Reciprocal Rank Fusion: puntaje = Σ 1 / (k_rrf + posición) en cada lista donde aparece."""
    fusion: dict[str, ResultadoBusqueda] = {}
    for lista in listas:
        for pos, res in enumerate(lista, start=1):
            actual = fusion.setdefault(res.fragmento_id, res.model_copy(update={"puntaje": 0.0}))
            actual.puntaje += 1 / (k_rrf + pos)
            # conserva los datos de cada rama (similitud, BM25 y posiciones)
            for campo in ("similitud", "puntaje_bm25", "posicion_vectorial", "posicion_bm25"):
                if getattr(res, campo) is not None:
                    setattr(actual, campo, getattr(res, campo))
    for res in fusion.values():
        res.puntaje = round(res.puntaje, 6)
    return sorted(fusion.values(), key=lambda r: r.puntaje, reverse=True)


def buscar(
    consulta: str,
    usuario: Usuario,
    k: int = 5,
    *,
    coleccion=None,
    embedder: Embedder | None = None,
    modo: Modo = "hibrida",
    candidatos: int = CANDIDATOS,
    documentos: list[str] | None = None,
) -> list[ResultadoBusqueda]:
    """Devuelve hasta k fragmentos que el usuario puede leer, del más al menos relevante.

    `documentos` restringe la búsqueda a esos documentos (rutas del corpus). Se suma al filtro por rol:
    nunca lo reemplaza.
    """
    coleccion = coleccion if coleccion is not None else coleccion_por_defecto()
    filtro = filtro_chroma(usuario)  # SIEMPRE: no hay camino de búsqueda sin filtro
    if documentos:
        filtro = {"$and": [filtro, {"documento": {"$in": list(documentos)}}]}

    vectorial: list[ResultadoBusqueda] = []
    if modo in ("hibrida", "vectorial"):
        embedder = embedder or _embedder_defecto()
        modelo_indice = (coleccion.metadata or {}).get("embedder")
        if modelo_indice != embedder.nombre:
            raise ValueError(f"La colección se armó con {modelo_indice} y la consulta usa {embedder.nombre}: "
                             "los vectores no son comparables. Volvé a ingestar.")
        vectorial = _rama_vectorial(consulta, filtro, coleccion, embedder, candidatos)
    lexica = _rama_bm25(consulta, filtro, coleccion, candidatos) if modo in ("hibrida", "bm25") else []

    if modo == "vectorial":
        return vectorial[:k]
    if modo == "bm25":
        return lexica[:k]
    return fusionar_rrf(vectorial, lexica)[:k]
