"""Medición top-k: ¿el documento correcto aparece entre los primeros k resultados?

Corre las 10 preguntas de prueba con cada configuración de fragmentado
(estructural y fijo) y cada modo de búsqueda (híbrida y solo vectorial):

- normal: Recall@k, el nombre de la clase. Acierta si el documento esperado está en
  el top k; se calcula SOLO sobre las preguntas que tienen respuesta. También se
  informa el MRR (1 / posición del primer fragmento del documento correcto).
- sin_respuesta: acá se informa la compuerta de evidencia del código (similitud
  mínima), pero la validación que cuenta es con el LLM (`medir_con_llm`): si el
  sistema se abstiene o no.
- restringida: acierta si el Lector no recibe NINGÚN fragmento del documento
  restringido, ni pidiendo todos los resultados. Aparte se informa si el
  Operador sí lo encuentra en el top k (el documento existe; lo oculta el permiso).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .busqueda import buscar
from .embeddings import Embedder
from .fragmentado import CONFIGS
from .ingesta import ingestar
from .permisos import Rol, Usuario
from .respuesta import UMBRAL_SIMILITUD, hay_evidencia

RAIZ = Path(__file__).resolve().parents[2]
PREGUNTAS = RAIZ / "datos" / "rag" / "preguntas_prueba.json"
CONSULTAS_HIBRIDA = RAIZ / "datos" / "rag" / "consultas_hibrida.json"
TODOS = 1000  # "pedir todo": para probar que un restringido no aparece en ninguna posición
COLECCIONES: dict = {}  # la última colección ingestada por configuración (para el registro por consulta)


@dataclass
class FilaMedicion:
    id: str
    tipo: str
    acierto: bool
    posicion: int | None  # del primer fragmento del documento esperado (normal) o None
    detalle: str
    operador_encuentra: bool | None = None  # solo en las restringidas


@dataclass
class ResultadoConfig:
    config: str
    modo: str
    fragmentos: int
    filas: list[FilaMedicion] = field(default_factory=list)

    def aciertos(self, tipo: str) -> tuple[int, int]:
        filas = [f for f in self.filas if f.tipo == tipo]
        return sum(f.acierto for f in filas), len(filas)

    @property
    def mrr(self) -> float:
        normales = [f for f in self.filas if f.tipo == "normal"]
        return sum(1 / f.posicion for f in normales if f.posicion) / max(len(normales), 1)


def _posicion(documento: str, resultados) -> int | None:
    return next((i for i, r in enumerate(resultados, start=1) if r.documento == documento), None)


def medir_coleccion(coleccion, preguntas: list[dict], modo: str, k: int, embedder: Embedder) -> list[FilaMedicion]:
    filas = []
    for p in preguntas:
        usuario = Usuario(id=f"prueba-{p['rol']}", rol=Rol(p["rol"]))
        res = buscar(p["pregunta"], usuario, k, coleccion=coleccion, embedder=embedder, modo=modo)
        if p["tipo"] == "normal":
            pos = _posicion(p["documento_esperado"], res)
            filas.append(FilaMedicion(p["id"], "normal", pos is not None and pos <= k, pos,
                                      f"top-1: {res[0].documento if res else '—'}"))
        elif p["tipo"] == "sin_respuesta":
            mejor = max((r.similitud or 0 for r in buscar(p["pregunta"], usuario, k, coleccion=coleccion,
                                                          embedder=embedder, modo="vectorial")), default=0)
            filas.append(FilaMedicion(p["id"], "sin_respuesta", not hay_evidencia(
                buscar(p["pregunta"], usuario, k, coleccion=coleccion, embedder=embedder)), None,
                f"mejor similitud {mejor:.2f} (umbral {UMBRAL_SIMILITUD})"))
        else:  # restringida
            todos = buscar(p["pregunta"], usuario, TODOS, coleccion=coleccion, embedder=embedder, modo=modo,
                           candidatos=TODOS)
            filtrados = [r for r in todos if r.documento == p["documento_esperado"]]
            operador = Usuario(id="prueba-operador", rol=Rol.OPERADOR)
            pos_op = _posicion(p["documento_esperado"], buscar(p["pregunta"], operador, k, coleccion=coleccion,
                                                                embedder=embedder, modo=modo))
            filas.append(FilaMedicion(p["id"], "restringida", not filtrados, pos_op,
                                      f"Lector: {len(filtrados)} de {len(todos)} fragmentos del restringido; "
                                      f"Operador: posición {pos_op or '—'}", operador_encuentra=pos_op is not None))
    return filas


def medir(cliente, embedder: Embedder, carpeta: Path, k: int = 3, ruta_preguntas: Path = PREGUNTAS,
          configs: tuple[str, ...] = ("estructural", "fijo"), modos: tuple[str, ...] = ("hibrida", "vectorial"),
          ) -> list[ResultadoConfig]:
    preguntas = json.loads(Path(ruta_preguntas).read_text(encoding="utf-8"))["preguntas"]
    resultados = []
    for nombre in configs:
        coleccion = ingestar(carpeta, CONFIGS[nombre], cliente=cliente, embedder=embedder)
        COLECCIONES[nombre] = coleccion
        for modo in modos:
            r = ResultadoConfig(nombre, modo, coleccion.count())
            r.filas = medir_coleccion(coleccion, preguntas, modo, k, embedder)
            resultados.append(r)
    return resultados


def _posicion_literal(codigo: str, resultados) -> int | None:
    """Posición del primer fragmento que contiene el código tal cual (sin distinguir mayúsculas)."""
    return next((i for i, r in enumerate(resultados, start=1) if codigo.lower() in r.texto.lower()), None)


def comparar_hibrida(coleccion, embedder: Embedder, ruta: Path = CONSULTAS_HIBRIDA, k: int = 3) -> list[dict]:
    """Consultas con un código exacto: ¿algún fragmento del top k contiene ese código literal?

    Se mide por el texto y no por un documento esperado, porque un mismo código (una
    enmienda, un id de orden) suele aparecer en varios documentos. Es verificable: el
    fragmento tiene el código o no lo tiene.
    """
    filas = []
    for c in json.loads(Path(ruta).read_text(encoding="utf-8"))["consultas"]:
        usuario = Usuario(id=f"prueba-{c['rol']}", rol=Rol(c["rol"]))
        fila = dict(c)
        for modo in ("vectorial", "bm25", "hibrida"):
            res = buscar(c["consulta"], usuario, k, coleccion=coleccion, embedder=embedder, modo=modo)
            fila[modo] = {"top1": res[0].fragmento_id if res else None,
                          "posicion": _posicion_literal(c["consulta"], res)}
        filas.append(fila)
    return filas


def tabla_markdown(resultados: list[ResultadoConfig], k: int) -> str:
    lineas = [f"| Configuración | Modo | Fragmentos | Recall@{k} (6 con respuesta) | MRR | Sin respuesta frenadas por la compuerta | "
              f"Restringidas: el Lector no recibe nada | Restringidas: el Operador las encuentra en top {k} |",
              "|---|---|---|---|---|---|---|---|"]
    for r in resultados:
        (n, tn), (s, ts), (x, tx) = r.aciertos("normal"), r.aciertos("sin_respuesta"), r.aciertos("restringida")
        op = sum(bool(f.operador_encuentra) for f in r.filas if f.tipo == "restringida")
        lineas.append(f"| {r.config} | {r.modo} | {r.fragmentos} | {n}/{tn} | {r.mrr:.2f} | {s}/{ts} | {x}/{tx} "
                      f"| {op}/{tx} |")
    return "\n".join(lineas)


def ganadora(resultados: list[ResultadoConfig]) -> ResultadoConfig:
    """La que más aciertos normales tiene en modo híbrido; si empatan, la de mejor MRR."""
    hibridas = [r for r in resultados if r.modo == "hibrida"]
    return max(hibridas, key=lambda r: (r.aciertos("normal")[0], round(r.mrr, 4)))


async def medir_con_llm(coleccion, preguntas: list[dict], proveedores, embedder: Embedder, k: int = 5) -> list[dict]:
    """Las 10 preguntas con el LLM real (como el ejemplo de la clase).

    - normal: acierta si responde (requiere_humano=false) y cita el documento esperado.
    - sin_respuesta: acierta si el sistema se abstiene (requiere_humano=true, sin fuentes).
    - restringida (rol Lector): acierta si en el PROMPT que recibió el modelo no hay ningún
      fragmento del documento restringido y si las fuentes de la respuesta tampoco lo citan.
    """
    from ..proveedores import RespuestaLLM
    from .respuesta import responder

    class Espia:
        """Envuelve al proveedor real y guarda el prompt que se le mandó."""

        def __init__(self, real):
            self.real, self.nombre, self.modelo, self.prompts = real, real.nombre, real.modelo, []

        async def generar(self, sistema, mensajes, esquema) -> RespuestaLLM:
            self.prompts.append(json.dumps(mensajes, ensure_ascii=False))
            return await self.real.generar(sistema, mensajes, esquema)

    base = json.loads((RAIZ / "datos" / "consultas_demo.json").read_text(encoding="utf-8"))
    base = (base["consultas"] if isinstance(base, dict) else base)[0]
    filas = []
    for i, p in enumerate(preguntas):
        espias = [Espia(pr) for pr in proveedores]
        usuario = Usuario(id=f"prueba-{p['rol']}", rol=Rol(p["rol"]))
        entrada = {**base, "id_propuesta": f"PRP-20261103-{9100 + i:04d}", "consulta": p["pregunta"]}
        salida = await responder(entrada, usuario, proveedores=espias, coleccion=coleccion, embedder=embedder, k=k)
        prompt = "".join(e.prompts[0] for e in espias if e.prompts)
        fila = {"id": p["id"], "tipo": p["tipo"], "rol": p["rol"], "pregunta": p["pregunta"],
                "requiere_humano": salida.requiere_humano, "respuesta": salida.respuesta,
                "fuentes": [f.fragmento_id for f in salida.fuentes], "se_llamo_al_modelo": bool(prompt)}
        if p["tipo"] == "normal":
            fila["acierto"] = not salida.requiere_humano and any(
                f.documento == p["documento_esperado"] for f in salida.fuentes)
        elif p["tipo"] == "sin_respuesta":
            fila["acierto"] = salida.requiere_humano and not salida.fuentes
        else:
            restringido = p["documento_esperado"]
            fila["restringido_en_prompt"] = f'"{restringido}#' in prompt or f"{restringido}#" in prompt
            fila["restringido_en_fuentes"] = any(f.documento == restringido for f in salida.fuentes)
            fila["acierto"] = not fila["restringido_en_prompt"] and not fila["restringido_en_fuentes"]
        filas.append(fila)
    return filas


def registro_por_consulta(resultados: list[ResultadoConfig], coleccion_por_config: dict, preguntas: list[dict],
                          embedder: Embedder, k: int) -> list[dict]:
    """Lo que pide la clase: por cada consulta, el documento esperado y los resultados observados."""
    registro = []
    for r in resultados:
        coleccion = coleccion_por_config[r.config]
        for p in preguntas:
            usuario = Usuario(id=f"prueba-{p['rol']}", rol=Rol(p["rol"]))
            res = buscar(p["pregunta"], usuario, k, coleccion=coleccion, embedder=embedder, modo=r.modo)
            fila = next(f for f in r.filas if f.id == p["id"])
            registro.append({"config": r.config, "modo": r.modo, "id": p["id"], "tipo": p["tipo"], "rol": p["rol"],
                             "documento_esperado": p["documento_esperado"],
                             "observados": [x.fragmento_id for x in res], "acierto": fila.acierto})
    return registro
