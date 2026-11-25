# 0010 — Jobs en tabla con `FOR UPDATE SKIP LOCKED`

**Estado:** aceptado · **Fecha:** 2026-11-24

## Contexto

El sistema necesita proactividad: recordatorios del plan de estudio (8:00 y 18:00), revisiones
semanales, monitoreo de precios o de ofertas, y tareas de mantenimiento (indexado, agregación de
costos, purga por retención).

Restricciones: un solo host, procesos que se reinician (deploy o reboot), y la necesidad de **auditar**
qué corrió, cuándo y con qué resultado. Cuando un recordatorio no llega, hay que poder decir por qué.

## Decisión

Los jobs viven en una tabla `jobs` y se drenan con un tick de 10 s desde `worker/scheduler`:

```sql
SELECT ... FROM jobs
WHERE state = 'queued' AND next_run_at <= now()
ORDER BY next_run_at
FOR UPDATE SKIP LOCKED
LIMIT 20;
```

Con `locked_until` como lease, `attempts`/`max_attempts` para reintentos, y `job_runs` como bitácora
de cada corrida (inicio, fin, estado, salida, `turn_id` si generó un turno).

- Un job que falla se reprograma con backoff exponencial; al agotar intentos queda `failed` con
  `last_error`.
- La ejecución es **idempotente por diseño**: el job declara una clave de corrida
  (`job_id, scheduled_for`) única en `job_runs`, así un doble tick no dispara dos veces.

## Consecuencias

- **Se gana:** los jobs sobreviven reinicios; dos workers no duplican trabajo (`SKIP LOCKED`); hay
  bitácora consultable (`/jobs`) y una base para la alerta de "turno proactivo no disparado".
- **Cuesta:** una tabla y un poller propio (aunque acotado: es un `SELECT` cada 10 s, no un motor de
  colas); la precisión del disparo es de ±10 s, no de milisegundos.
- **Se vuelve difícil:** razonar los jobs como "eventos" (son filas con estado, lo que es una ventaja
  para auditar y una desventaja si se necesita fan-out masivo).
- **Verificación:** test con dos schedulers corriendo en paralelo → una sola ejecución por job; test
  que reinicia el proceso con un job vencido → se ejecuta; 7 días de recordatorio dentro de ±60 s.

## Alternativas rechazadas

- **APScheduler / `node-cron` en memoria** — pierde los jobs en cada reinicio y no deja bitácora; para
  recordatorios personales, la auditoría **es** el producto.
- **Cron del sistema (`crontab`)** — no tiene reintentos ni historial consultable, y reparte la lógica
  entre el crontab y el código; además el contenedor/entorno cambia y el crontab queda mintiendo.
- **Cola externa (Redis/RabbitMQ/Celery)** — un servicio más para decenas de jobs por día; el
  `SKIP LOCKED` de Postgres ya resuelve concurrencia y atomicidad sin operar nada nuevo.

## Lo que habilita en una entrevista

"Los jobs están en Postgres, no en memoria: `SKIP LOCKED` para que dos workers no dupliquen, lease para
recuperar un job huérfano, y una tabla de corridas para poder responder '¿por qué no llegó el
recordatorio de las 8?'. Redis o un cron del sistema no daban reintentos ni auditoría; el costo es un
`SELECT` cada 10 segundos."
