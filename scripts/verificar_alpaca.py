"""Verifica qué da el plan gratuito de Alpaca. Resultados en docs/datos_alpaca.md.

1. Velas de 5 minutos de SPY con feed IEX hasta ahora (¿incluye los últimos 15 minutos?).
2. Feed SIP hasta hace 16 minutos y hasta ahora (el plan Basic restringe los últimos 15).
3. Diferencia entre el cierre IEX y el SIP en las mismas velas.
4. get_account(): que responda y que sea paper. No imprime saldos ni el número de cuenta.

Uso, desde bot/:  python -m scripts.verificar_alpaca
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alpaca.data.enums import DataFeed
from dotenv import load_dotenv

from src.broker import ErrorAlpaca, ErrorConfiguracion, resumen_cuenta
from src.datos_mercado import Vela, bajar_velas

RAIZ = Path(__file__).resolve().parent.parent


def probar(titulo: str, feed: DataFeed, fin: datetime) -> list[Vela]:
    print(f"\n== {titulo}: feed={feed.value}, hasta {fin:%Y-%m-%d %H:%M} UTC")
    try:
        velas = bajar_velas("SPY", "5m", "4d", feed=feed, fin=fin)
    except ErrorAlpaca as e:
        print(f"   ERROR {e.error.model_dump_json()}")
        return []
    if not velas:
        print("   0 velas")
        return []
    ultima = velas[-1]
    atraso = (datetime.now(timezone.utc) - (ultima.hora + timedelta(minutes=5))).total_seconds() / 60
    print(f"   {len(velas)} velas · primera {velas[0].hora:%m-%d %H:%M} · última {ultima.hora:%m-%d %H:%M} UTC "
          f"(cierra hace {atraso:.0f} min) · cierre {ultima.cierre:.2f}")
    return velas


def main() -> int:
    load_dotenv(RAIZ / ".env")
    ahora = datetime.now(timezone.utc)
    print(f"Ahora: {ahora:%Y-%m-%d %H:%M} UTC")
    try:
        iex = probar("1. IEX hasta ahora", DataFeed.IEX, ahora)
        sip_viejo = probar("2a. SIP hasta hace 16 min", DataFeed.SIP, ahora - timedelta(minutes=16))
        probar("2b. SIP hasta ahora", DataFeed.SIP, ahora)
        probar("2c. SIP demorado (delayed_sip) hasta ahora", DataFeed.DELAYED_SIP, ahora)

        print("\n== 3. Cierre IEX contra SIP en las mismas velas")
        sip = {v.hora: v.cierre for v in sip_viejo}
        pares = [(v.hora, v.cierre, sip[v.hora]) for v in iex if v.hora in sip]
        if pares:
            difs = [abs(i - s) / s * 100 for _, i, s in pares]
            for hora, i, s in pares[-3:]:
                print(f"   {hora:%m-%d %H:%M} UTC · IEX {i:.2f} · SIP {s:.2f} · {abs(i - s) / s * 100:.3f} %")
            print(f"   {len(pares)} velas comparadas · diferencia media {sum(difs) / len(difs):.4f} % · "
                  f"máxima {max(difs):.4f} % · velas SIP sin par en IEX: {len(sip) - len(pares)}")
        else:
            print("   No hay velas para comparar")

        print("\n== 4. Cuenta (get_account)")
        try:
            print(f"   {resumen_cuenta()}")
        except ErrorAlpaca as e:
            print(f"   ERROR {e.error.model_dump_json()}")
    except ErrorConfiguracion as e:
        print(f"No arranco: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
