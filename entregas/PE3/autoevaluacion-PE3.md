# Autoevaluación de la PE3: RAG con permisos y búsqueda híbrida

Entrega: [`FDE_PE3_Gomez_Matias.pdf`](FDE_PE3_Gomez_Matias.pdf) (3 páginas, **versión pública**) y la evidencia en `evidencia/`. El código está en `src/rag/`. Tag `pe3`.

> **Versión pública.** La medición se hizo sobre el corpus real de la mesa, que no se publica; el PDF completo se entregó por la plataforma. Las métricas son las mismas; los nombres de estrategias, ids, archivos del corpus y datos de operaciones están generalizados. En este repo el RAG corre con un corpus de demo (`corpus/demo/`).

Medición del 03/11/2026 con Haiku 5.5, sobre el corpus real que entregó la mesa el 02/11 (45 documentos anonimizados, 1.208 fragmentos), después de redactar las rutas internas.

## Rúbrica (100 puntos; se aprueba con 70)

| Criterio | Peso | Estado | Evidencia |
|---|---|---|---|
| **Permisos antes de buscar** | 25 % | ✅ Cumple | El `where` de ChromaDB va en la rama vectorial y en el `get` que arma el BM25 (`busqueda.py`). Los tests por rol están en `tests/test_rag.py`: el Lector no recibe nada del documento restringido, ni pidiendo 1.000 resultados ni dentro del prompt (se captura el prompt que recibe el modelo), y el Operador sí lo recibe. Otro test, con un espía, verifica que cada `query` y cada `get` lleven el filtro. Con el corpus real, el Lector recibe 0 de 817 fragmentos de los exports, y con el LLM real no llega nada de los exports al prompt (P09 y P10). |
| **Búsqueda híbrida con evidencia** | 20 % | ✅ Cumple | Vectorial más BM25, fusionados con RRF (k = 60). Hay 3 casos reales, sin fabricar, en los que la híbrida acierta y la vectorial falla: un id de enmienda, un código de rechazo y el id de una orden. En otro la híbrida mejora la posición. El PDF informa también 1 caso en el que fallan las dos (un código de estado), con su causa y, como trabajo futuro, un RRF ponderado. |
| **Ingesta y metadatos** | 15 % | ✅ Cumple | Un comando: `python -m src.rag ingestar`. Cada fragmento lleva documento, título, sección (ruta de títulos), índice, rango de caracteres, hash y un booleano por rol. Lee Markdown, texto, HTML, PDF, CSV, JSONL y JSON; los tres últimos pasan por el conversor determinista que eligió Matías, con 9 tests. |
| **Respuesta con fuentes** | 15 % | ✅ Cumple | `responder(entrada, usuario)` recibe la `Entrada` de la PE2 y devuelve `SalidaConFuentes`, que hereda de `Salida`. Las fuentes las arma el código desde los metadatos y solo valen ids recuperados. Tres compuertas dan `requiere_humano=true`. Con el LLM real, las 2 preguntas sin respuesta se abstienen; las fallas que aparecieron fueron abstenciones con fuentes (del lado seguro), nunca un invento. |
| **Preguntas de prueba y medición** | 15 % | ✅ Cumple | El archivo de preguntas (repo privado, sobre el corpus real) tiene 10 preguntas (6 normales, 2 sin respuesta y 2 restringidas), cada una con su documento y su respuesta esperados. El Recall@3 se calcula sobre las 6 con respuesta, con dos fragmentados por dos modos. Las dos híbridas empatan (6/6) y dan 9/10 con el LLM en la misma corrida; la vectorial sola, 1/6 y 2/6. El PDF informa que el MRR cambió de lado entre corridas (inestable con 6 preguntas). El registro de cada consulta queda en el repo privado; la versión pública de la corrida está en `evidencia/`. |
| **Decisiones** | 10 % | ✅ Cumple | Sección 6 del PDF. Justifica el fragmento de 900 caracteres: ante el empate en las métricas decide lo estable (un 40 % menos de fragmentos y secciones citables); compara con el patrón de la clase y aplica Contextual Retrieval. Explica cuándo alcanza ChromaDB y en qué 4 situaciones del cliente convendría Pinecone. |

## Checklist del docente (notebook de la clase, sección 14)

| Requisito PE3 | Para la entrega | Estado |
|---|---|---|
| Ingesta `ingestar()` | Ingesta automatizada de documentos propios | ✅ Corpus real de la mesa, un comando, idempotente, con conversor de exports |
| `buscar(consulta, usuario, k)` | Modularizar, probar roles y mejorar el ranking | ✅ `src/rag/busqueda.py`, con tests por rol. El ranking mejoró: las tablas pasan a texto y los códigos se tokenizan enteros y en partes |
| `responder(entrada)` | Integrar el motor de la PE2 y tests reales de calidad | ✅ Usa la `Entrada` de la PE2, la abstracción de proveedor y el reintento; además, el veto del motor (`procesar_con_senal`) lee el mandato con este buscador. 9 de 10 con el LLM real en la corrida final (10 de 10 en la anterior); la que falla se abstiene |
| Dos configuraciones | Elegir con métrica y evidencias | ✅ Estructural 900/120 contra fijo 300/0, cada una en modo híbrido y vectorial, comparadas con el LLM en la misma corrida |
| 10 preguntas | Corpus y golden set del caso | ✅ Sobre el corpus real: mandato, estrategias, journal, manuales y exports (generalizadas en la versión pública) |
| 2 ventajas híbridas verificadas | 2 casos reales, sin inventar | ✅ 3: un id de enmienda, un código de rechazo y un id de orden; también se informa el caso que falla |
| PDF | Evidencia, decisiones y trade-offs | ✅ 3 páginas |

## Errores que la slide pide evitar

| Error | Cómo se evita |
|---|---|
| No registrar el documento esperado y los resultados observados | Se registran en `medicion.json`, por configuración y modo |
| Fabricar las consultas en las que gana la híbrida | Se eligieron antes de medir y se informan las 6, incluida la que falla |
| Confundir una cita mostrada con evidencia real | El código arma las fuentes y descarta los ids que el modelo no recibió (`test_cita_inventada_no_cuenta_y_requiere_humano`) |
| No tener un test por rol que pruebe resultados y prompt | `test_lector_sin_permiso_no_recibe_nada_ni_en_el_prompt` y `test_operador_recibe_el_restringido_en_el_prompt` |

## Lo que falta o queda débil

- **La fusión RRF diluye algunos identificadores** (un código de estado). La mejora, pesar más BM25 cuando la consulta es un id, queda para la PE6, con consultas nuevas, para no ajustarla mirando las mismas.
- **El umbral de similitud (0,40) se calibró con pocos casos**, y la compuerta frena solo 1 de las 2 sin respuesta; la otra la frena el modelo. Hay que medirlo con 20 casos o más en la PE6.
- **Diez preguntas sirven para elegir una configuración, no para afirmar una tasa.**
- **Los permisos se leen al ingestar.** Un cambio de roles exige volver a ingestar. En la PE7, con SSO, el rol vendrá del proveedor de identidad.
- **Confidencialidad: resuelto.** Los documentos y exports traían rutas internas del sistema de la mesa. Se redactaron con etiquetas neutras que conservan fecha e ids de negocio, y la ingesta falla si vuelven a entrar (`RutaInternaError`, con tests).
- **Con 6 preguntas, la elección entre fragmentados no es estable:** el MRR cambió de lado entre la corrida previa a la redacción y la final.
- **Transcripción de la clase:** la clase está grabada y la transcripción está pendiente. El documento de estudio usa por ahora las slides, el resumen de la plataforma, los dos notebooks y el script del docente; se completa cuando llegue.

## Cambios que quedaron fuera de la PE3 pero salieron de este módulo

- **La lección de la PE2, aplicada** (`docs/senal_en_codigo.md`): la señal la calcula el código y el LLM confirma o veta con fuentes del RAG. Que el veto sea obligatorio y con falla cerrada a los 20 s, como decidió la mesa, lo toma la sesión del bot en paper.
- **`rag-prompt-v3`**: define qué significa `requiere_humano`. Lo detectó la validación con el LLM.
