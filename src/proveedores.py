"""Abstracción de proveedor de LLM: el motor no sabe si habla con Anthropic u OpenAI.

Equivalencia TS: `ProveedorLLM` es como una `interface` de TypeScript. Cualquier
clase con un método `generar` con esa firma cumple el contrato, sin heredar
de nada (tipado estructural, igual que en TS).

`async def` ≈ una función `async` de JS: devuelve algo "awaitable" (≈ Promise),
y quien la llama hace `await proveedor.generar(...)`.
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic
import openai
from pydantic import BaseModel

from .precios import costo_usd


@dataclass
class RespuestaLLM:
    """Lo que devuelve cualquier proveedor, ya normalizado."""

    texto: str
    proveedor: str
    modelo: str
    tokens_entrada: int
    tokens_salida: int
    latencia_ms: int
    costo_usd: float


class ErrorProveedor(Exception):
    """Error de la API, traducido a un tipo que el motor entiende."""

    def __init__(self, tipo: str, mensaje: str, proveedor: str):
        super().__init__(mensaje)
        self.tipo = tipo  # limite_de_uso | conexion | autenticacion | servidor | rechazo | otro
        self.mensaje = mensaje
        self.proveedor = proveedor


class ProveedorLLM(Protocol):
    nombre: str
    modelo: str

    async def generar(self, sistema: str, mensajes: list[dict], esquema: dict) -> RespuestaLLM: ...


# Palabras clave de JSON Schema que validamos con Pydantic y no le pedimos al
# proveedor: el esquema del proveedor fija la FORMA (campos, tipos, Enum) y
# Pydantic fija las REGLAS (rangos, patrones, coherencia entre campos).
_CLAVES_QUE_VALIDA_PYDANTIC = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "pattern", "default", "title", "examples",
}


def esquema_para_llm(modelo: type[BaseModel]) -> dict:
    """Arma el JSON Schema "de forma" que se le manda al modelo."""
    esquema = modelo.model_json_schema()
    definiciones = esquema.pop("$defs", {})

    def limpiar(nodo: Any) -> Any:
        if isinstance(nodo, dict):
            if "$ref" in nodo:  # reemplaza {"$ref": "#/$defs/Accion"} por la definición
                return limpiar(copy.deepcopy(definiciones[nodo["$ref"].split("/")[-1]]))
            limpio = {k: limpiar(v) for k, v in nodo.items() if k not in _CLAVES_QUE_VALIDA_PYDANTIC}
            if limpio.get("type") == "object":
                limpio["additionalProperties"] = False
                limpio["required"] = list(limpio.get("properties", {}))
            return limpio
        if isinstance(nodo, list):
            return [limpiar(x) for x in nodo]
        return nodo

    return limpiar(esquema)


class ProveedorAnthropic:
    nombre = "anthropic"

    def __init__(
        self,
        modelo: str,
        api_key: str,
        esfuerzo: str = "low",
        max_tokens: int = 4000,
        timeout_s: float = 60.0,
    ):
        self.modelo = modelo
        self.esfuerzo = esfuerzo
        self.max_tokens = max_tokens
        # La clave llega de BOT_ANTHROPIC_API_KEY (ver motor.py). El SDK ya reintenta 2 veces los 429 y 5xx.
        self._cliente = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_s, max_retries=2)

    async def generar(self, sistema: str, mensajes: list[dict], esquema: dict) -> RespuestaLLM:
        inicio = time.perf_counter()
        try:
            respuesta = await self._cliente.beta.messages.create(
                model=self.modelo,
                max_tokens=self.max_tokens,
                system=sistema,
                messages=mensajes,
                output_config={
                    "effort": self.esfuerzo,  # poco razonamiento: la tarea es acotada
                    "format": {"type": "json_schema", "schema": esquema},
                },
                # Si un filtro de seguridad rechaza el pedido, Anthropic lo reintenta
                # con otro modelo dentro de la misma llamada.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError as e:
            raise ErrorProveedor("limite_de_uso", f"Límite de uso (429): {e.message}", self.nombre) from e
        except anthropic.AuthenticationError as e:
            raise ErrorProveedor("autenticacion", "Clave inválida o ausente (401)", self.nombre) from e
        except anthropic.APITimeoutError as e:
            raise ErrorProveedor("conexion", "La API no respondió a tiempo", self.nombre) from e
        except anthropic.APIConnectionError as e:
            raise ErrorProveedor("conexion", "Sin conexión con la API", self.nombre) from e
        except anthropic.APIStatusError as e:
            tipo = "servidor" if e.status_code >= 500 else "otro"
            raise ErrorProveedor(tipo, f"Error {e.status_code}: {e.message}", self.nombre) from e
        latencia_ms = round((time.perf_counter() - inicio) * 1000)

        if respuesta.stop_reason == "refusal":
            raise ErrorProveedor("rechazo", "El modelo rechazó el pedido", self.nombre)

        uso = respuesta.usage
        tokens_entrada = (
            uso.input_tokens
            + (uso.cache_creation_input_tokens or 0)
            + (uso.cache_read_input_tokens or 0)
        )
        texto = "".join(b.text for b in respuesta.content if b.type == "text")
        return RespuestaLLM(
            texto=texto,
            proveedor=self.nombre,
            modelo=self.modelo,
            tokens_entrada=tokens_entrada,
            tokens_salida=uso.output_tokens,  # incluye los tokens de razonamiento
            latencia_ms=latencia_ms,
            costo_usd=costo_usd(self.modelo, tokens_entrada, uso.output_tokens),
        )


class ProveedorOpenAI:
    nombre = "openai"

    def __init__(self, modelo: str, max_tokens: int = 4000, timeout_s: float = 60.0):
        self.modelo = modelo
        self.max_tokens = max_tokens
        # Lee OPENAI_API_KEY del entorno.
        self._cliente = openai.AsyncOpenAI(timeout=timeout_s, max_retries=2)

    async def generar(self, sistema: str, mensajes: list[dict], esquema: dict) -> RespuestaLLM:
        inicio = time.perf_counter()
        try:
            respuesta = await self._cliente.chat.completions.create(
                model=self.modelo,
                messages=[{"role": "system", "content": sistema}, *mensajes],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "salida", "strict": True, "schema": esquema},
                },
                max_completion_tokens=self.max_tokens,
            )
        except openai.RateLimitError as e:
            raise ErrorProveedor("limite_de_uso", f"Límite de uso (429): {e.message}", self.nombre) from e
        except openai.AuthenticationError as e:
            raise ErrorProveedor("autenticacion", "Clave inválida o ausente (401)", self.nombre) from e
        except openai.APITimeoutError as e:
            raise ErrorProveedor("conexion", "La API no respondió a tiempo", self.nombre) from e
        except openai.APIConnectionError as e:
            raise ErrorProveedor("conexion", "Sin conexión con la API", self.nombre) from e
        except openai.APIStatusError as e:
            tipo = "servidor" if e.status_code >= 500 else "otro"
            raise ErrorProveedor(tipo, f"Error {e.status_code}: {e.message}", self.nombre) from e
        latencia_ms = round((time.perf_counter() - inicio) * 1000)

        mensaje = respuesta.choices[0].message
        if mensaje.refusal:
            raise ErrorProveedor("rechazo", "El modelo rechazó el pedido", self.nombre)

        uso = respuesta.usage
        return RespuestaLLM(
            texto=mensaje.content or "",
            proveedor=self.nombre,
            modelo=self.modelo,
            tokens_entrada=uso.prompt_tokens,
            tokens_salida=uso.completion_tokens,
            latencia_ms=latencia_ms,
            costo_usd=costo_usd(self.modelo, uso.prompt_tokens, uso.completion_tokens),
        )


@dataclass
class ProveedorSimulado:
    """LLM falso para los tests y para probar el motor sin claves.

    Devuelve, en orden, los textos (o errores) que le cargues. Equivalente a un
    `vi.fn().mockResolvedValueOnce(...)` de Vitest.
    """

    respuestas: list[str | ErrorProveedor]
    nombre: str = "simulado"
    modelo: str = "simulado"
    tokens_entrada: int = 900
    tokens_salida: int = 150
    llamadas: list[list[dict]] = field(default_factory=list)

    async def generar(self, sistema: str, mensajes: list[dict], esquema: dict) -> RespuestaLLM:
        self.llamadas.append(copy.deepcopy(mensajes))
        siguiente = self.respuestas.pop(0)
        if isinstance(siguiente, ErrorProveedor):
            raise siguiente
        return RespuestaLLM(
            texto=siguiente,
            proveedor=self.nombre,
            modelo=self.modelo,
            tokens_entrada=self.tokens_entrada,
            tokens_salida=self.tokens_salida,
            latencia_ms=1,
            costo_usd=0.0,
        )


class ProveedorConErrorInyectado:
    """Envuelve un proveedor real y arruina sus primeras N respuestas.

    Sirve solo para la demo de errores (`--demo-errores`): fuerza una salida
    inválida para mostrar el reintento con un modelo real. Nunca se usa en la
    operación normal.
    """

    def __init__(self, real: ProveedorLLM, respuestas_rotas: int, texto_roto: str):
        self.real = real
        self.nombre = real.nombre
        self.modelo = real.modelo
        self.pendientes = respuestas_rotas
        self.texto_roto = texto_roto

    async def generar(self, sistema: str, mensajes: list[dict], esquema: dict) -> RespuestaLLM:
        respuesta = await self.real.generar(sistema, mensajes, esquema)
        if self.pendientes > 0:
            self.pendientes -= 1
            respuesta.texto = self.texto_roto  # tokens, costo y latencia siguen siendo reales
        return respuesta
