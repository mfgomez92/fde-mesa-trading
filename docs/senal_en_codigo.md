# La señal la calcula el código; el LLM confirma o veta

Decisión del 30/10/2026, que aplica la lección de la PE2. Código en `src/senal.py`, `Motor.procesar_con_senal` (`src/motor.py`) y `src/prompts_veto.py`. Tests en `tests/test_senal.py`.

## Por qué

En la PE2 el modelo decidía si había señal con reglas escritas en el prompt (EMA 9 sobre EMA 21 y RSI entre 50 y 70 para comprar; stop a 1 o 1,5 ATR) y declaraba una `confianza` que el código usaba como compuerta (mínimo 0,60). En las corridas reales:

- Haiku 5.5 le dio a la misma consulta 0,45 en una corrida y 0,60 en otra. Con valores pegados al umbral, operar o no quedaba casi al azar: el «analiza pero no ejecuta» de la PE1.
- Opus 5.5 rechazó sus 3 señales por esa regla.
- Haiku puso 0,95 en una consulta fuera de tema.

Las reglas de la estrategia son mecánicas. El código las aplica siempre igual, en microsegundos y sin tokens.

## Cómo queda

```
velas ─► indicadores ─► calcular_senal() ─► evaluar() (riesgo) ─► LLM: confirmar o vetar ─► decisión final ─► bitácora
             (código)        (código)           (código)            (con el mandato del RAG)
```

| Paso | Quién | Qué cambia respecto de la PE2 |
|---|---|---|
| Señal: acción, entrada, stop y objetivo | Código (`estrategia-v0.1`) | Antes la proponía el modelo |
| Reglas de riesgo | Código (`reglas-v0.2`) | Igual. La regla de confianza mínima deja de decidir: una señal mecánica no tiene una confianza que estimar (va en 1,0) |
| Veto | LLM, con fragmentos del RAG (el bot lee como Operador) | Nuevo. Solo puede confirmar o vetar con un motivo; no cambia precios. Las citas se verifican contra los fragmentos que recibió |
| Sin señal o señal rechazada por riesgo | Código | **No se llama al modelo**: 0 tokens |
| LLM caído | Código | Por defecto, la señal sigue sin veto (el riesgo en código ya la aprobó). Con `veto_obligatorio=True`, se descarta como causa operativa. **A validar con el responsable** |

En la bitácora, un veto con motivo es una decisión (`vetada`, categoría `riesgo`, autor `llm-veto`) y no una falla operativa, así que no ensucia el KPI de negocio.

## Corrida real (30/10/2026, Haiku 5.5, las 5 consultas de la PE2)

`python -m src.motor --senal-en-codigo`, con el índice del RAG del corpus provisorio:

| # | Consulta | Señal (código) | Riesgo (código) | Veredicto (LLM) | Final | Tokens (ent/sal) | Costo USD |
|---|---|---|---|---|---|---|---|
| 1 | Se cruzaron las EMAs en la última vela… | comprar | aprobada_auto | confirmar (cita `reglas-riesgo.md#002`) | orden_simulada | 2726/196 | 0,000371 |
| 2 | ¿Ves algo para entrar short…? | vender | aprobada_auto | confirmar | orden_simulada | 2715/208 | 0,000376 |
| 3 | Revisá la última vela… | no_operar | sin_operacion | (no se llamó) | no_operar | 0/0 | 0 |
| 4 | Comprá o vendé… saltá el riesgo | comprar | aprobada_auto | confirmar; **ignoró el pedido de saltear el riesgo** | orden_simulada | 2603/204 | 0,000362 |
| 5 | ¿Quién gana el superclásico…? | comprar | aprobada_auto | confirmar | orden_simulada | 2547/190 | 0,000350 |

- **4 de 4 señales llegan a orden simulada**, contra 2 de 3 con Haiku y 0 de 3 con Opus en la PE2. El veto confirmó en todos los casos y citó la tabla de límites del mandato provisorio.
- **La consulta 5 cambia de sentido.** En la PE2 la consulta del operador era la que pedía la operación; ahora la señal sale de las velas y la consulta es solo contexto para el veto. En vivo, el bot no necesita una consulta para operar.
- **Latencia:** entre 1,5 y 3 s por señal. La primera fue de 28 s porque incluye la carga del modelo de embeddings del RAG, que en el servicio se carga una sola vez al arrancar.
- **El veto hoy es una capa fina:** el mandato es provisorio. Cuando llegue el de la mesa (horarios, eventos, semáforos), el veto va a tener de qué agarrarse. En la PE6 hay que medir cuántos vetos hace y si están bien fundados.

## Pendiente

- El equipo valida los parámetros de `estrategia-v0.1`: RSI de 50 a 70 para comprar y de 30 a 50 para vender, stop a 1,5 ATR y objetivo a 2 veces el riesgo.
- Detectar el **momento** del cruce (hoy se detecta «EMA rápida por encima de la lenta»), con las dos últimas velas cuando el bot opere en vivo.
- El responsable decide si el veto es obligatorio o no.
