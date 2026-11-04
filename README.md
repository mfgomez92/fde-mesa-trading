# fde-mesa-trading

Proyecto integrador de Matías Gómez para el curso **Forward Deployed Engineer** (Coderhouse, 2026). El cliente es el equipo de inversiones de una fintech argentina (nombre reservado): un bot que opera una cuenta **paper** (simulada) con un LLM que propone, reglas de riesgo en código y una bitácora que mide los KPIs.

## Qué es este repo (y qué no)

Es la **versión del curso** del proyecto: tiene solo lo necesario para cumplir y revisar cada pre-entrega (código, tests, PDF, autoevaluación y evidencia). El desarrollo completo vive en un repo privado, del que este se publica con un proceso que copia una lista cerrada de archivos y frena la publicación si encuentra rutas internas, claves, mails o teléfonos reales, o datos de la mesa.

El código está en la raíz y se va actualizando con cada PE; cada entrega queda en `entregas/PEN/`, con su etiqueta `peN`.

**No incluye:**

- datos internos de la empresa ni de la mesa (documentos, exports, mandato, estrategias, nombres de personas);
- claves, el `.env` ni el número de la cuenta paper (se muestra como `PA***`);
- las funciones de producto que no son parte de las consignas;
- el bot operando en paper (operador, despliegue en AWS, vigía y runbook)
- el backtest y sus datos
- el corpus real de la mesa y las preguntas y la medición hechas sobre él: el PDF, la autoevaluación y la evidencia de la PE3 son una versión pública con las mismas métricas (el PDF completo se entregó por la plataforma)
- el detector con la estructura interna de la mesa y el script que la redacta
- la entrevista y la ficha del caso (`caso/`)

| Pre-entrega | Etiqueta | Qué revisar |
|---|---|---|
| PE2 · Motor LLM base y validación estructurada | `pe2` | `src/motor.py`, el PDF y la evidencia en `entregas/PE2/` |
| PE3 · RAG con permisos y búsqueda híbrida | `pe3` | `src/rag/`, `tests/test_rag.py`, la autoevaluación y la evidencia en `entregas/PE3/` |

## Motor LLM base de la mesa (PE2)

Recibe una foto del mercado (SPY en velas de **Alpaca, feed IEX**, con EMAs, RSI y ATR) y una consulta del operador. Se la pasa a un modelo real (Anthropic, con OpenAI de fallback), valida la respuesta con Pydantic y **decide el riesgo en código**. Registra todo en una bitácora: tokens, costo en USD, latencia y las decisiones con hora.

```
consulta ──► Entrada (Pydantic) ──► prompt (plantilla) ──► LLM ──► Salida (Pydantic) ──► reglas de riesgo (código) ──► bitácora
                 │ inválida                                   │ inválida: 1 reintento con el campo que falló
                 ▼                                            ▼ falla otra vez / error de API
          error estructurado                           error estructurado (causa operativa)
```

Desde el 30/10 el motor también puede **calcular la señal en el código** (cruce de EMAs + RSI + ATR) y usar el LLM solo para confirmarla o vetarla con motivo ([por qué](docs/senal_en_codigo.md)): es la lección de la PE2 (la confianza que reporta el modelo cambia entre corridas).

## RAG con permisos y búsqueda híbrida (PE3)

```
corpus/ + permisos.json ─► ingestar() ─► fragmentos con documento, sección y roles ─► ChromaDB
consulta + usuario ─► buscar() ─► where={rol_<rol>: true} en la vectorial Y en el BM25 ─► RRF ─► top k
Entrada (PE2) + usuario ─► responder() ─► sin evidencia: requiere_humano ─ con evidencia: LLM ─► citas verificadas
```

| Pieza | Archivo | Qué hace |
|---|---|---|
| Permisos | `src/rag/permisos.py` | Roles de la PE1 (Operador y Lector), un metadato booleano por rol y el `where` de ChromaDB |
| Documentos | `src/rag/documentos.py` | Lee Markdown, texto, HTML, PDF, CSV, JSONL y JSON; limpia, pasa las tablas a texto, aplica `permisos.json` y rechaza documentos con rutas internas |
| Conversores | `src/rag/conversores.py` | Exports (CSV, JSONL) y JSON anidado a Markdown: un título por registro, citable y con su id para BM25 |
| Fragmentado | `src/rag/fragmentado.py` | `estructural` (por títulos, 900 caracteres, solape de una oración) y `fijo` (300 caracteres), con prefijo de documento y sección |
| Embeddings | `src/rag/embeddings.py` | `paraphrase-multilingual-MiniLM-L12-v2` local (fastembed) y uno falso para los tests |
| Búsqueda | `src/rag/busqueda.py` | Vectorial + BM25, fusionadas con RRF (k=60), las dos con el filtro de rol en la consulta a la base |
| Respuesta | `src/rag/respuesta.py` | `SalidaConFuentes` hereda de `Salida` (PE2) y suma `respuesta`, `fuentes` y `requiere_humano` |
| Medición | `src/rag/medicion.py` | Recall@k y MRR de las preguntas de prueba, con dos fragmentados y dos modos |

**Datos:** La medición del PDF se hizo sobre el corpus real de la mesa, que no se publica. Acá el RAG corre con un corpus de demo (`corpus/demo/`). El corpus de demo son documentos propios del proyecto (KPIs, criterios de aceptación, proceso TO-BE, datos de Alpaca y reglas de riesgo) con su `permisos.json`: dos son solo del Operador, así que se puede probar el filtro por rol. Las 10 preguntas de prueba y su medición son sobre el corpus real, así que `python -m src.rag medir` no corre acá: los resultados están en el PDF y la evidencia. Los tests del RAG arman su propio corpus en una carpeta temporal y corren completos; el único que necesita el corpus real está marcado `solo_full` y se saltea.

## Estructura

```
fde-mesa-trading/
├── README.md             este archivo
├── requirements.txt      dependencias (pip), con las mismas versiones que el proyecto
├── pyproject.toml        metadatos y configuración de pytest
├── .env.example          variables de entorno, sin valores
├── .gitignore            incluye .env
├── entregas/PEN/         PDF, autoevaluación y evidencia de cada pre-entrega
├── src/
│   ├── motor.py          el motor y la línea de comandos (punto de entrada)
│   ├── contratos.py      clases Entrada y Salida (el contrato de datos)
│   ├── prompts.py        plantillas de prompt, separadas del código
│   ├── proveedores.py    Anthropic, OpenAI y un LLM simulado detrás de una misma interfaz
│   ├── riesgo.py         reglas de riesgo en código
│   ├── senal.py          la señal de la estrategia, calculada por el código
│   ├── prompts_veto.py   prompt del veto: el LLM confirma o veta la señal del código
│   ├── bitacora.py       bitácora y cálculo de los KPIs
│   ├── precios.py        precios publicados por modelo
│   ├── datos_mercado.py  velas de Alpaca e indicadores
│   ├── broker.py         claves de Alpaca, candado paper/real, feed y errores
│   ├── rag/              RAG con permisos y búsqueda híbrida (PE3)
├── config/               estrategia y ventanas sin entradas por datos macro
├── corpus/demo/          corpus de demo del RAG + permisos.json (quién lee cada documento)
├── datos/consultas_demo.json   las 5 consultas de la medición de la PE2 (velas reales de SPY)
├── scripts/              armar las consultas y verificar Alpaca
├── docs/                 ficha de KPIs, criterios de aceptación, proceso TO-BE, datos de Alpaca y señal en código
└── tests/                tests con el LLM y Alpaca simulados (no necesitan claves ni red)
```

## Cómo correrlo desde un clon limpio

Necesitás Python 3.12 o superior y git.

### WSL2 (Ubuntu en Windows)

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git   # si no los tenés
git clone https://github.com/mfgomez92/fde-mesa-trading.git
cd fde-mesa-trading
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env        # completá BOT_ANTHROPIC_API_KEY, ALPACA_API_KEY y ALPACA_SECRET_KEY (y OPENAI_API_KEY si querés fallback)
```

Trabajá dentro del sistema de archivos de Linux (`~/...`), no en `/mnt/c/...`: es mucho más rápido.

### macOS

```bash
brew install python@3.12 git      # si no los tenés (https://brew.sh)
git clone https://github.com/mfgomez92/fde-mesa-trading.git
cd fde-mesa-trading
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
open -e .env     # completá BOT_ANTHROPIC_API_KEY, ALPACA_API_KEY y ALPACA_SECRET_KEY
```

### Windows (PowerShell, sin WSL)

```powershell
git clone https://github.com/mfgomez92/fde-mesa-trading.git
cd fde-mesa-trading
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
notepad .env
$env:PYTHONUTF8 = 1   # la consola de Windows no es UTF-8 por defecto y la salida usa tildes y ≥
```

### Comandos (desde la raíz del repo, con el entorno activado)

| Comando | Qué hace | ¿Necesita clave? |
|---|---|---|
| `python -m pytest` | corre los tests | No |
| `python -m src.motor --simulado` | corre las 5 consultas con un LLM falso | No |
| `python -m src.motor` | corre las 5 consultas con el modelo real e imprime la tabla de medición | Sí (LLM) |
| `python -m src.motor --demo-errores` | muestra una entrada inválida, un reintento y un error estructurado | Sí (LLM) |
| `python -m scripts.armar_consultas_demo` | vuelve a armar las consultas con las velas más recientes de SPY | Sí (Alpaca) |
| `python -m scripts.verificar_alpaca` | prueba los feeds IEX y SIP y la conexión a la cuenta paper | Sí (Alpaca) |
| `python -m src.rag ingestar --carpeta corpus/demo` | ingesta el corpus de demo en ChromaDB (`indice/`) | No (baja el modelo de embeddings la primera vez, ~220 MB) |
| `python -m src.rag buscar "¿cuál es el stop máximo?" --rol lector` | búsqueda híbrida filtrada por rol (`--modo vectorial` o `bm25` para comparar) | No |
| `python -m src.rag responder "¿cuál es el stop máximo?" --rol operador` | respuesta con fuentes o `requiere_humano` | Sí (LLM); `--simulado` sin clave |

La bitácora queda en `bitacora/bitacora.jsonl`, con una fila JSON por decisión.

## Las claves

- **Anthropic:** se crea en https://platform.claude.com → **Settings → API Keys** (hace falta cargar créditos en **Settings → Billing**). Va en `BOT_ANTHROPIC_API_KEY`.
- **Alpaca (cuenta paper, gratis):**
  1. Creá una cuenta en https://alpaca.markets. Para la cuenta paper alcanza con un email: no hace falta fondearla ni verificar identidad.
  2. En https://app.alpaca.markets elegí la cuenta **Paper** (selector arriba a la izquierda).
  3. En el panel de la derecha, **API Keys → Generate New Keys**. El secreto se muestra una sola vez.
  4. Copialas en `ALPACA_API_KEY` y `ALPACA_SECRET_KEY`.
- Las claves van **solo** en `.env`, que está en `.gitignore`. El repo tiene `.env.example`, con los nombres de las variables y sin valores. Nunca se imprimen ni se registran en la bitácora. El número de cuenta de Alpaca se muestra como `PA***`.

### Variables de Alpaca

| Variable | Valor por defecto | Para qué |
|---|---|---|
| `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` | — | claves de la cuenta paper |
| `ALPACA_PAPER` | `true` | el candado: con `false` el bot **no arranca**, salvo que también esté `ALPACA_LIVE_CONFIRMADO=si`, que no se usa durante el curso |
| `ALPACA_FEED` | `iex` | feed de velas: `iex` (tiempo real y gratis) o `sip` (consolidado; en el plan gratuito, solo con más de 15 minutos de atraso) |

## Datos de mercado

Las velas salen de la API de datos de Alpaca (`StockHistoricalDataClient`), con el feed **IEX**, y se filtran a la rueda regular de Nueva York (9:30 a 16:00). En la PE2 no se mandan órdenes: de la API de trading solo se usa `get_account()` para verificar la conexión. Qué da el plan gratuito, y qué feed se usa para operar y cuál para backtests, está en [`docs/datos_alpaca.md`](docs/datos_alpaca.md).

## Decisiones

- **El modelo propone y el código decide.** `Salida` valida la forma y la coherencia (por ejemplo, en una compra `stop < entrada < objetivo`). `riesgo.py` aplica los límites: relación riesgo/beneficio, distancia de la entrada y stop máximo automático.
- **Dos capas de validación.** Al proveedor se le pasa un JSON Schema "de forma" (campos, tipos y Enum). Las reglas (rangos, patrones y validación cruzada) las verifica Pydantic en nuestro código, así que no dependen de que el proveedor las respete.
- **Un reintento**, que le devuelve al modelo los campos que fallaron. Si vuelve a fallar, devuelve un error estructurado y la propuesta se descarta con categoría `operativo`, que es lo que mide el KPI de negocio.
- **Sin dependencia de un único proveedor.** Si la API de Anthropic falla (429, sin conexión, 5xx), el motor prueba con OpenAI. En Anthropic también está activado el fallback del servidor, por si un filtro de seguridad rechaza el pedido.
- **Presupuesto de tokens por ciclo**, configurable. Si se agota, no se reintenta.
- **Modelo por defecto: `claude-haiku-5-5`** (`effort=low`). En la medición de la PE2 (velas de Alpaca) validó 5 de 5 consultas, igual que `claude-opus-5-5`, a 1/32 del costo (0,00035 contra 0,0112 USD por consulta) y con un 35 % menos de latencia. Opus fue más conservador con la confianza y el código rechazó sus tres señales; Haiku tuvo dos aprobadas. Se cambia con `MODELO_ANTHROPIC`. Evidencia en `entregas/PE2/evidencia/`.

## Equivalencias con TypeScript

| Python | TypeScript |
|---|---|
| `class Entrada(BaseModel)` con `Field(gt=0)` | `z.object({ precio: z.number().positive() })` (Zod) |
| `Entrada.model_validate(datos)` | `Entrada.parse(datos)` |
| `ValidationError.errors()` | `ZodError.issues` |
| `Protocol` (`ProveedorLLM`) | `interface` |
| `async def` / `await` / `asyncio.run(main())` | `async function` / `await` / llamar a `main()` en el top level |
| `pytest` + `pytest-asyncio` | Jest o Vitest |
| `python-dotenv` | `dotenv` |
| `Decimal("771.89")` | `new Decimal("771.89")` (decimal.js) |
| inyectar `cliente=ClienteFalso()` en los tests | pasar un mock en vez de `vi.mock` |
| `.venv` + `requirements.txt` | `node_modules` + `package.json` |
