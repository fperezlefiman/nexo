# ADR 0027 — Pool de workers, orden por conversación

- **Fecha**: 2026-11-25
- **Estado**: aceptado
- **Relacionado**: ADR 0005 (máquina de estados del turno), ADR 0010 (jobs en tabla)

## Contexto

Un solo proceso worker recorre la cola global. Es simple y garantiza el orden, pero acopla cosas
que no deberían estarlo: el scout de `career` tarda ~166 s y, mientras corre, cualquier mensaje de
cualquier perfil espera. Medido el 25-nov: un turno de chat quedó detrás de un job del mismo perfil.

La propuesta inicial fue "un worker por perfil". Se descartó antes de escribirla: el caso que
motiva el cambio es el job y el chat **del mismo perfil**. Repartir por perfil mueve el bloqueo de
sitio, no lo elimina.

## Decisión

1. **N workers** (`nexo-worker@.service`, tres instancias) consumen la misma cola. La coordinación
   sigue siendo la base: `SELECT ... FOR UPDATE SKIP LOCKED`.
2. **Orden por conversación** se vuelve una regla del esquema, no una promesa del proceso:
   `CREATE UNIQUE INDEX turns_one_running ON turns (conversation_id) WHERE state = 'running'`.
   Si dos workers intentan tomar dos turnos del mismo chat a la vez, uno pierde con
   `UniqueViolation` y `claim_turn()` devuelve `None` (no es error: es la respuesta).
3. El `lease` de 20 min se mantiene: sigue siendo lo que recupera turnos de un worker muerto.

## Alternativas descartadas

- **Un worker por perfil**: no resuelve el caso que lo motiva (job y chat en el mismo perfil) y
  multiplica procesos con una regla de negocio (perfiles) metida en la topología.
- **Garantizar el orden solo con `NOT EXISTS`**: hay carrera entre la comprobación y el `UPDATE`;
  el índice único la cierra en la base, que es donde no se puede mentir.
- **Un worker por conversación**: la cantidad de conversaciones no es acotada y cada proceso
  pagaría su conexión.

## Consecuencias

- El orden por chat se conserva aunque haya tres consumidores, y un job largo ya no frena al chat
  de otro perfil **ni al del mismo**.
- `claim_turn()` puede devolver `None` con trabajo pendiente: el worker duerme un segundo y vuelve.
  El costo es latencia de hasta ~1 s en un turno que perdió la carrera.
- Más conexiones a Postgres (una por worker) y tres procesos en vez de uno.
- El día que haga falta más paralelismo, se agregan instancias del template sin cambiar código.
