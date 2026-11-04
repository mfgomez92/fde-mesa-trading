# Ficha de los KPIs

Responde al punto 1 de la devolución de la PE1: para cada KPI, el período exacto de cálculo y la muestra mínima antes de decidir. Los valores los propone el FDE y **quedan a validar con el responsable de la mesa**.

| | KPI técnico | KPI de negocio |
|---|---|---|
| **Indicador** | % de propuestas con la decisión de riesgo y la decisión final registradas con hora | % de señales frenadas o demoradas por causas operativas, no por una decisión de riesgo |
| **Línea de base (PE1)** | 25 % (5 de 20 propuestas) | 60 % (3 de 5 señales, 13–20/10) |
| **Meta** | ≥ 95 % | 0 % |
| **Ventana de cálculo** | Semana de mercado: de lunes 00:00 a sábado 00:00, en hora argentina (ART, UTC−3). Se informa también el acumulado desde el arranque en paper | Igual que el técnico |
| **Muestra mínima** | 20 propuestas en la ventana. Con menos, el valor se informa pero se marca como **no concluyente** | 10 señales decididas en la ventana. Con menos, no concluyente |
| **Fuente** | Bitácora (`src/bitacora.py`): etapas `propuesta`, `decision_riesgo` y `decision_final`, cada una con `hora` en UTC | Bitácora: `decision_final` con `categoria_motivo` = `operativo` o `riesgo` |
| **Cálculo** | `kpi_tecnico(registros, desde, hasta)` | `kpi_negocio(registros, desde, hasta)` |
| **Control** | Automático diario + revisión manual semanal de 10 propuestas al azar (PE1) | Igual |

**Por qué 20 y 10.** Con menos de 20 propuestas, una sola propuesta incompleta mueve el KPI técnico más de 5 puntos y alcanza para cruzar la meta de 95 %. Con 10 señales, una señal frenada pesa 10 puntos, lo mínimo razonable para una meta de 0 %. Con la frecuencia prevista para la estrategia intradía, se juntan en una semana.

**Propuestas que esperan aprobación.** Una propuesta `requiere_aprobacion` no tiene decisión final hasta que una persona decide (M5). Mientras tanto cuenta como incompleta: el KPI no se infla con propuestas sin resolver.

**Costo y latencia** (sin meta comprometida en la PE1): se registran por ciclo (tokens de entrada y salida, costo en USD y latencia en ms, reintentos incluidos) en la etapa `propuesta` de la bitácora. El presupuesto por ciclo es `PRESUPUESTO_TOKENS_CICLO` (12.000 tokens por defecto).
