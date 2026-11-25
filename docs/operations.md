# Operaciones

Entornos, despliegue, alertas con runbook, costos y límites del proveedor. Todo lo de acá se aplica a
un solo host del homelab, sin costo de nube.

## 1. Topología de procesos

| Proceso | Forma | Notas |
|---|---|---|
| Postgres 17 + pgvector | Docker, `restart: unless-stopped` | puerto en `127.0.0.1:5545`, volumen nombrado |
| `opencode-engine` | systemd (usuario **dedicado**) | `127.0.0.1:49374`, con auth, nunca expuesto (`Restart=always`) |
| `nexo worker` | systemd | ejecuta turnos (1 instancia) |
| `nexo channel telegram` | systemd | long polling, un solo consumidor |
| `nexo cron` | systemd | drena `jobs`, tick 10 s |

## Credenciales

La URL de Postgres **no está en el código** (ADR 0020). Se resuelve así, en orden:

1. `NEXO_DATABASE_URL` en el entorno.
2. `~/.config/nexo/database_url` (modo 600), o la ruta de `NEXO_DATABASE_URL_FILE`.

Si ninguna existe, el proceso falla al conectar con un mensaje que dice qué falta. Ningún servicio
pasa la credencial por línea de comandos ni por unidad de systemd: el servidor MCP que el motor lanza
como hijo la lee del mismo archivo.

Rotar la clave: `ALTER USER nexo WITH PASSWORD '...'`, actualizar el archivo y reiniciar los cuatro
servicios. La rotación anterior se verificó comprobando que la clave vieja ya no conecta.

## Memoria semántica

```bash
nexo vectors index     # vectoriza las memorias sin vector (idempotente)
nexo vectors index --force   # re-vectoriza todo (después de cambiar de modelo)
nexo vectors stats
nexo evals             # el gate: recall@5 y MRR contra el set dorado
```

Modelo: `multilingual-e5-large` (1024 dimensiones) en CPU, definido en `NEXO_EMBED_MODEL`. Ocupa
~4,1 GB de caché de HuggingFace y tarda ~1,5 s en cargar en cada proceso que lo use (el servidor MCP
lo carga una vez por sesión del motor). No hay llamada a ningún servicio externo: las memorias no
salen del homelab.

Los prefijos `query:` y `passage:` son obligatorios para este modelo. Sin ellos no falla nada: los
puntajes se degradan en silencio, que es peor.

Si el índice está vacío o el modelo no está instalado, `memory_search` cae a la búsqueda por palabras
en vez de devolver un error. Es cobertura, no una segunda estrategia: la estrategia es coseno.

**Cambiar de modelo** es `--force`, no una migración: la clave de `memory_vectors` incluye el nombre
del modelo, así que ambas versiones conviven mientras se re-indexa.

## Medición de jobs

```bash
nexo metrics    # runs, promedio, máximo, fallos y último disparo por job
```

La duración se mide con `job_runs.started_at` (cuando empezó), no con `scheduled_for` (cuando debía
empezar): la diferencia entre ambos es la espera en cola, y contarla como tiempo de ejecución hace
ilegible cualquier optimización.

**Enviar desde un script de afuera:** `nexo notify <destino> <texto>` usa el canal que corresponde:
`destino` puede ser un perfil (`freelance`), un chat id (`-5476036060`) o `telegram`, que es el chat
del operador (perfil `default`). Es lo que usan el aviso del scout de Workana y los avisos de mediax;
antes entregaban con `hermes send` y, cuando sacaron a ese bot del grupo, los avisos se perdieron en
silencio. El proceso que lo llama necesita `NEXO_TELEGRAM_TOKEN` en su entorno (`EnvironmentFile`).

Estados de un run: `ok`, `failed` (el comando falló) y `delivery_failed` (el comando anduvo pero el
mensaje no llegó a Telegram). Un `delivery_failed` con `telegram refused the message` casi siempre
significa que el bot no está en el grupo.

## Backups

**Y además hay copia fuera del disco.** El backup diario de Google Drive (`~/homelab/gdrive-backup/backup.sh`,
05:00) ahora incluye: el dump de la base de nexo (`db/nexo-YYYYMMDD.sql.gz`), la configuración del
despliegue (`.env`, `~/.config/nexo/`) y el archivo de cada perfil (`hermes-profiles/<perfil>/` con
`SOUL.md`, `memories/` y `legal/`, sin `state.db`). Verificado el 2026-11-24: **2,6 MB** de dump y
**13,2 MiB** de perfiles en `gdrive:homelab-backup/`, y una restauración completa en base temporal
(5 perfiles, 9.590 mensajes, 157 memorias, 14 jobs). Un backup sin restaurar no es un backup.

`backup-postgres-diario` (job `script`, 03:00) deja `~/backups/nexo/nexo-YYYYMMDD.sql.gz` y borra lo
que tenga más de 14 días. Un backup sin verificar no es un backup: el dump se revisa con
`gunzip -c <archivo> | head -3` y se espera ver el encabezado de `pg_dump` y las sentencias
`CREATE TABLE`.

## Tools del agente (MCP)

El motor lanza `nexo mcp` como servidor MCP por stdio (ADR 0016). Registro, una sola vez:

```bash
opencode mcp add nexo --global -- "$PWD/.venv/bin/nexo" mcp
systemctl --user restart opencode.service     # el motor descubre el servidor al arrancar
opencode mcp list                              # verificar que aparece
```

Prueba sin pasar por el modelo, útil cuando algo falla:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | .venv/bin/nexo mcp
```

Si el motor no ve las tools: revisar que `nexo mcp` arranque a mano (las dependencias del venv son la
causa habitual), y que el proceso hijo herede `NEXO_DATABASE_URL` si la base no está en el puerto por
defecto.

## Skills: una fuente, dos destinos

`skills/` en este repo es la fuente de verdad (ADR 0015). Después de editar una skill, tres comandos:

```bash
nexo skills sync                 # reindexa en la base (idempotente: sin cambios reindexa 0)
nexo skills deploy               # copia al directorio de skills del motor (carga bajo demanda)
nexo skills export               # copia a NEXO_HOME/skills-export/<perfil>/ (ya no escribe en ~/.hermes)
```

`deploy` y `export` son por manifiesto: borran sólo los archivos que ellos mismos escribieron, nunca
las skills que ya estaban. `export` **reporta colisiones** — un slug que ya vive en otro lado del
perfil significa dos copias de la misma skill: hay que retirar la vieja, no convivir con ella.

## Dónde vive el estado: `NEXO_HOME`

Nada de Nexo lee o escribe dentro de `~/.hermes` (eso es el sistema viejo). `NEXO_HOME` es
`~/.local/share/nexo` y se puede mover con la variable de entorno homónima:

```
import-sources/<perfil>/   SOUL.md y memories/ copiados con `nexo snapshot`
skills-export/<perfil>/    espejo de skills/ por perfil (`nexo skills export`)
shopping/price_history.db  histórico de precios (cientos de MB: fuera del repo)
finance/state/             estado del recordatorio de Racional
freelance/scratch/         dumps de Workana para comparar
<perfil>/legal/            documentos que lee un job diario
personal-trainer/          backend Sheets + Google Health (creds de service account)
venvs/                     yt (yt-dlp + faster-whisper) y sheets (gspread)
```

Los scripts de `jobs/` resuelven sus datos por `NEXO_HOME` o por variables propias
(`NEXO_SHOPPING_STATE`, `CAREER_STATE_DIR`, `NEXO_MEDIA_DIR`); el repo sólo guarda código.

**Estado real (2026-11-24).** En producción hay cinco piezas: `nexo-postgres` (Docker,
`127.0.0.1:5546`), el motor de OpenCode y **cuatro** unidades de usuario **versionadas en `deploy/`**:

```bash
cp deploy/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now opencode-engine nexo-channel nexo-worker nexo-scheduler
systemctl --user status opencode-engine nexo-channel nexo-worker nexo-scheduler
journalctl --user -u nexo-worker -f
```

`loginctl enable-linger <usuario>` está activo: las unidades arrancan al bootear sin que haya sesión
abierta. `Restart=always` con `RestartSec=5`, verificado matando el proceso con `kill -9`
(la unidad volvió sola en menos de 8 s y el contador de reinicios quedó en 1).

El worker de turnos todavía no es una unidad aparte: los turnos corren dentro del canal. Cuando F4
separe el ejecutor, se agrega `nexo-worker.service` sin tocar el resto.

Orden de arranque en systemd: `postgres` → `opencode` (con `health`) → `worker`/`cron`/`channel`. El
worker reintenta la conexión pero no arranca a ciegas: si el motor no responde, queda `degraded` y lo
dice en el canal, en vez de acumular turnos `failed`.

**Agregar una tool MCP exige reiniciar el motor.** El cliente MCP del motor se arma cuando el proceso
arranca y no recarga: después de tocar `src/nexo/mcp.py`, `systemctl --user restart opencode-engine` o
la tool nueva no aparece y el agente jura que no la tiene. Verificado el 2026-11-24 con `python` y
`vision_analyze` (la lista pasó de 7 a 9 tools `nexo` recién tras el reinicio).

**Migraciones en un paso previo**, no en el arranque de los servicios: `nexo migrate` corre una vez y
los procesos arrancan asumiendo el esquema. Con varios workers, migrar en el arranque son N intentos
concurrentes y un rollback de imagen no revierte el esquema.

## 2. Variables de entorno

| Variable | Para qué | Rotación |
|---|---|---|
| `OPENCODE_BASE_URL` | `http://127.0.0.1:49374` | — |
| `OPENCODE_TOKEN` | auth del motor | a mano; un `401` a mitad de turno se clasifica `AuthFailed` |
| `DATABASE_URL` | Postgres local | — |
| `TELEGRAM_BOT_TOKEN` | Bot API | a mano; revocar en BotFather si se filtra |
| `TELEGRAM_ALLOWED_CHATS` | allowlist de `chat_id` | por config |
| `EMBEDDINGS_MODEL_PATH` | modelo local | fijado por hash |
| `NEXO_TURN_TIMEOUT` | presupuesto de un turno (default 600 s) | — |
| `NEXO_STREAM_TIMEOUT` | presupuesto del feed SSE que alimenta el guardrail (default 600 s) | — |
| `NEXO_SESSION_TTL_HOURS` | horas de silencio tras las que se descarta la sesión del motor (default 2) | — |

Los dos timeouts existen porque 180 s cortaba trabajo real: el turno del scout midió 165,8 s y
178 s, y uno murió justo en el límite (2026-11-25).

**Workers.** La cola la consumen tres instancias de `nexo-worker@.service`, no una. El orden por
conversación lo garantiza el índice parcial `turns_one_running` (ADR 0027): un job largo ya no frena
el chat, ni de otro perfil ni del mismo. Si un worker pierde la carrera por un turno, `claim_turn`
devuelve `None` y reintenta en un segundo.

**Adjuntos.** El canal descarga cualquier adjunto (foto, documento, audio, video, nota de voz,
sticker) a `NEXO_MEDIA_DIR` (default `~/.cache/nexo/media`) y le pasa la ruta al turno, no el binario.
El turno recibe `[adjunto: ruta · mime · KB]` y decide qué hacer: imágenes con `vision_analyze`,
documentos con `read`. Sin esa descarga, una foto se descartaba en silencio.

**Notificaciones.** Todo lo que manda el bot va con `disable_notification`: un canal de operación
no debe sonar. La excepción es explícita (`silent=False`), no el default.

**Ventana de contexto.** La sesión del motor es una caché de contexto (ADR 0004), no una identidad.
Se descarta y se reconstruye con el transcript en dos casos: `/new` a mano, y **2 h sin
interacción** (`NEXO_SESSION_TTL_HOURS`). Medición que lo motivó: 74.275 tokens de entrada para un
mensaje de 15 caracteres, de los cuales solo ~6.800 los arma nexo; el resto es sesión acumulada.

Secretos en `EnvironmentFile` con permisos `600`, nunca en el repositorio. El motor y la base **solo**
escuchan en loopback.

## 3. Alertas

Cuatro, todas sobre síntoma. Sin runbook no es alerta, es ruido.

| # | Alerta | Umbral | Fuente | Runbook |
|---|---|---|---|---|
| 1 | Turnos fallando | > 10 % en 30 min | `turns.state='failed'` | ver `error_class` dominante → ¿motor arriba? (`/doc`) → ¿token vigente? → si es `ModelRateLimited`, revisar política (ADR 0006) |
| 2 | Turnos huérfanos | ≥ 1 `running` con lease vencido | `turns.state='running' AND locked_until < now()` | worker caído o motor colgado: `journalctl --user -u nexo-worker`; el turno se reintenta al reclamarse |
| 3 | Cuota de gratuitos | > 30 % de `429` por modelo | **no implementada**: el ledger no guarda el estado HTTP de la llamada | — |
| 4 | Costo del día | > 1,5× mediana de 14 días | `model_calls` por día (tabla lista; falta la consulta y el umbral) | desglose por perfil/modelo |

Antes de agregar una alerta nueva: comprobar que ninguna existente ya mide ese síntoma. Lo que suele
faltar es el runbook, no otra alerta.

## 4. Costos

**Estado real (2026-11-25): ledger mínimo, recién implementado.** `model_calls` guarda una fila por
turno con lo que reporta el motor: `tokens_input`, `tokens_output`, `cost_usd`, `latency_ms`,
`model_id`. No hay estimación: si el engine no lo reporta, la fila queda en cero.

Lo que **no** mide, y no hay que leerlo como si lo hiciera: tokens de caché (el adapter no los
extrae) y tokens de subagentes (el engine no los reporta por evento). El costo de un turno con
subagente es el del padre, no el total.

Base medida (30 días, sistema viejo): 34,0 M input · 7,8 M output · 1.800 M cache read · 14,4 k llamadas.

| Tarifa | Mensual | Nota |
|---|---|---|
| Go (con ×2 en ventanas 01–04 y 06–10 UTC) | ≈ USD 15,2 | output 31 %, cache read 36 %, input 33 % |
| Zen pay-as-you-go plano | ≈ USD 12,1 | sin multiplicador horario |

**El cache read es el rubro que decide el costo.** El payload se arma con prefijo estable
(`system + skills` → memoria → historial → mensaje nuevo): mover la memoria antes de las skills rompe
el prefijo y multiplica el gasto del historial. Cualquier cambio en el orden del prompt se mide con
`model_calls.tokens_cache_read` **cuando el ledger exista**; hoy no se estima ni se mide.

Topes vigentes: **ninguno**. `max_tokens_out`, `max_tool_steps` y el presupuesto diario por perfil
están en el diseño y no en el código.

## 5. Backup y restauración

- `pg_dump` diario a disco local, retención 14 días.
- **Restauración probada el primer sábado de cada mes**, en una base temporal, con fecha anotada acá:

| Fecha | Backup usado | Resultado |
|---|---|---|
| — | — | pendiente (se completa en la primera restauración) |

Un backup que nunca se restauró no es un backup. Si la fecha de la tabla tiene más de 60 días, es un
hallazgo de operación, no un detalle.

## 6. Rollback

<a id="rollback"></a>

Volver un perfil al sistema anterior debe tomar **< 10 min** y se prueba antes de migrar el primer
perfil:

1. Reactivar el perfil en el sistema anterior.
2. Apuntar el grupo de Telegram al bot anterior (el bot nuevo queda silencioso, no eliminado).
3. Deshabilitar el cron del perfil en nexo (`UPDATE channels SET enabled = false`).
4. Anotar en `parity.md` la fecha de reversión y la causa.

Los datos de nexo no se borran en un rollback: el transcript queda como evidencia.

## 7. Límites del proveedor que definen el producto

- **Free tier solo por cliente/servidor**: por API pública los modelos `-free` devuelven
  `429 FreeUsageLimitError`. Enrutar gratuitos exige pasar por el motor (ADR 0006).
- **Ventana de 5 h** en los gratuitos: apto para tareas cortas, no para turnos agénticos.
- **Multiplicador ×2** en horarios específicos: el costo de un turno depende de la hora a la que se
  ejecuta; los jobs pesados se programan fuera de esa ventana.
- **API experimental**: un upgrade puede cambiar la forma. El snapshot del contrato en CI es la
  detección (ADR 0002), y la respuesta por defecto es adaptar el adapter, no el dominio.

## 8. Capacidad y desempeño esperado (a validar en F0/F1)

| Métrica | Objetivo | Cómo se mide |
|---|---|---|
| Latencia hasta primer delta | p95 medido, sin objetivo inventado | sonda propia, frío y caliente |
| Turno completo (chat) | p95 < 30 s | `turn_attempts.latency_ms` |
| Búsqueda de memoria | p95 < 150 ms con 20 k items | `EXPLAIN ANALYZE` + sonda |
| Throughput de workers | 1–2 | el cuello es el motor, no el código |

Cualquier número que se publique en el README lleva **procedimiento** (máquina, N concurrentes,
calentamiento) y fecha. Un p95 sin procedimiento se lee como una promesa.

## Credenciales por perfil

Cada perfil tiene su `.env` en `NEXO_HOME/<perfil>/.env`. El motor es un proceso compartido, así que
no hereda ese entorno: las credenciales se piden de a una, nunca el archivo entero.

- Tool MCP `credentials`: `{"profile": "finance", "service": "finance", "key": "imap_user"}` devuelve
  un solo valor; `{"profile": "finance", "action": "list"}` devuelve solo nombres de variables.
- CLI: `nexo credentials list --profile finance` y `nexo credentials get --profile finance FINANCE_IMAP_USER`.

Así dos perfiles pueden usar cuentas distintas de Gmail, GitHub o fal.ai sin duplicar servicios.
