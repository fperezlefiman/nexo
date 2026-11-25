# nexo

[![tests](https://github.com/fperezlefiman/nexo/actions/workflows/test.yml/badge.svg)](https://github.com/fperezlefiman/nexo/actions/workflows/test.yml)

Orquestador personal de agentes LLM. Reemplaza al agente que uso todos los días sobre el servidor
HTTP de [OpenCode](https://opencode.ai), con estado durable propio, pausa/reanudación de turnos,
memoria recuperada por significado, skills compartidas y versionadas, y canales propios.

> **Estado: F0–F7 implementados y en uso diario.** Siete perfiles corren sobre `nexo` con tráfico real. La suite
> corre contra un Postgres de descarte (`./scripts/test.sh`) y el repo trae un workflow de GitHub
> Actions que la corre sin el motor. Los contract tests en vivo se saltean solos si el motor no
> responde o si se agota la cuota del modelo gratuito. Pendiente declarado: aprobaciones de tools.

## Qué resuelve

Corro seis perfiles de agente (carrera, entrenamiento, finanzas, freelance, compras, trámites) en un
homelab Linux, multiplexados detrás de Telegram. El gateway que uso funciona, pero tiene tres
límites que me obligan a reemplazarlo:

1. **La memoria es texto plano.** Recupera por coincidencia léxica, no por significado: lo que
   anoté con otras palabras no aparece.
2. **No hay pausa.** Un turno largo corre hasta terminar o hasta que lo mato, y lo que quedó a
   medias no se puede retomar sin repetir el trabajo.
3. **Las skills no están versionadas ni compartidas.** Cada perfil tiene su copia y divergen en
   silencio.

nexo pone el valor propio en esas tres cosas más dos: **canales** (Telegram con línea de progreso,
comandos, adjuntos y multi-bot) y **contabilidad de costos por turno**. El loop del agente, las tools
y el acceso a modelos los sigue resolviendo OpenCode: no los reimplemento
([ADR 0001](docs/decisions/0001-opencode-como-motor.md)).

## Stack

| Capa | Elección | Por qué |
|---|---|---|
| Motor de agente | `opencode serve` (HTTP, local) | loop, tools, terminal, proveedores y modelos ya resueltos |
| Orquestador | Python 3.11+ · `psycopg` 3 · `urllib` de stdlib | el 80 % es I/O y SQL; sin framework web porque no hay endpoints públicos (ADR 0009) |
| Base de datos | Postgres 17 + `pgvector` | cola con `SKIP LOCKED`, híbrida vector+FTS, ledger SQL |
| Embeddings | `sentence-transformers` + `multilingual-e5-large` (1024d, CPU) | corpus en español; costo marginal 0 y nada sale de la casa (ADR 0021) |
| Canal | Telegram Bot API (long polling) + CLI | sin IP pública, sin infraestructura nueva |
| Procesos | Docker (Postgres) + systemd | homelab, un solo operador |

Justificación completa y alternativas rechazadas: [ADR 0003](docs/decisions/0003-postgres-pgvector.md).

## Arquitectura

```mermaid
flowchart LR
  TG[Telegram] --> CH[channel/telegram]
  CLI[CLI] --> CH
  CH --> D[core/dispatch<br/>chat → perfil → conversación]
  D -->|turno encolado| W[worker/turn_executor]
  W --> MP[ModelPolicy]
  W --> MS[(memoria<br/>pgvector)]
  W --> SK[(skills<br/>repo git)]
  MP --> RT[AgentRuntime<br/>port]
  RT --> AD[adapter/opencode]
  AD -->|HTTP local| OC[opencode serve<br/>:49374]
  W --> CL[(model_calls<br/>ledger de costos)]
  W --> CH
  CRON[worker/scheduler] --> D
  IX[worker/indexer] --> EMB[embeddings] --> MS
```

**Principio central:** el servidor de OpenCode es un motor efímero; la verdad está en la base. El
transcript es canónico y la sesión del motor es una caché de contexto descartable
([ADR 0004](docs/decisions/0004-transcript-canonico.md)).

Detalle de capas, contratos y modelo de datos: [docs/architecture.md](docs/architecture.md).

## Decisiones

| # | Decisión | Estado |
|---|---|---|
| [0001](docs/decisions/0001-opencode-como-motor.md) | OpenCode server como motor; no se reimplementa el loop | aceptado |
| [0002](docs/decisions/0002-adapter-contrato-propio.md) | Adapter con contrato propio + snapshot OpenAPI | aceptado |
| [0003](docs/decisions/0003-postgres-pgvector.md) | Postgres 17 + pgvector en vez de SQLite | aceptado |
| [0004](docs/decisions/0004-transcript-canonico.md) | Transcript propio canónico; sesión del motor descartable | aceptado |
| [0005](docs/decisions/0005-maquina-de-estados-turno.md) | Turnos con máquina de estados y attempts para pausar | aceptado |
| [0006](docs/decisions/0006-politica-de-modelos.md) | Política de modelos por clase de tarea + circuit breaker | aceptado |
| [0007](docs/decisions/0007-embeddings-locales.md) | Embeddings multilingües locales (1024d) | aceptado |
| [0008](docs/decisions/0008-busqueda-hibrida.md) | Búsqueda híbrida vector + full-text con RRF | aceptado |
| [0009](docs/decisions/0009-telegram-long-polling.md) | Telegram por long polling, sin endpoint público | aceptado |
| [0010](docs/decisions/0010-jobs-en-tabla.md) | Jobs en tabla con `SKIP LOCKED` | aceptado |
| [0011](docs/decisions/0011-permisos-con-aprobacion.md) | Permisos de tools con aprobación humana por chat | aceptado |
| [0012](docs/decisions/0012-coexistencia-dos-sistemas.md) | Coexistencia: un bot por sistema, un dueño por job | aceptado |
| [0013](docs/decisions/0013-jobs-script-y-prompt.md) | Jobs con dos clases: `script` y `prompt` | aceptado |
| [0014](docs/decisions/0014-navegador-playwright.md) | Navegador Playwright como comando del sistema | aceptado |

## Documentación

- [docs/architecture.md](docs/architecture.md) — capas, contratos, modelo de datos, límites.
- [docs/operations.md](docs/operations.md) — entornos, alertas con runbook, costos.
- [docs/evals.md](docs/evals.md) — dataset, métricas y umbrales de la memoria.
- [docs/decisions/](docs/decisions/README.md) — índice de ADRs agrupado por tema.

## Cómo se corre

```bash
mkdir -p ~/.config/nexo && chmod 700 ~/.config/nexo
printf 'postgresql://nexo:CLAVE@127.0.0.1:5546/nexo' > ~/.config/nexo/database_url
chmod 600 ~/.config/nexo/database_url                        # la credencial vive acá, nunca en el código

python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # psycopg entra solo
.venv/bin/nexo health                                        # ¿está el motor?
.venv/bin/nexo ask "Responde solo: OK"                       # un turno completo
.venv/bin/python -m pytest -q                                # suite offline + contrato en vivo
./scripts/test.sh -q                                         # suite completa, incluida la cola
```

El navegador es opcional: `pip install -e '.[browser]'` y `playwright install chromium`. Sin eso,
`nexo browse` falla con un mensaje claro y el resto funciona.

La URL de la base se toma de `NEXO_DATABASE_URL` o del archivo de arriba (o de la ruta que indique
`NEXO_DATABASE_URL_FILE`). No hay valor por defecto: si falta, el proceso no arranca.

**Perfiles y skills.** Los perfiles son datos tuyos, no del repo: viven en la base (`profiles`,
`channels`) y sus skills en un directorio privado que se suma al público. `skills/shared/` trae
herramientas genéricas y `skills/.template/` un ejemplo; tus skills de perfil van en
`$NEXO_SKILLS_PRIVATE` (por defecto `~/.local/share/nexo/skills-private`). `nexo skills sync` indexa
las dos raíces y `nexo skills deploy` las copia al motor. Ver [skills/README.md](skills/README.md).

Los tests de la cola (`SKIP LOCKED`, carrera de claim, leases) necesitan un Postgres real y **no
corren con `pytest` pelado**: sin `NEXO_TEST_DATABASE_URL` se saltean. `./scripts/test.sh` crea la base
descartable `nexo_test`, aplica el esquema y corre todo. Es la forma de correr la suite antes de un
commit; `pytest` pelado sirve para iterar rápido. El workflow de GitHub Actions replica esto con un
servicio `pgvector/pgvector:pg17` y sin motor.

El servidor de OpenCode se levanta aparte (`opencode serve`) y **nunca** se expone fuera de
loopback. Los tests en vivo se saltean solos si el motor no responde. Ver
[docs/operations.md](docs/operations.md).

## Fuera de alcance

Multi-usuario, registro, facturación, interfaz web, fine-tuning de modelos, reemplazo big-bang del
sistema anterior, y cualquier endpoint público. Es un sistema de un solo operador con N perfiles.

## Reglas globales (todos los perfiles)

Viven en `src/nexo/rules.py` y `dispatch.system_prompt_for()` las inyecta en **todos** los perfiles,
después del SOUL y antes del bloque `## Entorno` (ADR 0019). No son una skill: las skills se cargan a
demanda y estas reglas no pueden ser opcionales.

Cubren: idioma (español neutro, tuteo, sin voseo), economía de salida (sin preámbulo, 8 líneas por
defecto, sin tablas en Telegram), veracidad (no inventar datos, mostrar evidencia, distinguir verificado
de supuesto), acciones (confirmación antes de algo destructivo), implementación (`ponytail`) y UI/UX
(`impeccable`), y formato de fechas y números.

Cambiar una regla es un cambio de código con test: `tests/test_rules_offline.py` verifica que estén
todas las que se pidieron.

## Convenciones del repositorio

- **Documentación en español**: README, `docs/`, ADRs, diagramas, roadmap.
- **Todo lo demás en inglés**: identificadores, comentarios, docstrings, nombres de archivos y de
  tests, variables de entorno, comandos de CLI y su salida, mensajes de log y de commit.
- **Única excepción**: el texto que ve el usuario final (respuestas del bot, mensajes de error que se
  publican en Telegram) va en español, porque es el idioma del producto. El idioma de las respuestas se
  fuerza en el bloque `## Entorno` del prompt (`NEXO_RESPONSE_LANGUAGE`), no en el SOUL de cada perfil:
  el modelo derivó al chino una vez y una regla por perfil no lo habría evitado.
- Los ADRs son **inmutables**: cambiar de opinión es un ADR nuevo que reemplaza al anterior.
- Ninguna capacidad central depende de un endpoint marcado `experimental` del motor.
