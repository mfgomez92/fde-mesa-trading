# fde-mesa-trading

Proyecto integrador de Matías Gómez para el curso **Forward Deployed Engineer** (Coderhouse, 2026). El cliente es el equipo de inversiones de una fintech argentina (nombre reservado): un bot que opera una cuenta **paper** (simulada) con un LLM que propone, reglas de riesgo en código y una bitácora que mide los KPIs. El repo crece con cada pre-entrega.

| Pre-entrega | Etiqueta | Qué revisar |
|---|---|---|
| PE2 · Motor LLM base y validación estructurada | `pe2` | Este README, `src/motor.py`, el PDF y la evidencia en `entregas/PE2/` |

## Motor LLM base de la mesa (PE2)

Recibe una foto del mercado (SPY en velas de 5 minutos de **Alpaca, feed IEX**, con EMAs, RSI y ATR) y una consulta del operador. Se la pasa a un modelo real (Anthropic, con OpenAI de fallback), valida la respuesta con Pydantic y **decide el riesgo en código**. Registra todo en una bitácora: tokens, costo en USD, latencia y las decisiones con hora.

```
consulta ──► Entrada (Pydantic) ──► prompt (plantilla) ──► LLM ──► Salida (Pydantic) ──► reglas de riesgo (código) ──► bitácora
                 │ inválida                                   │ inválida: 1 reintento con el campo que falló
                 ▼                                            ▼ falla otra vez / error de API
          error estructurado                           error estructurado (causa operativa)
```

## Estructura

```
fde-mesa-trading/
├── README.md             este archivo
├── requirements.txt      dependencias (pip)
├── pyproject.toml        metadatos y configuración de pytest
├── .env.example          variables de entorno, sin valores
├── .gitignore            incluye .env
├── entregas/PE2/         PDF de la entrega y salida real de la terminal (evidencia/)
├── src/
│   ├── motor.py          el motor y la línea de comandos (punto de entrada)
│   ├── contratos.py      clases Entrada y Salida (el contrato de datos)
│   ├── prompts.py        plantillas de prompt, separadas del código
│   ├── proveedores.py    Anthropic, OpenAI y un LLM simulado detrás de una misma interfaz
│   ├── riesgo.py         reglas de riesgo en código
│   ├── bitacora.py       bitácora y cálculo de los KPIs
│   ├── precios.py        precios publicados por modelo
│   ├── datos_mercado.py  velas de Alpaca e indicadores
│   └── broker.py         claves de Alpaca, candado paper/real, feed y errores
├── datos/consultas_demo.json   las 5 consultas de la medición (velas reales de SPY, con la fuente y la hora de descarga)
├── scripts/
│   ├── armar_consultas_demo.py   arma las consultas con velas de Alpaca
│   └── verificar_alpaca.py       verifica qué da el plan gratuito
├── docs/                 ficha de KPIs, criterios de aceptación y datos_alpaca.md
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

- **El modelo propone y el código decide.** `Salida` valida la forma y la coherencia (por ejemplo, en una compra `stop < entrada < objetivo`). `riesgo.py` aplica los límites: confianza mínima, relación riesgo/beneficio, distancia de la entrada y stop máximo automático.
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
