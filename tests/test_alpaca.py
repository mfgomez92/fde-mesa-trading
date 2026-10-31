"""Tests de Alpaca con el cliente mockeado: corren sin claves y sin red.

El cliente falso se inyecta por parámetro (≈ pasarle un mock al constructor en
vez de usar vi.mock sobre el módulo).
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import requests
from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed
from alpaca.data.models.bars import Bar

from src.broker import ErrorAlpaca, ErrorConfiguracion, feed_datos, modo_paper, resumen_cuenta
from src.datos_mercado import bajar_velas
from src.motor import main


class ClienteFalso:
    """Imita StockHistoricalDataClient: devuelve las barras o levanta el error que le cargues."""

    def __init__(self, barras=None, error: Exception | None = None):
        self.barras, self.error, self.pedidos = barras or [], error, []

    def get_stock_bars(self, pedido):
        self.pedidos.append(pedido)
        if self.error:
            raise self.error
        return SimpleNamespace(data={"SPY": self.barras})


def barra(hora: str, cierre: float) -> Bar:
    return Bar("SPY", {"t": hora, "o": cierre, "h": cierre + 0.5, "l": cierre - 0.5, "c": cierre, "v": 1000, "n": 10, "vw": cierre})


def error_api(codigo: int, mensaje: str) -> APIError:
    respuesta = requests.Response()
    respuesta.status_code = codigo
    return APIError(json.dumps({"code": codigo * 100000, "message": mensaje}), requests.HTTPError(response=respuesta))


@pytest.fixture(autouse=True)
def entorno_limpio(monkeypatch):
    for nombre in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_PAPER", "ALPACA_LIVE_CONFIRMADO", "ALPACA_FEED"):
        monkeypatch.delenv(nombre, raising=False)


# --- Mapeo de velas ---------------------------------------------------------

def test_las_velas_quedan_en_utc_y_ordenadas():
    # Alpaca manda la hora en UTC ("Z"); acá llegan desordenadas a propósito.
    cliente = ClienteFalso([barra("2026-10-28T19:50:00Z", 771.9), barra("2026-10-28T19:45:00Z", 771.5)])

    velas = bajar_velas("SPY", "5m", "5d", cliente=cliente)

    assert [v.hora for v in velas] == [
        datetime(2026, 10, 28, 19, 45, tzinfo=timezone.utc),
        datetime(2026, 10, 28, 19, 50, tzinfo=timezone.utc),
    ]
    assert all(v.hora.utcoffset().total_seconds() == 0 for v in velas)
    assert (velas[1].apertura, velas[1].maximo, velas[1].minimo, velas[1].cierre) == (771.9, 772.4, 771.4, 771.9)


def test_una_hora_sin_zona_se_toma_como_utc():
    sin_zona = SimpleNamespace(timestamp=datetime(2026, 10, 28, 19, 45), open=1.0, high=2.0, low=0.5, close=1.5)
    vela = bajar_velas(cliente=ClienteFalso([sin_zona]))[0]
    assert vela.hora.tzinfo == timezone.utc


def test_el_pedido_usa_velas_de_5_minutos_y_el_feed_iex_por_defecto():
    cliente = ClienteFalso()
    fin = datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc)

    bajar_velas("SPY", "5m", "5d", fin=fin, cliente=cliente)

    pedido = cliente.pedidos[0]
    assert (pedido.timeframe.amount, pedido.timeframe.unit.value) == (5, "Min")
    assert pedido.feed == DataFeed.IEX
    assert pedido.symbol_or_symbols == "SPY"


def test_feed_invalido_da_un_error_claro(monkeypatch):
    monkeypatch.setenv("ALPACA_FEED", "nasdaq")
    with pytest.raises(ErrorConfiguracion, match="ALPACA_FEED"):
        feed_datos()


# --- Candado paper / real ---------------------------------------------------

def test_por_defecto_es_paper():
    assert modo_paper() is True


def test_real_sin_confirmacion_no_arranca(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER", "false")
    with pytest.raises(ErrorConfiguracion, match="ALPACA_LIVE_CONFIRMADO"):
        modo_paper()


def test_real_con_confirmacion_pasa_el_candado(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER", "false")
    monkeypatch.setenv("ALPACA_LIVE_CONFIRMADO", "si")
    assert modo_paper() is False


def test_valor_raro_en_alpaca_paper_no_arranca(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER", "quizas")
    with pytest.raises(ErrorConfiguracion, match="true o false"):
        modo_paper()


async def test_el_motor_no_arranca_sin_confirmacion(monkeypatch, capsys):
    monkeypatch.setenv("ALPACA_PAPER", "false")
    assert await main(["--simulado"]) == 1
    assert "ALPACA_LIVE_CONFIRMADO" in capsys.readouterr().err


def test_la_cuenta_se_consulta_solo_en_paper(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER", "false")
    monkeypatch.setenv("ALPACA_LIVE_CONFIRMADO", "si")
    with pytest.raises(ErrorConfiguracion, match="paper"):
        resumen_cuenta(cliente=SimpleNamespace())


def test_la_cuenta_no_muestra_el_numero():
    cuenta = SimpleNamespace(account_number="PAEJEMPLO", status=SimpleNamespace(value="ACTIVE"), currency="USD")
    resumen = resumen_cuenta(cliente=SimpleNamespace(get_account=lambda: cuenta))
    assert resumen == {"cuenta": "PA***", "estado": "ACTIVE", "paper": True, "moneda": "USD"}


# --- Claves y errores de la API ---------------------------------------------

def test_sin_claves_el_error_dice_cuales_faltan():
    with pytest.raises(ErrorConfiguracion, match="ALPACA_API_KEY y ALPACA_SECRET_KEY"):
        bajar_velas()


@pytest.mark.parametrize(
    "error, causa",
    [
        (error_api(401, "unauthorized."), "alpaca/autenticacion: Claves de Alpaca inválidas (401)"),
        (error_api(429, "too many requests."), "alpaca/limite_de_uso"),
        (error_api(403, "subscription does not permit querying recent SIP data"), "recent SIP data"),
        (requests.ConnectionError("sin red"), "alpaca/conexion: Sin conexión con Alpaca"),
    ],
)
def test_los_errores_de_alpaca_terminan_en_error_estructurado(error, causa):
    with pytest.raises(ErrorAlpaca) as capturado:
        bajar_velas(cliente=ClienteFalso(error=error))
    assert capturado.value.error.tipo == "error_api"
    assert causa in capturado.value.error.mensaje


# --- Rueda regular ----------------------------------------------------------

def test_se_descartan_pre_market_y_after_hours():
    # 28/10 (horario de verano de Nueva York): la rueda va de 13:30 a 20:00 UTC.
    cliente = ClienteFalso([
        barra("2026-10-28T13:25:00Z", 770.0),  # pre-market
        barra("2026-10-28T13:30:00Z", 771.0),  # primera vela de la rueda
        barra("2026-10-28T19:55:00Z", 772.0),  # última vela de la rueda
        barra("2026-10-28T20:00:00Z", 773.0),  # after-hours
    ])
    assert [v.cierre for v in bajar_velas(cliente=cliente)] == [771.0, 772.0]


def test_la_rueda_sigue_el_cambio_de_horario():
    # 2/11, ya sin horario de verano: la rueda abre a las 14:30 UTC, no a las 13:30.
    cliente = ClienteFalso([barra("2026-11-02T13:30:00Z", 770.0), barra("2026-11-02T14:30:00Z", 771.0)])
    assert [v.cierre for v in bajar_velas(cliente=cliente)] == [771.0]
