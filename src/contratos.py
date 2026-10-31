"""Contrato de datos del motor: qué entra y qué tiene que devolver el modelo.

Sale de la HU2 de la PE1: "las órdenes dentro de los límites se ejecutan solas
y las que los superan esperan aprobación". El modelo solo PROPONE una operación;
si entra o no lo decide el código de riesgo (src/riesgo.py), nunca el modelo.

Equivalencia TS: cada clase BaseModel es como un schema de Zod
(`z.object({...})`) que además genera el tipo. `Field(gt=0)` ≈ `z.number().positive()`,
`Field(pattern=...)` ≈ `z.string().regex(...)`, y un `Enum` ≈ `z.enum([...])`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import Enum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PlainSerializer, WithJsonSchema, model_validator

CENTAVO = Decimal("0.01")


def a_la_grilla(valor: Decimal) -> Decimal:
    """Lleva el precio a la grilla del bróker: una acción de más de 1 USD cotiza en centavos.

    Es el único redondeo legítimo de un precio: una regla del mercado, no un parche.
    """
    redondeado = valor.quantize(CENTAVO, rounding=ROUND_HALF_EVEN)
    if redondeado <= 0:
        raise ValueError("el precio, llevado a centavos, tiene que ser mayor a 0")
    return redondeado


# Precio en Decimal y no en float: en binario 771.89 no es exacto, y una regla de
# riesgo justo en el límite puede fallar por un error de 1e-14 (ver riesgo.py).
# Equivalencia TS: como usar decimal.js en vez de number, con un preprocess de Zod.
Precio = Annotated[
    Decimal,
    Field(gt=0),
    AfterValidator(a_la_grilla),
    # En JSON sale como número y no como string: la bitácora y la evidencia no cambian de formato.
    PlainSerializer(float, return_type=float, when_used="json"),
    # Al proveedor le llega "number", como antes (Decimal genera un anyOf number|string).
    WithJsonSchema({"type": "number"}),
]


class Temporalidad(str, Enum):
    """Velas permitidas: las de la estrategia intradía candidata (entrevista del
    23/10) y las del MVP con precios demorados (decisión de diseño de la PE1)."""

    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    D1 = "1d"


class Accion(str, Enum):
    COMPRAR = "comprar"  # long
    VENDER = "vender"  # short
    NO_OPERAR = "no_operar"


class Clasificacion(str, Enum):
    """Qué tipo de consulta recibió el modelo."""

    SENAL = "senal"  # hay una operación para proponer
    SIN_SENAL = "sin_senal"  # consulta válida, pero los indicadores no dan entrada
    FUERA_DE_ALCANCE = "fuera_de_alcance"  # no tiene que ver con la mesa


class Indicadores(BaseModel):
    """Indicadores de la estrategia candidata: cruce de EMAs + RSI + ATR."""

    model_config = ConfigDict(extra="forbid")

    ema_rapida: float = Field(gt=0, description="EMA de 9 períodos")
    ema_lenta: float = Field(gt=0, description="EMA de 21 períodos")
    rsi: float = Field(ge=0, le=100, description="RSI de 14 períodos")
    atr: float = Field(gt=0, description="ATR de 14 períodos, en USD")


class Entrada(BaseModel):
    """Lo que recibe el motor: la foto del mercado y la consulta del operador."""

    model_config = ConfigDict(extra="forbid")

    # Regla 1 (patrón): id con fecha y secuencia, para cruzarlo con la bitácora.
    id_propuesta: str = Field(pattern=r"^PRP-\d{8}-\d{4}$", examples=["PRP-20261028-0001"])
    # Regla 2 (patrón): ticker en mayúsculas, de 1 a 5 letras.
    activo: str = Field(pattern=r"^[A-Z]{1,5}$", examples=["SPY"])
    # Regla 3 (Enum): solo las temporalidades de la estrategia.
    temporalidad: Temporalidad
    hora_vela: datetime = Field(description="Cierre de la última vela, con zona horaria")
    precio: Precio = Field(description="Último precio, en USD")
    indicadores: Indicadores
    # Regla 4 (largo): ni vacía ni un texto pegado entero.
    consulta: str = Field(min_length=5, max_length=500)

    @model_validator(mode="after")
    def hora_con_zona(self) -> "Entrada":
        # Una hora sin zona horaria es ambigua (¿ART o Nueva York?) y rompe la bitácora.
        if self.hora_vela.tzinfo is None:
            raise ValueError("hora_vela tiene que incluir la zona horaria")
        return self


class Salida(BaseModel):
    """Lo que el modelo TIENE que devolver. Si no cumple, no se usa."""

    model_config = ConfigDict(extra="forbid")

    clasificacion: Clasificacion
    accion: Accion
    precio_entrada: Precio | None = None
    stop_loss: Precio | None = None
    take_profit: Precio | None = None
    # Rango: la confianza la propone el modelo; no es una probabilidad calibrada.
    confianza: float = Field(ge=0, le=1)
    motivo: str = Field(min_length=20, max_length=600)

    @model_validator(mode="after")
    def coherencia_de_la_operacion(self) -> "Salida":
        """Validación cruzada: los campos tienen que tener sentido entre sí."""
        precios = (self.precio_entrada, self.stop_loss, self.take_profit)

        if self.accion == Accion.NO_OPERAR:
            if any(p is not None for p in precios):
                raise ValueError("si la acción es no_operar, los precios tienen que ser null")
            return self

        if self.clasificacion != Clasificacion.SENAL:
            raise ValueError(f"comprar o vender requiere clasificacion=senal, no {self.clasificacion.value}")
        if any(p is None for p in precios):
            raise ValueError("comprar o vender requiere precio_entrada, stop_loss y take_profit")

        entrada, stop, objetivo = precios
        if self.accion == Accion.COMPRAR and not (stop < entrada < objetivo):
            raise ValueError("en una compra tiene que cumplirse stop_loss < precio_entrada < take_profit")
        if self.accion == Accion.VENDER and not (objetivo < entrada < stop):
            raise ValueError("en una venta tiene que cumplirse take_profit < precio_entrada < stop_loss")
        return self


class CampoFallido(BaseModel):
    """Un error de validación, legible y sin el traceback."""

    campo: str  # ruta del error, por ejemplo "indicadores -> rsi"
    mensaje: str
    tipo: str  # identificador técnico de Pydantic, por ejemplo "less_than_equal"


class ErrorEstructurado(BaseModel):
    """Lo que devuelve el motor cuando no puede dar una respuesta válida."""

    tipo: str  # entrada_invalida | salida_invalida | error_api | presupuesto_agotado
    mensaje: str
    campos: list[CampoFallido] = Field(default_factory=list)
