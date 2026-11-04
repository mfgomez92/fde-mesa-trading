# Autoevaluación de la PE2 contra la rúbrica

Entrega: `curso/modulo-02/entrega/FDE_PE2_Gomez_Matias.pdf` (2 páginas), con su fuente en `fuente/` y la salida completa de la terminal en `evidencia/`. Repo: https://github.com/mfgomez92/fde-trading-desk, etiqueta `pe2`. Fecha: 29/10/2026.

**Versión 2 (29/10):** se rehízo con datos reales de **Alpaca** (cuenta paper personal, feed IEX) en lugar de Yahoo. No cambian el contrato, los KPIs, la estrategia ni las reglas de riesgo. La única diferencia es que los precios ahora son `Decimal` en centavos (`reglas-v0.2`, un fix de redondeo anterior a este cambio).

| Criterio (peso) | Qué pide | Estado | Evidencia | Falta o riesgo |
|---|---|---|---|---|
| Contrato de datos (25 %) | `Entrada` y `Salida` salen de la HU de la PE1, con tipos y al menos 3 reglas significativas | ✅ Cumple | PDF §1; `bot/src/contratos.py`. Salen de la HU2. Reglas: 2 patrones, 2 Enum en la entrada y 2 en la salida, 2 rangos, 2 largos, validador de zona horaria y validación cruzada de precios; `extra="forbid"`. Los precios son `Decimal` llevados a centavos | — |
| Llamada real y salida validada (25 %) | Modelo real, prompt en plantilla separada, validación contra `Salida`, ejecución exitosa con la salida real | ✅ Cumple | PDF §2; `src/prompts.py` (plantilla versionada `prompt-v1`); `evidencia/corrida_haiku-5-5.txt` y `corrida_opus-5-5.txt` (10 de 10 validadas, con velas de Alpaca) | — |
| Medición real (20 %) | Tabla de 5 consultas (una confusa y una fuera de tema), tokens de `usage`, costo USD, latencia ms, promedios contra la meta del KPI técnico de la PE1 | ✅ Cumple, con una aclaración | PDF §4: tabla con promedios (Haiku: 1.511 / 391 tokens, 0,00035 USD, 2,7 s) y comparación con Opus sobre las mismas velas. Precios verificados contra la página oficial de Anthropic el 29/10 | **Choque:** la consigna supone un KPI técnico «de precisión, tiempo o costo por consulta», pero el de la PE1 es «% de propuestas con la decisión de riesgo y la final registradas con hora» (25 % → ≥95 %). Lo comparé con ese KPI: dio 100 % (5 de 5), **no concluyente** por la muestra mínima de 20 (`bot/docs/kpis.md`). Costo y latencia van sin meta comprometida, como en la PE1. Un docente podría esperar una meta de costo o latencia |
| Manejo de errores (15 %) | Caso inválido con el campo que falló, el reintento y el error estructurado; errores de la API capturados | ✅ Cumple, con una aclaración | PDF §3 y §5; `evidencia/demo_errores_*.txt`, `error_api_clave_invalida.txt` (401 de Anthropic) y `error_alpaca_clave_invalida.txt` (401 de Alpaca, capturado como `ErrorEstructurado` `error_api`) | **Choque:** la entrada inválida (error 1) y los dos 401 son reales, pero la salida inválida del modelo (errores 2 y 3) es **inyectada** (`ProveedorConErrorInyectado`), porque con salidas estructuradas el modelo real nunca devolvió algo inválido. Las llamadas, los tokens y el costo son reales, y el PDF lo aclara. El 429 y la falta de conexión (de Anthropic y de Alpaca) están cubiertos con tests (`tests/test_motor.py`, `tests/test_alpaca.py`) |
| Repositorio ordenado y seguro (15 %) | El PDF muestra la estructura con README, `.env.example` sin valores, `.gitignore` con `.env` y la etiqueta `pe2`; sin claves subidas | ✅ Cumple, con dos aclaraciones | PDF §6; `bot/.env.example` (con las variables de Alpaca, sin valores), `bot/.gitignore`, tag `pe2`. Revisé el diff y la evidencia buscando claves (`PK`, `AK`, `sk-`, `sk-ant-`) y el número de cuenta: no hay; la cuenta se muestra como `PA***` | **Choques:** (1) la consigna pide un repo `fde-<caso>` con `src/motor.py` en la raíz; acá el motor está en `bot/` dentro de `fde-trading-desk`, porque el repo también guarda el material del curso (el README de la raíz tiene un índice). (2) El repo es **privado**: los docentes necesitan acceso de lectura (pendiente de Matías) |

**Puntaje estimado:** 100 de 100 si se aceptan las aclaraciones. El riesgo más concreto es el KPI técnico (podría costar algún punto en «Medición real»). Aprobación: 70.

## Extras
- Llamada asíncrona y proveedor abstraído (Anthropic ↔ OpenAI con fallback): lo pide la consigna como extra opcional.
- Datos de mercado reales de Alpaca, con el candado paper/real (`ALPACA_PAPER`, `ALPACA_LIVE_CONFIRMADO`) y la verificación del plan gratuito documentada (`bot/docs/datos_alpaca.md`).
- Comparación medida entre dos modelos para elegir el default (Haiku 5.5, elegido por Matías).
- Las reglas de riesgo están en código y cada decisión queda en la bitácora, que calcula los dos KPIs (puntos 1, 2, 5 y 6 de la devolución de la PE1).
- 69 tests con pytest, que no necesitan claves ni red.

## Para la revisión
- **La confianza decide.** Con el umbral de 0,60, Opus rechazó sus 3 señales y Haiku aprobó 2 de 3. Entre corridas, Haiku también cambió la confianza de una misma consulta (de 0,45 a 0,60). Refuerza el pendiente de validar el umbral con el responsable y de reevaluar en la PE6 con 20 casos o más.
- **La frescura en vivo de IEX** se verificó con el mercado cerrado. Hay que repetir la prueba 1 de `datos_alpaca.md` durante una rueda.
