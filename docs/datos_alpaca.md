# Datos de Alpaca: qué da el plan gratuito

Verificado el 29/10/2026 a las 05:28 UTC (02:28 ART, con el mercado cerrado) con la cuenta paper personal, `alpaca-py` 0.44.0 y SPY en velas de 5 minutos. Se reproduce con `python -m scripts.verificar_alpaca`, desde `bot/`. Las velas se filtran a la rueda regular de Nueva York (9:30 a 16:00), igual que hacía Yahoo.

| # | Prueba | Resultado |
|---|---|---|
| 1 | `feed=iex`, hasta ahora | ✅ 234 velas (26 al 28/10, 78 por rueda, sin huecos). La última es la de las 15:55 de Nueva York (19:55 UTC) del 28/10, el cierre de la última sesión. **No hay restricción de 15 minutos**: el pedido hasta «ahora» responde. |
| 2a | `feed=sip`, hasta hace 16 minutos | ✅ 234 velas, las mismas horas que IEX. |
| 2b | `feed=sip`, hasta ahora (incluye los últimos 15 minutos) | ❌ `403: subscription does not permit querying recent SIP data`. El plan gratuito no permite pedir SIP con un `end` dentro de los últimos 15 minutos, aunque el mercado esté cerrado. |
| 2c | `feed=delayed_sip`, hasta ahora | ❌ `400: invalid feed: delayed_sip`. Ese feed figura en el SDK, pero la API de datos de acciones no lo acepta. |
| 3 | Cierre IEX contra SIP, mismas 234 velas | Diferencia media **0,0037 %** (unos 3 centavos con SPY a 772 USD) y máxima **0,026 %** (unos 20 centavos). Ejemplos del 28/10: 15:45 → 771,88 contra 771,89; 15:50 → 771,99 contra 771,99; 15:55 → 772,28 contra 772,31. |
| 4 | `TradingClient(paper=True).get_account()` | ✅ Responde: cuenta `PA***`, estado `ACTIVE`, paper, USD. No se mandan órdenes en la PE2. |

## Qué se usa para qué

- **Operar en vivo: `feed=iex`.** Es en tiempo real y gratis. Para precios y para indicadores de precio (EMAs, RSI, ATR) la diferencia con el consolidado es chica: la media está muy por debajo de un ATR de 5 minutos (cerca de 1 USD), y la máxima ronda el 20 % de un ATR. **No sirve para indicadores de volumen**, porque IEX es un solo exchange (alrededor del 2,5 % del volumen). Si la estrategia final usa volumen, hay que evaluar Algo Trader Plus (SIP completo) con una matriz ponderada, como dice `CLAUDE.md`.
- **Backtests y evals: `feed=sip`, con `end` de más de 15 minutos atrás.** Es el precio consolidado de todo el mercado y entra en el plan gratuito para historia.

## Pendiente

- ~~Repetir la prueba 1 durante una rueda~~ **Hecho el 3/11, en el servidor (AWS, us-east-2), con el bot en seco y velas de 15 minutos:** en la rueda completa el bot pidió cada vela a los 20 s de su cierre y **25 de 26 ya estaban disponibles** (`frescura_iex`: velas 26, al_dia 25). La que faltaba es la primera del día: en la apertura (9:30 ET) el bot procesa la última vela de la rueda anterior (`PRP-20261102-1600`), que se mide tarde por diseño y no puede generar entradas (fuera de la ventana de 10:00 a 15:30 ET). Desde la vela de las 9:45 ET, todas llegaron a tiempo. Conclusión: el feed IEX gratis es en tiempo real para velas de 15 minutos, con menos de 20 s de demora, y alcanza para la estrategia.
