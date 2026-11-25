# 0004 — Transcript propio canónico; la sesión del motor es descartable

**Estado:** aceptado · **Fecha:** 2026-11-24

## Contexto

El motor mantiene el estado de la conversación en una sesión propia (`POST /api/session` devuelve un
id opaco que después se usa en `/prompt`, `/interrupt`, `/compact`, `/fork`). Es cómodo: el contexto
vive del otro lado y no hay que reenviarlo.

Dos hechos lo vuelven insuficiente como fuente de verdad:

1. La API es experimental (ADR 0002) y el servidor puede reiniciarse, upgradearse o perder sesiones.
2. El producto necesita cosas que la sesión no da: transcript auditable, costos por turno, pausa
   durable, búsqueda sobre la historia y reconstrucción tras un fallo.

Si la sesión es la fuente de verdad, un reinicio del motor equivale a perder la conversación.

## Decisión

El **transcript en nuestra base es canónico y append-only**. La sesión del motor es una **caché de
contexto** identificada por `(opencode_session_id, context_epoch)`:

- Cada conversación guarda su `context_epoch`. Sube cuando se reconstruye contexto: compactación,
  replay tras `SessionLost`, o fork deliberado.
- Si el motor pierde la sesión, se crea otra y se **reproduce un contexto compactado** desde el
  transcript (resumen + últimos N mensajes + memoria relevante). El turno continúa; el costo extra se
  registra como `phase='rebuild'` en `model_calls`.
- `revert` del motor no borra nada nuestro: se marca el turno como `reverted` y el replay lo excluye.

## Consecuencias

- **Se gana:** un reinicio del motor deja de ser un incidente; la historia queda consultable y
  auditable; pausa y fork son posibles; la migración a otro motor no pierde datos.
- **Cuesta:** duplicación de contexto (lo que el motor guarda y lo que nosotros guardamos), y un
  mecanismo de replay que hay que probar seguido; el rebuild cuesta tokens y hay que vigilarlo.
- **Se vuelve difícil:** delegar en el motor la ventana de contexto completa (nosotros decidimos qué
  se le manda, lo que es más trabajo pero también la razón de los costos controlados).
- **Verificación:** test de integración que mata el servidor **en medio** de un turno, y comprueba que
  el siguiente turno responde con contexto, con `context_epoch` incrementado y el costo del rebuild
  registrado. Sin ese test, la garantía es una intención.

## Alternativas rechazadas

- **Confiar en la sesión del motor y hacer backup de su almacenamiento** — acopla la durabilidad a un
  formato interno ajeno y no resuelve consultas ni costo por turno.
- **Reenviar siempre el historial completo en cada prompt** — simple y caro: el cache read ya es 36 %
  del gasto medido, y un prefijo que cambia en cada llamada destruye el caché.
- **Guardar solo un resumen y descartar los mensajes** — barato, pero pierde la evidencia y la
  posibilidad de reconstruir; un resumen no es un transcript.

## Lo que habilita en una entrevista

"Separé estado de motor y estado de producto. El motor es una caché de contexto con época; la verdad
es un transcript append-only. Por eso un reinicio del motor no es una pérdida: se reconstruye, se mide
el costo del rebuild y sigue. Y lo verifiqué matando el server en medio de un turno."
