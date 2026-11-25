# ADR 0025 — Jobs one-shot con `run_at`

- **Fecha**: 2026-11-24
- **Estado**: aceptado
- **Origen**: Corte 4 (`freelance`)

## Contexto

El perfil `freelance` tiene un recordatorio que debe dispararse **una sola vez**: el 2027-01-04, un día
antes de que venza la garantía de reembolso de The Accelerator+. En el sistema anterior era un job
`kind=once`.

`nexo` solo entendía cron de 5 campos: `add_job` exigía `schedule` y `finish()` siempre recalculaba el
próximo slot. La aproximación disponible era escribirlo como `0 10 4 1 *`, que dispara **cada 4 de
enero**. Para un recordatorio atado a una fecha única eso no es un detalle: el 2028 avisaría de una
garantía que venció hace un año.

Escribirlo como cron habría sido documentar el bug en vez de arreglarlo.

## Alternativas

1. **Cron anual (`0 10 4 1 *`) y acordarse de deshabilitarlo.** Rechazada: depende de que alguien se
   acuerde dentro de un año, y el sistema tiene el dato para no depender de la memoria de nadie.
2. **Job `script` con un `.at` de systemd.** Rechazada: saca el trabajo del scheduler propio y devuelve
   justo lo que la migración vino a eliminar — estado fuera de la base, sin `job_runs`, sin auditoría.
3. **`run_at` en la misma tabla.** Es la elegida.

## Decisión

`add_job(..., run_at=<datetime>)` crea el job con `schedule='once'` y `next_run_at = run_at`. Cuando
corre con éxito, `finish()` lo deja en `state='done'` y `enabled=false`, sin recalcular slot.

Un one-shot **que falla** sigue el camino normal de reintento: `next_run_at = now() + 5 min` hasta
agotar `max_attempts`, y recién ahí pasa a `failed`. Un recordatorio único que falla a la primera no
debería perderse en silencio.

## Consecuencias

- El scheduler no cambia: sigue despertando por `next_run_at`, y un job deshabilitado o `done` no
  vuelve a la cola porque la consulta filtra `enabled`.
- `schedule='once'` es un valor más en una columna de texto: no hace falta migración de esquema. El
  costo es que el campo `schedule` deja de ser forzosamente un cron, y eso está dicho en el docstring.
- Cinco tests nuevos contra Postgres real (`tests/test_jobs_oneshot_db.py`): creación, apagado tras
  correr, reintento antes de rendirse, y que un cron **siga** reprogramándose.
- El CLI cubre las cuatro formas (`add-script` / `add-prompt` × cron / `--once`) desde el 2026-11-24;
  antes solo se creaban programáticamente.
- **`nexo job list` mostraba UTC sin decirlo** (un cron de las 9 aparecía como "12:00"). Corregido: la
  lista convierte a hora local y lo dice. El scheduler siempre estuvo bien; el que mentía era el reporte.
