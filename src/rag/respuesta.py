"""responder(entrada, usuario): la Entrada de la PE2 -> Salida + fuentes, solo con lo recuperado.

    Entrada ─► buscar(consulta, usuario) ─► ¿hay evidencia? ──no──► requiere_humano=true (sin llamar al LLM)
                                                │ sí
                                                ▼
                         prompt con los fragmentos (como datos) ─► LLM ─► SalidaRAG validada
                                                                              │
                     el código verifica que cada fuente citada esté entre las recuperadas;
                     si no cita ninguna válida ─► requiere_humano=true

La lección de la PE2 aplicada: lo mecánico lo hace el código. El modelo solo
redacta la respuesta con los fragmentos y dice cuáles usó; la acción
(siempre no_operar) y la confianza las pone el código.

Tres compuertas para no inventar, y ninguna depende solo del modelo:
1. Código, antes del LLM: si el mejor fragmento no llega al umbral de similitud
   (o no hay ninguno visible para el rol), no se llama al modelo.
2. Modelo: si los fragmentos no contienen la respuesta, tiene que marcar
   requiere_humano (lo dice el prompt).
3. Código, después del LLM: una respuesta sin fuentes válidas no se acepta.

El contrato de la PE2 no cambia: SalidaConFuentes HEREDA de Salida (≈ `Salida.extend({...})`
de Zod) y suma `respuesta`, `fuentes`, `requiere_humano` y `confianza_modelo`. Salida, su esquema
para el proveedor y los tests de la PE2 quedan igual.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..contratos import Accion, Clasificacion, Entrada, Salida
from ..prompts import armar_mensaje_usuario
from ..proveedores import ErrorProveedor, ProveedorLLM, esquema_para_llm
from .busqueda import ResultadoBusqueda, buscar
from .embeddings import Embedder
from .permisos import Usuario
from .prompts_rag import PLANTILLA_CORRECCION_RAG, PLANTILLA_FRAGMENTOS, PROMPT_SISTEMA_RAG, VERSION_PROMPT_RAG

log = logging.getLogger("rag.respuesta")

K_RESPUESTA = 5
# Similitud de coseno mínima del mejor fragmento para considerar que hay evidencia.
# Calibrado con las preguntas de prueba (ver datos/rag/preguntas_prueba.json y la medición).
UMBRAL_SIMILITUD = 0.40


class Fuente(BaseModel):
    documento: str
    titulo: str
    seccion: str
    fragmento_id: str


class SalidaConFuentes(Salida):
    """La Salida de la PE2 más la respuesta, las fuentes y la marca de intervención humana.

    `confianza` (heredada de Salida) la calcula el CÓDIGO con la evidencia recuperada
    (ver confianza_por_evidencia). La que declara el modelo queda en `confianza_modelo`,
    solo como registro: en la PE2 vimos que cambia entre corridas (0,45 y 0,60 para la
    misma consulta) y no sirve como compuerta.
    """

    respuesta: str | None = Field(default=None, max_length=1500)
    fuentes: list[Fuente] = Field(default_factory=list)
    requiere_humano: bool = False
    confianza_modelo: float | None = Field(default=None, ge=0, le=1)


class RespuestaModeloRAG(BaseModel):
    """Lo único que le pedimos al modelo: redactar con los fragmentos y decir cuáles usó.

    Sin acción ni precios: el RAG responde preguntas, no opera (la señal la calcula
    src/senal.py). Menos campos, menos tokens de salida y menos lugar para inventar.
    """

    model_config = ConfigDict(extra="forbid")

    respuesta: str = Field(min_length=1, max_length=1500)
    motivo: str = Field(min_length=20, max_length=600)
    fuentes: list[str] = Field(description="ids de los fragmentos usados, por ejemplo 'kpis.md#002'")
    requiere_humano: bool
    confianza: float = Field(ge=0, le=1)


ESQUEMA_SALIDA_RAG = esquema_para_llm(RespuestaModeloRAG)

# Bonus si el fragmento citado aparece en las dos ramas de la búsqueda (la vectorial y la BM25).
BONUS_ACUERDO = 0.15


def confianza_por_evidencia(citados: list[ResultadoBusqueda]) -> float:
    """Confianza calculada por el código: misma entrada, mismo número.

    Es la similitud de coseno del mejor fragmento citado, más un bonus si ese
    fragmento lo encontraron las dos búsquedas (significado y palabras exactas).
    Sin fragmentos citados válidos, 0. No es una probabilidad: es una medida de
    cuánto respaldo tiene la respuesta en los documentos.
    """
    if not citados:
        return 0.0
    puntajes = [(f.similitud or 0.0) + (BONUS_ACUERDO if f.posicion_vectorial and f.posicion_bm25 else 0.0)
                for f in citados]
    return round(min(1.0, max(puntajes)), 2)


def sin_evidencia(motivo: str) -> SalidaConFuentes:
    """Respuesta segura: no opera, no inventa y pide a una persona."""
    return SalidaConFuentes(
        clasificacion=Clasificacion.SIN_SENAL, accion=Accion.NO_OPERAR, confianza=0.0,
        motivo=motivo, respuesta=None, fuentes=[], requiere_humano=True,
    )


def hay_evidencia(fragmentos: list[ResultadoBusqueda], umbral: float = UMBRAL_SIMILITUD) -> bool:
    return bool(fragmentos) and max((f.similitud or 0.0) for f in fragmentos) >= umbral


def armar_mensaje_rag(entrada: Entrada, fragmentos: list[ResultadoBusqueda]) -> str:
    bloques = "\n".join(
        f'<fragmento id="{f.fragmento_id}" documento="{f.titulo}" seccion="{f.seccion}">\n{f.texto}\n</fragmento>'
        for f in fragmentos
    )
    return PLANTILLA_FRAGMENTOS.format(fragmentos=bloques) + "\n" + armar_mensaje_usuario(entrada)


async def _llamar(proveedores: list[ProveedorLLM], mensajes: list[dict]):
    ultimo: ErrorProveedor | None = None
    for proveedor in proveedores:
        try:
            return await proveedor.generar(PROMPT_SISTEMA_RAG, mensajes, ESQUEMA_SALIDA_RAG)
        except ErrorProveedor as e:
            log.warning("Falló %s (%s): %s", e.proveedor, e.tipo, e.mensaje)
            ultimo = e
    raise ultimo


async def responder(
    entrada: Entrada | dict,
    usuario: Usuario,
    *,
    proveedores: list[ProveedorLLM],
    coleccion=None,
    embedder: Embedder | None = None,
    k: int = K_RESPUESTA,
    umbral: float = UMBRAL_SIMILITUD,
) -> SalidaConFuentes:
    entrada = entrada if isinstance(entrada, Entrada) else Entrada.model_validate(entrada)

    # 1. Recuperar SOLO lo que el rol puede leer (el filtro va en la consulta a la base).
    fragmentos = buscar(entrada.consulta, usuario, k, coleccion=coleccion, embedder=embedder)
    if not hay_evidencia(fragmentos, umbral):
        mejor = max((f.similitud or 0.0 for f in fragmentos), default=0.0)
        log.info("Sin evidencia (mejor similitud %.2f < %.2f): no se llama al modelo", mejor, umbral)
        return sin_evidencia("No hay evidencia suficiente en los documentos que este usuario puede leer. "
                             "La consulta queda para una persona.")

    # 2. Prompt con los fragmentos recuperados y nada más.
    mensajes = [{"role": "user", "content": armar_mensaje_rag(entrada, fragmentos)}]
    salida: RespuestaModeloRAG | None = None
    for intento in (1, 2):  # un reintento si la salida no cumple el contrato, como en la PE2
        try:
            respuesta = await _llamar(proveedores, mensajes)
        except ErrorProveedor as e:
            return sin_evidencia(f"No se pudo consultar al modelo ({e.proveedor}/{e.tipo}). Queda para una persona.")
        try:
            salida = RespuestaModeloRAG.model_validate_json(respuesta.texto)
            break
        except ValidationError as e:
            errores = "\n".join(f"- {'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors(include_input=False))
            log.warning("Intento %d: salida inválida\n%s", intento, errores)
            mensajes += [{"role": "assistant", "content": respuesta.texto},
                         {"role": "user", "content": PLANTILLA_CORRECCION_RAG.format(errores=errores)}]
    if salida is None:
        return sin_evidencia("El modelo no devolvió una respuesta válida. Queda para una persona.")

    # 3. El código verifica las citas (solo valen ids recuperados) y calcula la confianza.
    recuperados = {f.fragmento_id: f for f in fragmentos}
    citados = [recuperados[i] for i in dict.fromkeys(salida.fuentes) if i in recuperados]
    inventadas = [i for i in salida.fuentes if i not in recuperados]
    if inventadas:
        log.warning("El modelo citó fragmentos que no recibió: %s", inventadas)

    requiere_humano = salida.requiere_humano or not citados
    return SalidaConFuentes(
        # El RAG responde, no opera: la acción la fija el código, no el modelo.
        clasificacion=Clasificacion.SIN_SENAL, accion=Accion.NO_OPERAR,
        confianza=0.0 if requiere_humano else confianza_por_evidencia(citados),
        confianza_modelo=salida.confianza,
        motivo=salida.motivo, respuesta=salida.respuesta,
        fuentes=[Fuente(documento=f.documento, titulo=f.titulo, seccion=f.seccion, fragmento_id=f.fragmento_id)
                 for f in citados],
        requiere_humano=requiere_humano,
    )


__all__ = ["Fuente", "SalidaConFuentes", "responder", "hay_evidencia", "VERSION_PROMPT_RAG"]
