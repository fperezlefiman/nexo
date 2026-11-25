# 0013 — Jobs con dos clases: script y prompt

**Estado:** aceptado · **Fecha:** 2026-11-24

## Contexto

Al validar el perfil piloto (`shopping`) apareció un hecho que cambia el diseño del scheduler: sus dos
crons por hora dicen literalmente **"sin agente"**. La cadena es un script de bash que llama a
`price_monitor.py` (1.282 líneas, solo biblioteca estándar), escribe en `price_history.db` y **solo
imprime** cuando hay un mínimo histórico nuevo; ese texto es lo que se publica.

Es decir: 48 ejecuciones diarias que **no necesitan un LLM**. Modelarlas como un turno de agente
significaría pagar tokens por una comparación de precios que Python ya hace, y agregar latencia y un
modo de falla (el modelo) a un camino que hoy es determinista.

El segundo perfil de la lista (`personal-trainer`) es lo contrario: 127 de 138 sesiones son cron **con
agente**, porque leen Google Sheets, calculan la semana del mesociclo y redactan.

## Decisión

La tabla `jobs` tiene dos clases:

- **`kind=script`**: ejecuta un comando (ruta absoluta + argumentos), captura `stdout` y `stderr`,
  registra `exit_code` y **publica el resultado solo si `stdout` no está vacío**. Sin LLM, sin
  contexto, sin costo.
- **`kind=prompt`**: encola un turno de agente sobre una conversación del perfil, con el prompt del job
  como entrada. Pasa por `ModelPolicy` y registra `model_calls` como cualquier turno.

Común a las dos: `job_runs` con clave única `(job_id, scheduled_for)`, lease con `locked_until`,
reintentos acotados con backoff, y `owner` (ADR 0012).

## Consecuencias

- **Se gana:** los 48 disparos diarios del piloto cuestan **cero** y son deterministas; el mismo
  scheduler sirve para las dos necesidades; los jobs de script son la forma más barata de portar lo
  que ya funciona (el scraper no se toca).
- **Cuesta:** dos caminos de ejecución que mantener y testear (uno de proceso, uno de agente);
  `kind=script` necesita higiene propia: timeout, límite de salida, y no heredar entorno con secretos
  innecesarios.
- **Se vuelve difícil:** "un job" deja de ser un concepto único (hay dos contratos), y confundirlos
  produce o un job que gasta tokens sin necesidad o uno que espera prosa de un script.
- **Verificación:** los 3 días del gate del piloto (≈144 ejecuciones, 0 duplicados, 0 mensajes vacíos);
  un día sin mínimos históricos no produce ningún mensaje; `model_calls` con `phase='job'` = 0 para los
  jobs de `shopping`.

## Alternativas rechazadas

- **Todo job = turno de agente** — 48 turnos diarios de LLM para una comparación de precios:
  innecesario, caro y con un modo de falla nuevo.
- **Todo job = script, y el agente "lee el resultado"** — no sirve para el segundo perfil, que necesita
  redactar y consultar la planilla en el momento.
- **Cron del sistema (`systemd timer` / `crontab`) para los scripts** — resuelve el disparo pero pierde
  `job_runs` (auditoría) y deja la entrega a Telegram fuera del sistema, con dos mecanismos de
  notificación distintos.
- **Cron externo (n8n, que ya está en el homelab)** — agrega un salto de red y un sistema más para algo
  que ya vive en la misma base que el resto del estado.

## Lo que habilita en una entrevista

"El scheduler tiene dos clases porque no todos los cron necesitan un modelo: 48 ejecuciones diarias de
un scraper no deben costar un token. Los jobs de script publican solo si hay salida, con auditoría de
cada corrida y clave única por horario, así un doble tick no duplica la notificación. El mismo motor
sirve para los crons que sí redactan."
