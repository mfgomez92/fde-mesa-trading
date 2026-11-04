"""Motor LLM base: consulta -> modelo -> salida validada -> decisión de riesgo -> bitácora.

Uso (desde la carpeta bot/, con el entorno virtual activado):
    python -m src.motor                 # corre las 5 consultas de datos/consultas_demo.json
    python -m src.motor --simulado      # lo mismo, con un LLM falso (no necesita claves)
    python -m src.motor --demo-errores  # entrada inválida, reintento y error estructurado
    python -m src.motor --senal-en-codigo  # la señal la calcula el código; el LLM confirma o veta (con el RAG)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from .bitacora import Bitacora, CategoriaMotivo, Etapa, RegistroBitacora, kpi_tecnico
from .broker import ErrorConfiguracion, modo_paper
from .contratos import Accion, CampoFallido, Entrada, ErrorEstructurado, Salida, TipoVeredicto, Veredicto
from .prompts import PLANTILLA_CORRECCION, PROMPT_SISTEMA, VERSION_PROMPT, armar_mensaje_usuario
from .prompts_veto import PLANTILLA_VETO, PROMPT_SISTEMA_VETO, VERSION_PROMPT_VETO
from .proveedores import (
    ErrorProveedor,
    ProveedorAnthropic,
    ProveedorConErrorInyectado,
    ProveedorLLM,
    ProveedorOpenAI,
    ProveedorSimulado,
    esquema_para_llm,
)
from .riesgo import Blackouts, DecisionRiesgo, EstadoDia, ReglasRiesgo, ResultadoRiesgo, evaluar
from .senal import ParametrosEstrategia, calcular_senal

log = logging.getLogger("motor")

RAIZ = Path(__file__).resolve().parent.parent  # carpeta bot/
ESQUEMA_SALIDA = esquema_para_llm(Salida)
ESQUEMA_VETO = esquema_para_llm(Veredicto)

# Qué pasa al final con la propuesta, según lo que decidió el código de riesgo.
DECISION_FINAL = {
    DecisionRiesgo.APROBADA_AUTO: ("orden_simulada", CategoriaMotivo.NINGUNO),  # en la PE2 no se envía al bróker
    DecisionRiesgo.RECHAZADA: ("descartada", CategoriaMotivo.RIESGO),
    DecisionRiesgo.SIN_OPERACION: ("no_operar", CategoriaMotivo.NINGUNO),
    # REQUIERE_APROBACION no tiene decisión final hasta que decide una persona (M5).
}


class ResultadoMotor(BaseModel):
    ok: bool
    id_propuesta: str
    salida: Salida | None = None
    riesgo: ResultadoRiesgo | None = None
    error: ErrorEstructurado | None = None
    intentos: int = 0
    proveedor: str | None = None
    modelo: str | None = None
    tokens_entrada: int = 0
    tokens_salida: int = 0
    costo_usd: float = 0.0
    latencia_ms: int = 0
    # Solo en procesar_con_senal (la señal la calcula el código y el LLM confirma o veta):
    veredicto: Veredicto | None = None
    decision_final: str | None = None
    codigo_final: str | None = None


def campos_fallidos(error: ValidationError) -> list[CampoFallido]:
    """Traduce un ValidationError a una lista legible: qué campo, qué pasó."""
    return [
        CampoFallido(
            campo=" -> ".join(str(parte) for parte in e["loc"]) or "(objeto completo)",
            mensaje=e["msg"],
            tipo=e["type"],
        )
        for e in error.errors(include_input=False)
    ]


class Motor:
    def __init__(
        self,
        proveedores: list[ProveedorLLM],
        bitacora: Bitacora,
        reglas: ReglasRiesgo | None = None,
        presupuesto_tokens: int = 12_000,
        max_reintentos: int = 1,
        estrategia: ParametrosEstrategia | None = None,
        veto_obligatorio: bool = True,
        timeout_veto_s: float = 20.0,
    ):
        if not proveedores:
            raise ValueError("Hace falta al menos un proveedor")
        self.proveedores = proveedores  # en orden: el primero es el principal, el resto, fallback
        self.bitacora = bitacora
        self.reglas = reglas or ReglasRiesgo()
        self.presupuesto_tokens = presupuesto_tokens
        self.max_reintentos = max_reintentos
        self.estrategia = estrategia or ParametrosEstrategia()
        # Si el LLM no responde: True (desde el 02/11, pedido de la mesa) = la señal se descarta y
        # se registra (falla cerrada); False = sigue sin veto, como en la PE2.
        self.veto_obligatorio = veto_obligatorio
        self.timeout_veto_s = timeout_veto_s  # pedido de la mesa (02/11): 20 s y falla cerrada

    def _registrar(self, id_propuesta: str, etapa: Etapa, decision: str, autor: str,
                   version_prompt: str = VERSION_PROMPT, **extra) -> None:
        self.bitacora.registrar(
            RegistroBitacora(
                id_propuesta=id_propuesta,
                etapa=etapa,
                decision=decision,
                autor=autor,
                version_reglas=self.reglas.version,
                version_prompt=version_prompt,
                **extra,
            )
        )

    async def _llamar(self, mensajes: list[dict], sistema: str = PROMPT_SISTEMA, esquema: dict = ESQUEMA_SALIDA):
        """Prueba los proveedores en orden. Si uno falla por la API, pasa al siguiente."""
        ultimo_error: ErrorProveedor | None = None
        for proveedor in self.proveedores:
            try:
                return await proveedor.generar(sistema, mensajes, esquema)
            except ErrorProveedor as e:
                log.warning("Falló %s (%s): %s", e.proveedor, e.tipo, e.mensaje)
                ultimo_error = e
        raise ultimo_error

    async def procesar(self, datos: dict) -> ResultadoMotor:
        inicio = time.perf_counter()
        id_propuesta = str(datos.get("id_propuesta", "SIN-ID"))

        # 1. Validar la entrada antes de gastar un token.
        try:
            entrada = Entrada.model_validate(datos)
        except ValidationError as e:
            error = ErrorEstructurado(
                tipo="entrada_invalida", mensaje="La consulta no cumple el contrato", campos=campos_fallidos(e)
            )
            for campo in error.campos:
                log.error("Entrada inválida en [%s]: %s", campo.campo, campo.mensaje)
            self._registrar(id_propuesta, Etapa.ERROR, "entrada_invalida", "motor",
                            categoria_motivo=CategoriaMotivo.OPERATIVO, codigo_motivo="ENTRADA_INVALIDA",
                            detalle=error.model_dump_json())
            return ResultadoMotor(ok=False, id_propuesta=id_propuesta, error=error)

        resultado = ResultadoMotor(ok=False, id_propuesta=id_propuesta)
        mensajes = [{"role": "user", "content": armar_mensaje_usuario(entrada)}]
        salida: Salida | None = None
        error: ErrorEstructurado | None = None

        # 2. Llamar al modelo y validar; si la salida es inválida, un reintento.
        for intento in range(1, self.max_reintentos + 2):
            resultado.intentos = intento
            try:
                respuesta = await self._llamar(mensajes)
            except ErrorProveedor as e:
                error = ErrorEstructurado(tipo="error_api", mensaje=f"{e.proveedor}/{e.tipo}: {e.mensaje}")
                break

            resultado.proveedor, resultado.modelo = respuesta.proveedor, respuesta.modelo
            resultado.tokens_entrada += respuesta.tokens_entrada
            resultado.tokens_salida += respuesta.tokens_salida
            resultado.costo_usd += respuesta.costo_usd

            try:
                salida = Salida.model_validate_json(respuesta.texto)
                if salida.accion == Accion.NO_OPERAR and salida.confianza != 0:
                    # Sin señal no hay nada de qué estar seguro: el modelo ponía 0,95 o 0,0 según la
                    # consulta (PE6). El código la fija en 0 para que el campo signifique una sola cosa.
                    log.info("no_operar con confianza %.2f del modelo: se registra 0", salida.confianza)
                    salida = salida.model_copy(update={"confianza": 0.0})
                error = None
                break
            except ValidationError as e:
                campos = campos_fallidos(e)
                for campo in campos:
                    log.warning("Intento %d: salida inválida en [%s]: %s", intento, campo.campo, campo.mensaje)
                error = ErrorEstructurado(
                    tipo="salida_invalida",
                    mensaje=f"La salida no pasó la validación después de {intento} intento(s)",
                    campos=campos,
                )

            usados = resultado.tokens_entrada + resultado.tokens_salida
            if usados >= self.presupuesto_tokens:
                error = ErrorEstructurado(
                    tipo="presupuesto_agotado",
                    mensaje=f"Se usaron {usados} tokens; el presupuesto del ciclo es {self.presupuesto_tokens}",
                    campos=error.campos,
                )
                break
            if intento <= self.max_reintentos:
                log.info("Reintento: le devuelvo al modelo los campos que fallaron")
                errores = "\n".join(f"- {c.campo}: {c.mensaje}" for c in error.campos)
                mensajes += [
                    {"role": "assistant", "content": respuesta.texto},
                    {"role": "user", "content": PLANTILLA_CORRECCION.format(errores=errores)},
                ]

        resultado.costo_usd = round(resultado.costo_usd, 6)
        resultado.latencia_ms = round((time.perf_counter() - inicio) * 1000)
        medicion = dict(
            proveedor=resultado.proveedor, modelo=resultado.modelo, intentos=resultado.intentos,
            tokens_entrada=resultado.tokens_entrada, tokens_salida=resultado.tokens_salida,
            costo_usd=resultado.costo_usd, latencia_ms=resultado.latencia_ms,
        )
        self._registrar(id_propuesta, Etapa.PROPUESTA, salida.accion.value if salida else "sin_salida",
                        "motor", detalle=salida.model_dump_json() if salida else "", **medicion)

        # 3. Si no hubo salida válida: error estructurado, y la propuesta se descarta por causa operativa.
        if salida is None:
            log.error("Error estructurado: %s", error.model_dump_json())
            self._registrar(id_propuesta, Etapa.DECISION_RIESGO, "no_evaluada", "codigo-riesgo",
                            categoria_motivo=CategoriaMotivo.OPERATIVO, codigo_motivo=error.tipo.upper())
            self._registrar(id_propuesta, Etapa.DECISION_FINAL, "descartada", "motor",
                            categoria_motivo=CategoriaMotivo.OPERATIVO, codigo_motivo=error.tipo.upper(),
                            detalle=error.mensaje)
            resultado.error = error
            return resultado

        # 4. El código, no el modelo, decide el riesgo.
        riesgo = evaluar(entrada, salida, self.reglas)
        categoria = CategoriaMotivo.RIESGO if riesgo.decision == DecisionRiesgo.RECHAZADA else CategoriaMotivo.NINGUNO
        self._registrar(id_propuesta, Etapa.DECISION_RIESGO, riesgo.decision.value, "codigo-riesgo",
                        categoria_motivo=categoria, codigo_motivo=riesgo.codigo_motivo, detalle=riesgo.detalle)
        if riesgo.decision in DECISION_FINAL:
            decision_final, categoria_final = DECISION_FINAL[riesgo.decision]
            self._registrar(id_propuesta, Etapa.DECISION_FINAL, decision_final, "motor",
                            categoria_motivo=categoria_final, codigo_motivo=riesgo.codigo_motivo)

        resultado.ok, resultado.salida, resultado.riesgo = True, salida, riesgo
        return resultado

    async def procesar_con_senal(self, datos: dict, contexto=None, estado: EstadoDia | None = None,
                                 blackouts: Blackouts | None = None, ejecutor=None) -> ResultadoMotor:
        """La señal la calcula el código; el LLM solo confirma o veta (lección de la PE2).

        `contexto(entrada, salida)` es opcional y devuelve fragmentos del RAG (mandato y manuales)
        para que el veto tenga fundamento. Etapas de la bitácora: propuesta (autor codigo-senal),
        decision_riesgo (codigo-riesgo) y decision_final (motor, llm-veto o broker).

        Operando en vivo (src/operador.py) se pasan además:
        - `estado` y `blackouts`: las reglas del día de reglas-v0.3;
        - `ejecutor(entrada, salida)`: async, manda la orden y devuelve
          (decision, categoria, codigo, detalle) para la decisión final.
        El veto (búsqueda en el RAG + LLM) tiene `timeout_veto_s` en total; si no
        responde a tiempo y el veto es obligatorio, la señal se descarta (falla cerrada).
        """
        inicio = time.perf_counter()
        id_propuesta = str(datos.get("id_propuesta", "SIN-ID"))
        version = f"{self.estrategia.version}+{VERSION_PROMPT_VETO}"
        try:
            entrada = Entrada.model_validate(datos)
        except ValidationError as e:
            error = ErrorEstructurado(tipo="entrada_invalida", mensaje="La consulta no cumple el contrato",
                                      campos=campos_fallidos(e))
            self._registrar(id_propuesta, Etapa.ERROR, "entrada_invalida", "motor", version_prompt=version,
                            categoria_motivo=CategoriaMotivo.OPERATIVO, codigo_motivo="ENTRADA_INVALIDA",
                            detalle=error.model_dump_json())
            return ResultadoMotor(ok=False, id_propuesta=id_propuesta, error=error)

        # 1. El código calcula la operación: sin tokens, siempre igual.
        senal = calcular_senal(entrada, self.estrategia)
        salida = senal.salida
        resultado = ResultadoMotor(ok=True, id_propuesta=id_propuesta, salida=salida)

        # 2. El código de riesgo la evalúa (con el estado del día, si opera en vivo).
        riesgo = evaluar(entrada, salida, self.reglas, estado, blackouts)
        resultado.riesgo = riesgo
        veredicto: Veredicto | None = None
        nota_veto, codigo_falla = "", "VETO_NO_DISPONIBLE"

        # 3. Solo si hay una operación que el riesgo no rechazó, se gasta una llamada al LLM.
        if riesgo.decision in (DecisionRiesgo.APROBADA_AUTO, DecisionRiesgo.REQUIERE_APROBACION):
            try:
                veredicto, nota_veto = await asyncio.wait_for(
                    self._veto(entrada, salida, senal.regla, riesgo, contexto, resultado), self.timeout_veto_s)
            except asyncio.TimeoutError:
                veredicto = None
                nota_veto = f"Veto sin respuesta en {self.timeout_veto_s:g} s"
                codigo_falla = "VETO_TIMEOUT"
        resultado.veredicto = veredicto
        resultado.costo_usd = round(resultado.costo_usd, 6)
        resultado.latencia_ms = round((time.perf_counter() - inicio) * 1000)

        self._registrar(id_propuesta, Etapa.PROPUESTA, salida.accion.value, "codigo-senal", version_prompt=version,
                        detalle=salida.model_dump_json(), proveedor=resultado.proveedor, modelo=resultado.modelo,
                        intentos=resultado.intentos, tokens_entrada=resultado.tokens_entrada,
                        tokens_salida=resultado.tokens_salida, costo_usd=resultado.costo_usd,
                        latencia_ms=resultado.latencia_ms)
        categoria = CategoriaMotivo.RIESGO if riesgo.decision == DecisionRiesgo.RECHAZADA else CategoriaMotivo.NINGUNO
        self._registrar(id_propuesta, Etapa.DECISION_RIESGO, riesgo.decision.value, "codigo-riesgo",
                        version_prompt=version, categoria_motivo=categoria, codigo_motivo=riesgo.codigo_motivo,
                        detalle=riesgo.detalle)

        # 4. Decisión final.
        if veredicto and veredicto.veredicto == TipoVeredicto.VETAR:
            # Un veto con motivo es una decisión, no una falla: cuenta como riesgo en el KPI de negocio.
            final, cat, codigo, autor = "vetada", CategoriaMotivo.RIESGO, "VETO_LLM", "llm-veto"
            detalle = veredicto.model_dump_json()
        elif nota_veto and self.veto_obligatorio:
            final, cat, codigo, autor, detalle = "descartada", CategoriaMotivo.OPERATIVO, codigo_falla, "motor", nota_veto
        elif riesgo.decision == DecisionRiesgo.APROBADA_AUTO and ejecutor is not None:
            final, cat, codigo, detalle = await ejecutor(entrada, salida)
            autor = "broker"
        elif riesgo.decision in DECISION_FINAL:
            final, cat = DECISION_FINAL[riesgo.decision]
            codigo, autor = riesgo.codigo_motivo, "motor"
            detalle = nota_veto or (veredicto.model_dump_json() if veredicto else "")
        else:  # requiere_aprobacion confirmada: espera a una persona (M5), sin decisión final
            final = None
        if final:
            self._registrar(id_propuesta, Etapa.DECISION_FINAL, final, autor, version_prompt=version,
                            categoria_motivo=cat, codigo_motivo=codigo, detalle=detalle)
        resultado.decision_final = final
        resultado.codigo_final = codigo if final else None
        return resultado

    async def _veto(self, entrada: Entrada, salida: Salida, regla: str, riesgo: ResultadoRiesgo, contexto,
                    resultado: ResultadoMotor) -> tuple[Veredicto | None, str]:
        """Busca en el RAG y le pide el veredicto al LLM. Devuelve (veredicto, nota si no hubo)."""
        # La búsqueda es sincrónica (embeddings + ChromaDB): va a un hilo para que cuente en el timeout.
        fragmentos = await asyncio.to_thread(contexto, entrada, salida) if contexto else []
        bloques = "\n".join(f'<fragmento id="{f.fragmento_id}" documento="{f.titulo}" seccion="{f.seccion}">\n'
                            f'{f.texto}\n</fragmento>' for f in fragmentos) or "(no hay fragmentos)"
        mensajes = [{"role": "user", "content": PLANTILLA_VETO.format(
            activo=entrada.activo, temporalidad=entrada.temporalidad.value,
            hora_vela=entrada.hora_vela.isoformat(), accion=salida.accion.value, entrada=salida.precio_entrada,
            stop=salida.stop_loss, objetivo=salida.take_profit, regla=regla,
            decision_riesgo=riesgo.decision.value, detalle_riesgo=riesgo.detalle,
            fragmentos=bloques, consulta=entrada.consulta)}]
        nota = ""
        for intento in range(1, self.max_reintentos + 2):
            resultado.intentos = intento
            try:
                respuesta = await self._llamar(mensajes, PROMPT_SISTEMA_VETO, ESQUEMA_VETO)
            except ErrorProveedor as e:
                return None, f"Veto no disponible ({e.proveedor}/{e.tipo})"
            resultado.proveedor, resultado.modelo = respuesta.proveedor, respuesta.modelo
            resultado.tokens_entrada += respuesta.tokens_entrada
            resultado.tokens_salida += respuesta.tokens_salida
            resultado.costo_usd += respuesta.costo_usd
            try:
                veredicto = Veredicto.model_validate_json(respuesta.texto)
                validos = {f.fragmento_id for f in fragmentos}
                veredicto.fuentes = [f for f in veredicto.fuentes if f in validos]  # solo citas reales
                return veredicto, ""
            except ValidationError as e:
                nota = "Veto no disponible (salida inválida)"
                if intento <= self.max_reintentos:
                    errores = "\n".join(f"- {c.campo}: {c.mensaje}" for c in campos_fallidos(e))
                    mensajes += [{"role": "assistant", "content": respuesta.texto},
                                 {"role": "user", "content": PLANTILLA_CORRECCION.format(errores=errores)}]
        return None, nota


# ---------------------------------------------------------------------------
# Línea de comandos
# ---------------------------------------------------------------------------

def proveedores_desde_entorno() -> list[ProveedorLLM]:
    """Arma la lista de proveedores según las claves que haya en el entorno (.env)."""
    disponibles: dict[str, ProveedorLLM] = {}
    # BOT_ANTHROPIC_API_KEY y no ANTHROPIC_API_KEY: la nube de Claude Code reserva ese nombre
    # para su propia autenticación y no lo pasa a la sesión.
    if clave_anthropic := os.getenv("BOT_ANTHROPIC_API_KEY"):
        disponibles["anthropic"] = ProveedorAnthropic(
            modelo=os.getenv("MODELO_ANTHROPIC", "claude-haiku-5-5"),
            api_key=clave_anthropic,
            esfuerzo=os.getenv("ESFUERZO_ANTHROPIC", "low"),
        )
    if os.getenv("OPENAI_API_KEY"):
        disponibles["openai"] = ProveedorOpenAI(modelo=os.getenv("MODELO_OPENAI", "gpt-6.1-terra"))
    principal = os.getenv("PROVEEDOR_PRINCIPAL", "anthropic")
    orden = sorted(disponibles, key=lambda nombre: nombre != principal)  # el principal primero
    return [disponibles[nombre] for nombre in orden]


def respuestas_simuladas(consultas: list[dict]) -> list[str]:
    """Una salida válida por consulta, para correr el motor sin claves."""
    respuestas = []
    palabras_de_la_mesa = ("entrada", "operar", "short", "long", "ema", "rsi", "compr", "vend", "cruce")
    for c in consultas:
        precio, atr = c["precio"], c["indicadores"]["atr"]
        if not any(p in c["consulta"].lower() for p in palabras_de_la_mesa):
            salida = dict(clasificacion="fuera_de_alcance", accion="no_operar", precio_entrada=None, stop_loss=None,
                          take_profit=None, confianza=0.9, motivo="Simulado: la consulta no tiene que ver con la mesa.")
        elif c["indicadores"]["ema_rapida"] > c["indicadores"]["ema_lenta"] and 50 <= c["indicadores"]["rsi"] <= 70:
            salida = dict(clasificacion="senal", accion="comprar", precio_entrada=precio,
                          stop_loss=round(precio - 1.5 * atr, 2), take_profit=round(precio + 3 * atr, 2),
                          confianza=0.7, motivo="Simulado: EMA 9 sobre EMA 21 y RSI en zona alcista.")
        else:
            salida = dict(clasificacion="sin_senal", accion="no_operar", precio_entrada=None, stop_loss=None,
                          take_profit=None, confianza=0.5, motivo="Simulado: los indicadores no dan una entrada clara.")
        respuestas.append(json.dumps(salida))
    return respuestas


def imprimir_tabla(consultas: list[dict], resultados: list[ResultadoMotor]) -> None:
    print("\n| # | Consulta | ¿Validó? | Tokens (ent / sal) | Costo USD | Latencia ms | Decisión de riesgo |")
    print("|---|---|---|---|---|---|---|")
    for i, (c, r) in enumerate(zip(consultas, resultados), start=1):
        decision = r.riesgo.decision.value if r.riesgo else (r.error.tipo if r.error else "-")
        validó = f"Sí ({r.intentos} int.)" if r.ok else "No"
        print(f"| {i} | {c['consulta'][:45]} | {validó} | {r.tokens_entrada} / {r.tokens_salida} "
              f"| {r.costo_usd:.6f} | {r.latencia_ms} | {decision} |")
    n = len(resultados)
    print(f"| | **Promedio** | {sum(r.ok for r in resultados)}/{n} "
          f"| {sum(r.tokens_entrada for r in resultados) / n:.0f} / {sum(r.tokens_salida for r in resultados) / n:.0f} "
          f"| {sum(r.costo_usd for r in resultados) / n:.6f} | {sum(r.latencia_ms for r in resultados) / n:.0f} | |")


async def correr_demo(motor: Motor, consultas: list[dict]) -> list[ResultadoMotor]:
    resultados = []
    for i, consulta in enumerate(consultas, start=1):
        print(f"\n=== Consulta {i}/{len(consultas)}: {consulta['consulta']}")
        resultado = await motor.procesar(consulta)
        print(resultado.model_dump_json(indent=2, exclude_none=True))
        resultados.append(resultado)
    return resultados


# El veto tiene que leer el mandato vigente. Con la búsqueda libre sola, el mandato no aparecía en
# ninguna de 5 señales de ejemplo (03/11; la PE6 midió Recall@3 de 6/10): sus fragmentos son claves de
# JSON cortas y pierden contra los manuales en prosa. Por eso se busca aparte dentro del mandato y se
# suma la búsqueda libre de siempre.
# Qué documentos son «el mandato» depende del corpus: config/veto_mandato.json. Sin ese archivo (o con
# otro corpus) el veto hace solo la búsqueda libre.
RUTA_VETO_MANDATO = RAIZ / "config" / "veto_mandato.json"


def documentos_mandato(ruta: Path = RUTA_VETO_MANDATO) -> tuple[str, ...]:
    if not ruta.is_file():
        return ()
    return tuple(json.loads(ruta.read_text(encoding="utf-8")).get("documentos", []))


DOCUMENTOS_MANDATO = documentos_mandato()
K_VETO_MANDATO = 3
K_VETO_LIBRE = 3


def combinar_contexto_veto(del_mandato: list, libres: list) -> list:
    """Primero los fragmentos del mandato, después los libres, sin repetir fragmentos."""
    vistos, salida = set(), []
    for r in [*del_mandato, *libres]:
        if r.fragmento_id not in vistos:
            vistos.add(r.fragmento_id)
            salida.append(r)
    return salida


def contexto_desde_rag():
    """Si hay índice del RAG, el veto recibe el mandato y los manuales (el bot lee como Lector)."""
    try:
        from .rag.busqueda import buscar, coleccion_por_defecto
        from .rag.permisos import Rol, Usuario

        coleccion = coleccion_por_defecto()
    except Exception as e:  # sin índice: el veto corre sin fragmentos
        log.warning("Sin índice del RAG (%s): el veto no tiene contexto. Corré python -m src.rag ingestar", e)
        return None
    if not DOCUMENTOS_MANDATO:
        log.warning("Sin %s: el veto no busca aparte en el mandato, solo hace la búsqueda libre", RUTA_VETO_MANDATO.name)
    # El veto lee como Lector (reparto de permisos de la mesa): el mandato sí, los exports del Operador no.
    bot = Usuario(id="bot-mesa", rol=Rol.LECTOR)

    def contexto(entrada: Entrada, salida: Salida):
        consulta = (f"reglas de riesgo, límites y restricciones para {salida.accion.value} {entrada.activo} "
                    f"intradía. {entrada.consulta}")
        del_mandato = (buscar(consulta, bot, K_VETO_MANDATO, coleccion=coleccion, documentos=list(DOCUMENTOS_MANDATO))
                       if DOCUMENTOS_MANDATO else [])
        return combinar_contexto_veto(del_mandato, buscar(consulta, bot, K_VETO_LIBRE, coleccion=coleccion))

    return contexto


async def correr_con_senal(motor: Motor, consultas: list[dict]) -> list[ResultadoMotor]:
    contexto = contexto_desde_rag()
    resultados = []
    print("\n| # | Consulta | Señal (código) | Riesgo (código) | Veredicto (LLM) | Final | Tokens | Costo USD | ms |")
    print("|---|---|---|---|---|---|---|---|---|")
    for i, c in enumerate(consultas, start=1):
        r = await motor.procesar_con_senal(c, contexto)
        resultados.append(r)
        accion = r.salida.accion.value if r.salida else (r.error.tipo if r.error else "-")
        print(f"| {i} | {c['consulta'][:40]} | {accion} | {r.riesgo.decision.value if r.riesgo else '-'} "
              f"| {r.veredicto.veredicto.value if r.veredicto else '(no se llamó)'} | {r.decision_final or 'espera'} "
              f"| {r.tokens_entrada}/{r.tokens_salida} | {r.costo_usd:.6f} | {r.latencia_ms} |")
        if r.veredicto:
            print(f"|   | ↳ {r.veredicto.motivo} Fuentes: {', '.join(r.veredicto.fuentes) or '—'} | | | | | | | |")
    return resultados


async def demo_errores(motor_real: Motor, consulta: dict) -> None:
    print("\n=== Error 1: entrada inválida (RSI fuera de rango e id mal formado). No se llama al modelo.")
    mala = {**consulta, "id_propuesta": "123", "indicadores": {**consulta["indicadores"], "rsi": 140}}
    print((await motor_real.procesar(mala)).model_dump_json(indent=2, exclude_none=True))

    # Respuesta con forma válida pero incoherente: compra con el stop por encima de la entrada.
    rota = json.dumps(dict(clasificacion="senal", accion="comprar", precio_entrada=consulta["precio"],
                           stop_loss=consulta["precio"] + 2, take_profit=consulta["precio"] + 4,
                           confianza=1.4, motivo="Respuesta inválida inyectada para la demo."))
    real = motor_real.proveedores[0]

    print("\n=== Error 2: la primera salida del modelo se reemplaza por una inválida. El motor reintenta.")
    motor = Motor([ProveedorConErrorInyectado(real, 1, rota)], motor_real.bitacora, motor_real.reglas)
    print((await motor.procesar({**consulta, "id_propuesta": consulta["id_propuesta"][:-1] + "8"}))
          .model_dump_json(indent=2, exclude_none=True))

    print("\n=== Error 3: las dos salidas son inválidas. El motor devuelve un error estructurado.")
    motor = Motor([ProveedorConErrorInyectado(real, 2, rota)], motor_real.bitacora, motor_real.reglas)
    print((await motor.procesar({**consulta, "id_propuesta": consulta["id_propuesta"][:-1] + "9"}))
          .model_dump_json(indent=2, exclude_none=True))


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Motor LLM base de la mesa (PE2)")
    parser.add_argument("--simulado", action="store_true", help="usa un LLM falso (no necesita claves)")
    parser.add_argument("--demo-errores", action="store_true", help="muestra los caminos de error")
    parser.add_argument("--senal-en-codigo", action="store_true",
                        help="la señal la calcula el código y el LLM solo confirma o veta, con el RAG como contexto")
    parser.add_argument("--consultas", default=str(RAIZ / "datos" / "consultas_demo.json"))
    args = parser.parse_args(argv)

    load_dotenv(RAIZ / ".env")  # ≈ require("dotenv").config() en Node
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        modo_paper()  # el candado: con ALPACA_PAPER=false sin confirmación, el bot no arranca
    except ErrorConfiguracion as e:
        print(f"No arranco: {e}", file=sys.stderr)
        return 1

    datos = json.loads(Path(args.consultas).read_text(encoding="utf-8"))
    # El archivo trae las consultas y de dónde salieron las velas (armado por scripts/armar_consultas_demo.py).
    consultas = datos["consultas"] if isinstance(datos, dict) else datos
    if isinstance(datos, dict):
        print(f"Velas: {datos['fuente']}, descargadas el {datos['descargado']}")
    if args.simulado:
        proveedores: list[ProveedorLLM] = [ProveedorSimulado(respuestas_simuladas(consultas))]
    else:
        proveedores = proveedores_desde_entorno()
        if not proveedores:
            print("No hay BOT_ANTHROPIC_API_KEY ni OPENAI_API_KEY. Completá el .env o usá --simulado.", file=sys.stderr)
            return 1
    print("Proveedores en orden:", ", ".join(f"{p.nombre}/{p.modelo}" for p in proveedores))

    bitacora = Bitacora(RAIZ / os.getenv("RUTA_BITACORA", "bitacora/bitacora.jsonl"))
    motor = Motor(proveedores, bitacora, presupuesto_tokens=int(os.getenv("PRESUPUESTO_TOKENS_CICLO", "12000")))

    if args.demo_errores:
        if args.simulado:
            print("--demo-errores necesita un proveedor real.", file=sys.stderr)
            return 1
        await demo_errores(motor, consultas[0])
        return 0

    if args.senal_en_codigo:
        await correr_con_senal(motor, consultas)
        return 0

    desde = datetime.now(timezone.utc)
    resultados = await correr_demo(motor, consultas)
    imprimir_tabla(consultas, resultados)

    kpi = kpi_tecnico(bitacora.leer(), desde, datetime.now(timezone.utc))
    print(f"\nKPI técnico en esta corrida: {kpi.valor_pct} % de propuestas con decisión de riesgo y final "
          f"registradas con hora ({kpi.numerador}/{kpi.denominador}). Meta de la PE1: ≥ 95 %. "
          f"{'Muestra suficiente.' if kpi.concluyente else f'Muestra chica: el mínimo para evaluar la meta es {kpi.muestra_minima}.'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
