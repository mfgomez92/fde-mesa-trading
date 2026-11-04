# Reglas de riesgo vigentes (reglas-v0.2)

Ficha provisoria del mandato de riesgo del bot, generada desde `src/riesgo.py`. Reemplaza al
mandato de la mesa hasta que llegue (pedido del 2/11). Los valores los propuso el FDE y quedan
a validar con el responsable de la mesa. Solo la ve el Operador: cambiar una regla es una acción
del Operador y requiere aprobación.

## Quién decide

El modelo propone una operación y el código decide si entra. El modelo no puede saltear ni
negociar estas reglas. Las reglas se evalúan en este orden y la primera que falla decide.

## Límites

| Regla | Valor | Si no se cumple |
|---|---|---|
| Confianza mínima de la propuesta | 0.6 | Rechazada (código CONFIANZA_BAJA) |
| Distancia máxima entre la entrada y el último precio | 0.3 % | Rechazada (ENTRADA_LEJANA) |
| Relación riesgo/beneficio mínima | 1:1.5 | Rechazada (RR_INSUFICIENTE) |
| Stop máximo para aprobar sin una persona | 1.0 % del precio de entrada | Requiere aprobación del Operador (FUERA_DE_LIMITES) |

Si cumple todo, la propuesta se aprueba sola (DENTRO_DE_LIMITES) y en paper se simula la orden.

## Aprobación por excepción

Una propuesta con el stop más lejos que el máximo automático queda en `requiere_aprobacion`, sin
decisión final, hasta que el Operador la aprueba o la rechaza (canal de Telegram, M5). Un cambio
de reglas también requiere aprobación y se prueba antes contra escenarios históricos (M6).

## Botón de apagado

El Operador puede apagar el bot: cancela las órdenes abiertas y bloquea las nuevas en menos de
1 minuto (criterio 2.3, M5).
