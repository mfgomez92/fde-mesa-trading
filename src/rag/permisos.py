"""Roles y filtro de permisos del RAG.

Los roles salen de la PE1: el Operador de la mesa (aprueba, cambia reglas, ve
órdenes) y el Lector del equipo (ve el reporte agregado, sin poder actuar).

La regla de oro (consigna de la PE3): el filtro por rol va EN LA CONSULTA A LA
BASE (el `where` de ChromaDB), no después de recuperar. Así un fragmento que el
usuario no puede leer nunca sale de la base: no llega a Python, ni al ranking,
ni al prompt. Nunca le pedimos al modelo que "no muestre" algo.

Equivalencia TS: `Rol` ≈ `z.enum(["operador", "lector"])`, y `filtro_chroma`
devuelve el objeto que irá en el `where`, como un filtro de Prisma o de Mongo.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Rol(str, Enum):
    OPERADOR = "operador"
    LECTOR = "lector"


# Si un documento no figura en permisos.json, lo puede leer solo este rol:
# un olvido al cargar un documento nuevo falla del lado seguro.
ROLES_POR_DEFECTO: tuple[Rol, ...] = (Rol.OPERADOR,)


class Usuario(BaseModel):
    """Quién consulta. En la PE7 (OAuth2/SSO) el rol vendrá del proveedor de identidad."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, examples=["operador-1"])
    rol: Rol


def clave_rol(rol: Rol) -> str:
    """Nombre del metadato booleano de cada rol en el fragmento (por ejemplo, `rol_lector`).

    Un booleano por rol, y no una lista, porque el `where` de ChromaDB filtra
    igualdades sobre escalares en todas sus versiones.
    """
    return f"rol_{rol.value}"


def metadatos_de_roles(roles: list[Rol] | tuple[Rol, ...]) -> dict[str, bool | str]:
    """Lo que se guarda en cada fragmento: un booleano por rol y la lista legible."""
    metadatos: dict[str, bool | str] = {clave_rol(r): r in roles for r in Rol}
    metadatos["roles"] = ",".join(sorted(r.value for r in roles))
    return metadatos


def filtro_chroma(usuario: Usuario) -> dict:
    """El `where` que va en TODA consulta a la colección: solo lo que el rol puede leer."""
    return {clave_rol(usuario.rol): True}
