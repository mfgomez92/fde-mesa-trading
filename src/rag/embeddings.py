"""Modelos de embeddings detrás de una misma interfaz (como los proveedores de LLM).

- `EmbedderLocal`: `paraphrase-multilingual-MiniLM-L12-v2` con fastembed (ONNX,
  sin PyTorch). Es multilingüe (el corpus y las preguntas están en español),
  corre en la CPU, es gratis, no necesita clave y pesa unos 220 MB. Se baja una
  vez y queda en caché.
- `EmbedderFalso`: una "bolsa de palabras" con hashing, determinística y sin
  descargas. Es para los tests de permisos: no prueba la calidad semántica,
  prueba que el filtro funciona.

Equivalencia TS: `Embedder` es una `interface` con dos métodos async-free.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import unicodedata
from typing import Protocol

MODELO_LOCAL = os.getenv("MODELO_EMBEDDINGS", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")


class Embedder(Protocol):
    nombre: str

    def embed_documentos(self, textos: list[str]) -> list[list[float]]: ...

    def embed_consulta(self, texto: str) -> list[float]: ...


class EmbedderLocal:
    def __init__(self, modelo: str = MODELO_LOCAL):
        from fastembed import TextEmbedding  # import local: tarda y baja el modelo la primera vez

        self.nombre = modelo
        self._modelo = TextEmbedding(modelo)

    def embed_documentos(self, textos: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._modelo.embed(textos)]

    def embed_consulta(self, texto: str) -> list[float]:
        return next(iter(self._modelo.query_embed(texto))).tolist()


def _palabras(texto: str) -> list[str]:
    sin_tildes = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", sin_tildes)


class EmbedderFalso:
    """Vector de 256 dimensiones: cada palabra suma 1 en la posición que le toca por hash."""

    nombre = "falso-hashing-256"
    dimension = 256

    def _vector(self, texto: str) -> list[float]:
        v = [0.0] * self.dimension
        for palabra in _palabras(texto):
            v[int(hashlib.md5(palabra.encode()).hexdigest(), 16) % self.dimension] += 1.0
        norma = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norma for x in v]

    def embed_documentos(self, textos: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in textos]

    def embed_consulta(self, texto: str) -> list[float]:
        return self._vector(texto)
