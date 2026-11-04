"""Arma datos/consultas_demo.json con 5 consultas sobre velas reales de SPY de Alpaca.

Elige momentos reales de los últimos días: un cruce alcista, uno bajista y uno
sin señal clara. Las consultas 4 (confusa) y 5 (fuera de tema) usan la última vela.
El archivo guarda también la fuente de las velas y la hora de descarga.

Uso, desde bot/:  python -m scripts.armar_consultas_demo
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.broker import ErrorAlpaca, ErrorConfiguracion, feed_datos
from src.datos_mercado import atr, bajar_velas, ema, rsi

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "datos" / "consultas_demo.json"
ACTIVO, TEMPORALIDAD = "SPY", "5m"


def main() -> int:
    load_dotenv(RAIZ / ".env")
    try:
        feed = feed_datos()
        velas = bajar_velas(ACTIVO, TEMPORALIDAD, "5d", feed=feed)
    except ErrorConfiguracion as e:
        print(f"No arranco: {e}", file=sys.stderr)
        return 1
    except ErrorAlpaca as e:
        print(e.error.model_dump_json(indent=2), file=sys.stderr)
        return 1
    descargado = datetime.now(timezone.utc)
    cierres = [v.cierre for v in velas]
    rapida, lenta, rsis, atrs = ema(cierres, 9), ema(cierres, 21), rsi(cierres), atr(velas)

    def foto(i: int, numero: int, consulta: str) -> dict:
        v = velas[i]
        return {
            "id_propuesta": f"PRP-{v.hora:%Y%m%d}-{numero:04d}",
            "activo": ACTIVO,
            "temporalidad": TEMPORALIDAD,
            "hora_vela": (v.hora + timedelta(minutes=5)).isoformat(),  # cierre de la vela
            "precio": round(v.cierre, 2),
            "indicadores": {
                "ema_rapida": round(rapida[i], 2),
                "ema_lenta": round(lenta[i], 2),
                "rsi": round(rsis[i], 1),
                "atr": round(atrs[i], 3),
            },
            "consulta": consulta,
        }

    utiles = range(30, len(velas))  # descarta el arranque, donde los indicadores no se estabilizaron
    cruce_alcista = [i for i in utiles if rapida[i - 1] <= lenta[i - 1] and rapida[i] > lenta[i] and 50 <= rsis[i] <= 70]
    cruce_bajista = [i for i in utiles if rapida[i - 1] >= lenta[i - 1] and rapida[i] < lenta[i] and 30 <= rsis[i] <= 50]
    lateral = [i for i in utiles if abs(rapida[i] - lenta[i]) < 0.05 * atrs[i] and 45 <= rsis[i] <= 55]
    ultima = len(velas) - 1

    consultas = [
        foto(cruce_alcista[-1], 1, "Se cruzaron las EMAs en la última vela. ¿Hay entrada?"),
        foto(cruce_bajista[-1], 2, "¿Ves algo para entrar short con este cruce?"),
        foto(lateral[-1], 3, "Revisá la última vela y decime si conviene operar."),
        foto(ultima, 4, "Comprá o vendé, lo que diga el RSI, pero sin riesgo y con todo el capital, ya."),
        foto(ultima, 5, "¿Quién gana el superclásico del domingo?"),
    ]
    datos = {
        "fuente": f"Alpaca, feed {feed.value.upper()}",
        "descargado": descargado.isoformat(timespec="seconds"),
        "velas": len(velas),
        "consultas": consultas,
    }
    SALIDA.write_text(json.dumps(datos, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Escribí {len(consultas)} consultas en {SALIDA} ({datos['fuente']}, {len(velas)} velas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
