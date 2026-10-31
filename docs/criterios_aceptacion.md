# Criterios de aceptación declarativos

Responde al punto 2 de la devolución de la PE1: los 9 criterios de las historias de usuario, escritos como «Dado / cuando / entonces», cada uno con su test. Los que dependen de piezas que todavía no existen quedan marcados con el módulo donde se cubren.

## HU2 · Operador: órdenes dentro de límites solas; fuera de límites, con aprobación

| # | Criterio | Test | Estado |
|---|---|---|---|
| 2.1 | **Dado** una propuesta válida que cumple todas las reglas de riesgo, **cuando** el código la evalúa, **entonces** el sistema la aprueba sin intervención humana y registra la decisión final con hora | `test_riesgo.py::test_dentro_de_limites_se_aprueba_sola`, `test_motor.py::test_salida_valida_se_aprueba_y_queda_en_la_bitacora` | ✅ PE2 (la orden se simula; el envío al bróker es de la semana 4) |
| 2.2 | **Dado** una propuesta con el stop más lejos que el máximo automático, **cuando** el código la evalúa, **entonces** el sistema la deja en `requiere_aprobacion`, sin decisión final hasta que decide una persona | `test_riesgo.py::test_stop_demasiado_lejos_requiere_aprobacion`, `test_motor.py::test_requiere_aprobacion_queda_sin_decision_final` | ✅ PE2 (la aprobación por Telegram es del M5) |
| 2.3 | **Dado** órdenes abiertas, **cuando** el operador aprieta el botón de apagado, **entonces** el sistema las cancela y bloquea las nuevas en menos de 1 minuto | — | M5 |
| 2.4 | **Dado** una salida del modelo que no cumple el contrato, **cuando** falla también el reintento, **entonces** el sistema no opera, devuelve un error estructurado con el campo que falló y lo registra como causa operativa | `test_motor.py::test_dos_salidas_invalidas_devuelven_error_estructurado` | ✅ PE2 (criterio nuevo, sale del riesgo «el modelo responde mal») |
| 2.5 | **Dado** un modelo que propone saltear el riesgo, **cuando** el código evalúa, **entonces** manda la regla de código | `test_riesgo.py::test_el_modelo_no_puede_saltear_el_riesgo` | ✅ PE2 (criterio nuevo, sale de la decisión «el modelo propone, el código valida») |

## HU1 · Operador: probar un cambio contra datos históricos antes de activarlo

| # | Criterio | Test | Estado |
|---|---|---|---|
| 1.1 | **Dado** un cambio de reglas o de prompt, **cuando** el operador lo propone, **entonces** el sistema lo prueba contra los escenarios históricos y genera un reporte de antes y después | — | M6 (evals) |
| 1.2 | **Dado** un cambio probado, **cuando** nadie lo aprobó, **entonces** el sistema lo mantiene inactivo | — | M5–M6 |
| 1.3 | **Dado** cualquier decisión, **cuando** se registra, **entonces** la bitácora guarda la versión de reglas y de prompt con su autor y su hora | `test_bitacora.py::test_la_bitacora_guarda_y_lee_con_hora_y_versiones` | ✅ PE2 (parcial: falta guardar la versión anterior al aprobar un cambio, M6) |

## HU3 · Lector: reporte diario contra benchmarks

| # | Criterio | Test | Estado |
|---|---|---|---|
| 3.1 | **Dado** un día hábil, **cuando** son las 19:00 ART, **entonces** el sistema ya envió el reporte | — | M8 |
| 3.2 | **Dado** el reporte, **cuando** se genera, **entonces** muestra el rendimiento y el riesgo de cada cartera en el mismo período | — | M9 |
| 3.3 | **Dado** un usuario con rol Lector, **cuando** intenta aprobar una orden o cambiar reglas, **entonces** el sistema lo rechaza | — | M7 |

## KPIs

| Criterio | Test |
|---|---|
| **Dado** la bitácora de una ventana, **cuando** se calcula el KPI técnico, **entonces** cuenta solo las propuestas con decisión de riesgo y final dentro de la ventana, y marca como no concluyente una muestra menor a la mínima | `test_bitacora.py::test_kpi_tecnico_cuenta_solo_propuestas_completas_de_la_ventana`, `test_kpi_sin_muestra_suficiente_no_es_concluyente` |
| **Dado** decisiones finales con motivo, **cuando** se calcula el KPI de negocio, **entonces** cuenta solo las frenadas por causa operativa | `test_bitacora.py::test_kpi_negocio_separa_causas_operativas_de_riesgo` |
