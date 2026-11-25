# Arquitectura

Documento de referencia del sistema. El README es la puerta de entrada; acá vive el detalle.

## 1. Contexto

```
                    ┌──────────────────────────────────────────────┐
  operador ───────► │  Telegram (un grupo por perfil)              │
  (operador)        │  CLI local                                   │
                    └───────────────────────┬──────────────────────┘
                                            │
                    ┌───────────────────────▼──────────────────────┐
                    │  nexo (homelab, un solo host)                │
                    │  dispatch · workers · memoria · skills       │
                    └───────┬─────────────────────────┬────────────┘
                            │                         │
              ┌─────────────▼─────────┐   ┌───────────▼─────────────┐
              │ opencode serve        │   │ Postgres 17 + pgvector  │
              │ 127.0.0.1:49374       │   │ (session, transcript,   │
              │ loop + tools + modelos│   │  memoria, skills, jobs) │
              └───────────┬───────────┘   └─────────────────────────┘
                          │
              ┌───────────▼───────────┐
              │ proveedores de modelos│
              │ (free y pagos)        │
              └───────────────────────┘
```

Restricciones reales:

- **Un host, sin costo de nube.** Docker para Postgres, systemd para los procesos, un solo operador.
- **El motor no es nuestro y su API está marcada experimental** (version 0.0.1). Requiere
  autenticación: `/openapi.json` responde `401 Authentication required`.
- **Presupuesto de modelos:** plan con allowance mensual y multiplicador horario, más modelos
  gratuitos que solo funcionan a través del cliente/servidor de OpenCode (no por API pública).
- **Volumen esperado:** ~480 llamadas/día, ~34 M tokens de input y 1.800 M de cache read por mes.
  Datos en el orden de megabytes, no gigabytes.

## 2. Principio rector

> El servidor de OpenCode es un motor efímero. La verdad está en nuestra base.

| Pieza | Dueño | Si se pierde |
|---|---|---|
| Transcript (`messages`) | nosotros, append-only | pérdida inaceptable → backup y restauración probada |
| Estado de turnos (`turns`, `turn_attempts`) | nosotros | turno `expired` → reconciliador al arrancar |
| Sesión del motor (`opencode_session_id`) | motor | se descarta y se reconstruye desde el transcript |
| Memoria (`memory_items`) | nosotros | reindexable desde el transcript |
| Skills (repo git) | nosotros | re-indexables |
| Ejecución de tools | motor | se reintenta el turno |

La sesión del motor es **caché de contexto**, no identidad: se guarda junto a un `context_epoch` que
sube cada vez que reconstruimos el contexto (compactación, replay, fork). Un upgrade que invalide
sesiones degrada la conversación a "recontextualizar en el próximo turno", con su costo medido.

> **Verificado 2026-11-24:** las sesiones del motor **sí persisten** a un reinicio
> (`~/.local/share/opencode/opencode.db`, 29 sesiones en disco). El evento de pérdida no es un
> reinicio, es un upgrade o una migración de esquema del motor. La decisión no cambia; la
> probabilidad del riesgo baja. Ver `docs/migration.md`.

## 3. Capas y contratos

```
channels/   telegram · cli           (L4)
workers/    turn_executor · indexer · scheduler   (L4)
core/       dispatch · turn state machine · model policy · budget · permissions   (L3)
ports/      AgentRuntime · MemoryStore · SkillRegistry · ModelPolicy · Channel     (L1)
adapters/   opencode · telegram · embeddings · postgres                            (L2)
```

Regla de dependencia: `channels`/`workers` → `core` → `ports` ← `adapters`. **Ningún módulo de
dominio importa un adapter ni `httpx`.** Es lo que permite testear el núcleo sin red y reemplazar el
motor sin tocar el dominio.

### 3.1 `AgentRuntime` (port)

```
open_session(profile, model) -> SessionHandle
prompt(handle, payload) -> AsyncIterator[Event]   # delta | tool | permission | usage | end | error
interrupt(handle)
set_model(handle, model)
compact(handle) -> CompactionResult
context(handle) -> ContextStats
fork(handle) -> SessionHandle
revert(handle, checkpoint)
reply_permission(handle, request_id, decision)
health() -> RuntimeHealth
```

Taxonomía de errores propia (el adapter traduce; el dominio no ve códigos HTTP):

```
RuntimeUnavailable · AuthFailed · SessionLost · ModelRateLimited · ModelUnavailable
ContextOverflow · PermissionRequested · ToolFailed · Unknown
```

### 3.2 Otros contratos

```
MemoryStore.write(items) / search(query, profile_id, k, filters) / forget(id)
SkillRegistry.sync(repo_ref) -> SyncReport / resolve(query, profile_id, k)
ModelPolicy.pick(task_class, profile, budget_state) -> ModelRef     # determinista y auditable
Channel.inbound() -> AsyncIterator[InboundMessage] / send / update / typing
Clock, IdGen                                                          # inyectables, para tests
```

## 4. Flujo de un turno

1. **Entrada.** `channel/telegram` recibe un update; descarta remitentes fuera de la allowlist.
2. **Dispatch.** Resuelve `chat_id → profile → conversation` (creándola si es nueva) y calcula
   `idempotency_key = sha256(channel_id, external_message_id)`. Inserta el turno en `queued`.
3. **Claim.** Un worker toma el turno con `FOR UPDATE SKIP LOCKED`, escribe `turn_attempts` y renueva
   el lease cada 15 s.
4. **Contexto.** `ModelPolicy` elige modelo; `SkillRegistry` resuelve skills del perfil;
   `MemoryStore.search` recupera k memorias; se arma el payload en orden **estable**:
   `system + skills` → memoria → historial reciente → mensaje nuevo.
5. **Ejecución.** `adapter/opencode` abre (o reutiliza) sesión y consume eventos. Los deltas se
   agrupan (~800 ms) y se editan en Telegram. Un `permission` pasa el turno a `awaiting_permission`
   y se pregunta por chat; timeout 120 s → deny.
6. **Cierre.** Se persisten mensajes, uso de tokens y costo en `model_calls`, y el turno pasa a
   `completed`. Los hechos durables detectados se encolan para el indexer.

Si el proceso muere, el lease expira, el reconciliador devuelve el turno a `queued` (o a `paused` si
ya hubo output parcial) y se registra un attempt nuevo.

## 5. Modelo de datos

### 5.1 Núcleo

```sql
profiles(id, name UNIQUE, system_prompt, default_model, settings jsonb, created_at)
channels(id, kind, external_id, profile_id, enabled, created_at)
conversations(id, profile_id, channel_id, title, state, context_epoch int, created_at, last_turn_at)

turns(id, conversation_id, seq, state, idempotency_key UNIQUE, input_text,
      attempt_no, created_at, started_at, ended_at)

turn_attempts(id, turn_id, state, model, provider, opencode_session_id, opencode_run_id,
      output_text, reason, tokens_in, tokens_out, tokens_cache_read, cost_usd,
      latency_ms, error_class, error_detail, started_at, ended_at)

messages(id, conversation_id, turn_id, role, content, tool_calls jsonb, token_est, created_at)
model_calls(id, turn_id, attempt_id, phase, model, provider, tokens_in, tokens_out,
      tokens_cache_read, cost_usd, latency_ms, status, created_at)
permission_requests(id, attempt_id, request_id, tool, args jsonb, state, decided_at, decided_by)
```

- `messages` es **append-only**: no se actualiza; corregir es un turno nuevo con `parent_turn_id`.
- `turns` (lógico) ≠ `turn_attempts` (físico). Esa separación es lo que permite pausar, reintentar y
  comparar modelos sin reescribir historia.

### 5.2 Memoria

```sql
memory_items(id uuid pk, profile_id, kind,          -- fact | preference | decision | event
  content text NOT NULL, embedding vector(1024) NOT NULL,
  fts tsvector GENERATED ALWAYS AS (to_tsvector('spanish', content)) STORED,
  source_turn_id, importance smallint, last_used_at, use_count int, metadata jsonb,
  created_at, deleted_at)

CREATE INDEX ON memory_items USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON memory_items USING gin (fts);
CREATE INDEX ON memory_items (profile_id, deleted_at) WHERE deleted_at IS NULL;
```

Puntuación híbrida por *reciprocal rank fusion*:

```
score = 1/(60+rank_vec) + 1/(60+rank_fts) + 0.05 * importance_norm
```

### 5.3 Skills y jobs

```sql
skills(id, slug, title, path, content_hash, frontmatter jsonb, last_synced_at)
skill_chunks(id, skill_id, seq, content, embedding vector(1024))

jobs(id, kind, profile_id, payload jsonb, schedule text, next_run_at, state, attempts,
     max_attempts, locked_until, last_error, created_at)
job_runs(id, job_id, started_at, ended_at, status, output, error, turn_id)
```

### 5.4 Índices y retención

| Necesidad | Índice | Consulta que lo justifica |
|---|---|---|
| leer transcript | `messages(conversation_id, created_at)` | últimos N mensajes para el prompt |
| reconciliar | `turn_attempts(state) WHERE state IN ('running','paused')` | barrido al arrancar |
| panel de costos | vista diaria sobre `model_calls(created_at)` | costo por perfil/modelo del día |
| cola | `jobs(next_run_at) WHERE state='queued'` | tick del scheduler |

Retención: transcript y memoria indefinidos (~MB, es historia personal valiosa); `model_calls` se
agrega a diario y se purga a 180 días **conservando el agregado**; `job_runs` 90 días. La purga
nunca borra la fila que sostiene un agregado (regla: el número sobrevive a la fila).

## 6. Límites del sistema

| Dimensión | Límite v0 | Qué pasa al acercarse |
|---|---|---|
| Conversaciones activas | ~10 | ninguna acción; el sistema es de un operador |
| Turnos/día | ~500 | límite práctico del allowance; alerta de costo |
| Memoria | ~20 k items | p95 de búsqueda < 150 ms con HNSW; reindexar si supera |
| Latencia turno | p95 < 30 s (chat) | alerta si sube sin cambio de código |
| Contexto por turno | ventana del modelo | compactar y subir `context_epoch` |
| Workers | 1–2 | más workers no ayudan: el cuello es el motor |
| Consistencia | fuerte dentro de Postgres | la sesión del motor es eventual y descartable |

## 7. Seguridad

- El motor tiene terminal con los permisos del usuario: es la superficie de riesgo mayor.
- Procesos del motor bajo **usuario dedicado**, con acceso solo al workspace.
- Cada request de permiso se enruta a Telegram; **timeout = deny**.
- Allowlist de `chat_id`; el resto se descarta antes del dispatch.
- El motor escucha en loopback y el orquestador no expone endpoints públicos.
