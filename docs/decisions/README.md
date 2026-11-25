# Índice de decisiones (ADR)

Un archivo por decisión. **Inmutables**: cambiar de opinión es un ADR nuevo que reemplaza al anterior.
Estados: `propuesto` · `aceptado` · `reemplazado por NNNN`.

Criterio para escribir uno: al menos dos alternativas que un ingeniero tomaría en serio y una
elección que cuesta revertir. Si no cumple eso, es una línea en el documento que corresponda.

## Motor y contrato externo

| # | Decisión | Estado | Dónde se aplica |
|---|---|---|---|
| [0001](0001-opencode-como-motor.md) | OpenCode server como motor; no se reimplementa el loop | aceptado | `architecture.md` §3 |
| [0002](0002-adapter-contrato-propio.md) | Adapter con contrato propio y snapshot del OpenAPI | aceptado | `architecture.md` §3.1 |
| [0022](0022-contrato-recortado-a-lo-implementado.md) | El contrato del motor se recorta a lo que el adapter implementa | aceptado | `ports.py` |

## Datos y estado

| # | Decisión | Estado | Dónde se aplica |
|---|---|---|---|
| [0003](0003-postgres-pgvector.md) | Postgres 17 + pgvector en vez de SQLite | aceptado | `architecture.md` §5 |
| [0004](0004-transcript-canonico.md) | Transcript propio canónico; sesión del motor descartable | aceptado | `architecture.md` §2 |
| [0005](0005-maquina-de-estados-turno.md) | Turnos con máquina de estados y attempts para pausar | aceptado | `architecture.md` §4 |
| [0010](0010-jobs-en-tabla.md) | Jobs en tabla con `SKIP LOCKED` | aceptado | `roadmap.md` F2 |
| [0013](0013-jobs-script-y-prompt.md) | Jobs con dos clases: `script` y `prompt` | aceptado | `migration.md` §1.2 |

## Modelos y recuperación

| # | Decisión | Estado | Dónde se aplica |
|---|---|---|---|
| [0006](0006-politica-de-modelos.md) | Política de modelos por clase de tarea + circuit breaker | aceptado | `architecture.md` §4 |
| [0007](0007-embeddings-locales.md) | Embeddings multilingües locales (1024d) | aceptado | `architecture.md` §5.2 |
| [0008](0008-busqueda-hibrida.md) | Búsqueda híbrida vector + full-text con RRF | aceptado **en parte** — la fusión la reemplaza la 0021 | `evals.md` |

## Canales y seguridad

| # | Decisión | Estado | Dónde se aplica |
|---|---|---|---|
| [0009](0009-telegram-long-polling.md) | Telegram por long polling, sin endpoint público | aceptado | `operations.md` |
| [0011](0011-permisos-con-aprobacion.md) | Permisos de tools con aprobación humana por chat | aceptado | `architecture.md` §7 |

## Migración y herramientas

| # | Decisión | Estado | Dónde se aplica |
|---|---|---|---|
| [0012](0012-coexistencia-dos-sistemas.md) | Coexistencia: un bot por sistema y un dueño por job | aceptado | `migration.md`, `operations.md` |
| [0014](0014-navegador-playwright.md) | Navegador Playwright como comando del sistema | aceptado | `migration.md` §1.5 |
| [0015](0015-skills-repo-unico.md) | Skills: repo git como fuente, el motor las carga, sin inyección | aceptado | `operations.md`, `registry.py` |
| [0016](0016-tools-por-mcp.md) | Tools del agente por servidor MCP, no por fork del motor | aceptado | `operations.md`, `mcp.py` |
| [0017](0017-retiro-shared-memory.md) | Se retira `shared-memory` y queda un solo camino de memoria | aceptado | `docs/importado/`, `operations.md` |
| [0018](0018-canal-multi-bot.md) | Canal multi-bot: identidad por canal, un poller por token | aceptado | `channels/telegram.py` |
| [0019](0019-reglas-globales-en-prompt.md) | Reglas globales inyectadas en el prompt, no como skill | aceptado | `rules.py`, `dispatch.py` |
| [0020](0020-credenciales-fuera-del-codigo.md) | Credenciales fuera del código; la filtrada se rotó | aceptado | `db.py`, `operations.md` |
| [0021](0021-memoria-semantica.md) | Memoria semántica con vectores locales; la fusión híbrida se midió y se descartó | aceptado | `vectors.py`, `evals.py`, `mcp.py` |
| [0023](0023-vision-con-el-motor.md) | `vision_analyze` con el modelo multimodal del motor, no con un proveedor nuevo | aceptado | `vision.py`, `mcp.py` |
| [0024](0024-python-como-tool.md) | Tool `python` con un proceso nuevo por llamada, no un kernel persistente | aceptado | `pytool.py`, `mcp.py` |
| [0025](0025-jobs-one-shot.md) | Jobs one-shot con `run_at`, no cron anual | aceptado | `jobs.py` |
| [0026](0026-jobs-prompt-con-command.md) | Un job `prompt` puede traer `command`: el script alimenta al agente | aceptado | `jobs.py` |
