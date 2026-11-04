"""ingestar(): carpeta de documentos -> colección de ChromaDB con metadatos.

Un solo comando, desde bot/:  python -m src.rag ingestar

Cada fragmento se guarda con:
- `documento`, `titulo`, `seccion` y `fragmento_indice`: para citar la fuente exacta;
- `rol_operador` / `rol_lector` (booleanos) y `roles` (texto): quién puede leerlo;
- `hash_documento`, `inicio` y `fin`: si el documento cambia, se sabe qué invalidar.

La ingesta es idempotente: borra la colección y la vuelve a crear. Con el
corpus de la mesa (decenas de documentos) tarda segundos; si crece, se pasa a
una ingesta incremental por `hash_documento`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import chromadb
from chromadb.config import Settings

from .documentos import cargar_documentos
from .embeddings import Embedder, EmbedderLocal
from .fragmentado import CONFIG_POR_DEFECTO, CONFIGS, ConfigFragmentado, fragmentar
from .permisos import metadatos_de_roles

log = logging.getLogger("rag.ingesta")

RAIZ = Path(__file__).resolve().parents[2]  # carpeta bot/
CARPETA_CORPUS = RAIZ / "corpus" / "mesa"  # el corpus real (respuesta de la mesa del 02/11)
RUTA_INDICE = RAIZ / "indice"


def cliente_chroma(ruta: Path | str | None = RUTA_INDICE):
    """Cliente persistente en disco (o en memoria si `ruta` es None, para los tests)."""
    ajustes = Settings(anonymized_telemetry=False, allow_reset=True)
    if ruta is None:
        return chromadb.EphemeralClient(settings=ajustes)
    return chromadb.PersistentClient(path=str(ruta), settings=ajustes)


def nombre_coleccion(config: ConfigFragmentado) -> str:
    return f"corpus_{config.nombre}"


def ingestar(
    carpeta: Path | str = CARPETA_CORPUS,
    config: ConfigFragmentado | str = CONFIG_POR_DEFECTO,
    cliente=None,
    embedder: Embedder | None = None,
):
    """Lee, limpia, fragmenta y guarda en ChromaDB. Devuelve la colección."""
    config = CONFIGS[config] if isinstance(config, str) else config
    cliente = cliente or cliente_chroma()
    embedder = embedder or EmbedderLocal()

    documentos = cargar_documentos(Path(carpeta))
    if not documentos:
        raise ValueError(f"No hay documentos para ingestar en {carpeta}")

    nombre = nombre_coleccion(config)
    if nombre in [c.name for c in cliente.list_collections()]:
        cliente.delete_collection(nombre)
    coleccion = cliente.create_collection(
        nombre,
        embedding_function=None,  # los vectores los calculamos nosotros (Embedder)
        metadata={
            "hnsw:space": "cosine",  # similitud de coseno: 1 - distancia
            "embedder": embedder.nombre,  # la búsqueda verifica que use el mismo modelo
            "estrategia": config.estrategia,
            "tamano": config.tamano,
            "solape": config.solape,
        },
    )

    ids, textos, metadatos = [], [], []
    for doc in documentos:
        for f in fragmentar(doc, config):
            ids.append(f"{doc.id}#{f.indice:03d}")
            textos.append(f.texto)
            metadatos.append({
                "documento": doc.id,
                "titulo": doc.titulo,
                "seccion": f.seccion or doc.titulo,
                "fragmento_indice": f.indice,
                "inicio": f.inicio,
                "fin": f.fin,
                "hash_documento": doc.hash,
                **metadatos_de_roles(doc.roles),
            })

    coleccion.add(ids=ids, documents=textos, metadatas=metadatos, embeddings=embedder.embed_documentos(textos))
    log.info("Ingestados %d documentos en %d fragmentos (colección %s, %s)",
             len(documentos), len(ids), nombre, embedder.nombre)
    return coleccion
