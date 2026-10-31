"""Motor LLM base: consulta -> modelo -> salida validada -> decisión de riesgo -> bitácora.

Uso (desde la raíz del repo, con el entorno virtual activado):
    python -m src.motor                 # corre las 5 consultas de datos/consultas_demo.json
    python -m src.motor --simulado      # lo mismo, con un LLM falso (no necesita claves)
    python -m src.motor --demo-errores  # entrada inválida, reintento y error estructurado
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
from .contratos import CampoFallido, Entrada, ErrorEstructurado, Salida
from .prompts import PLANTILLA_CORRECCION, PROMPT_SISTEMA, VERSION_PROMPT, armar_mensaje_usuario
from .proveedores import (
    ErrorProveedor,
    ProveedorAnthropic,
    ProveedorConErrorInyectado,
    ProveedorLLM,
    ProveedorOpenAI,
    ProveedorSimulado,
    esquema_para_llm,
)
from .riesgo import DecisionRiesgo, ReglasRiesgo, ResultadoRiesgo, evaluar

log = logging.getLogger("motor")

RAIZ = Path(__file__).resolve().parent.parent  # raíz del repo
ESQUEMA_SALIDA = esquema_para_llm(Salida)

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
    ):
        if not proveedores:
            raise ValueError("Hace falta al menos un proveedor")
        self.proveedores = proveedores  # en orden: el primero es el principal, el resto, fallback
        self.bitacora = bitacora
        self.reglas = reglas or ReglasRiesgo()
        self.presupuesto_tokens = presupuesto_tokens
        self.max_reintentos = max_reintentos

    def _registrar(self, id_propuesta: str, etapa: Etapa, decision: str, autor: str, **extra) -> None:
        self.bitacora.registrar(
            RegistroBitacora(
                id_propuesta=id_propuesta,
                etapa=etapa,
                decision=decision,
                autor=autor,
                version_reglas=self.reglas.version,
                version_prompt=VERSION_PROMPT,
                **extra,
            )
        )

    async def _llamar(self, mensajes: list[dict]):
        """Prueba los proveedores en orden. Si uno falla por la API, pasa al siguiente."""
        ultimo_error: ErrorProveedor | None = None
        for proveedor in self.proveedores:
            try:
                return await proveedor.generar(PROMPT_SISTEMA, mensajes, ESQUEMA_SALIDA)
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
