"""RAG con permisos y búsqueda híbrida (PE3).

Las tres piezas del contrato de la consigna:
- ingestar(carpeta) -> colección de ChromaDB
- buscar(consulta, usuario, k) -> fragmentos con documento, fragmento_id, texto y puntaje
- responder(entrada, usuario) -> Salida de la PE2 + fuentes + requiere_humano
"""

from .busqueda import ResultadoBusqueda, buscar
from .ingesta import ingestar
from .permisos import Rol, Usuario
from .respuesta import Fuente, SalidaConFuentes, responder

__all__ = ["ingestar", "buscar", "responder", "ResultadoBusqueda", "SalidaConFuentes", "Fuente", "Rol", "Usuario"]
