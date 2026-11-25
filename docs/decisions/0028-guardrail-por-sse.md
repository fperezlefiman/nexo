# ADR 0028 — Guardrail por SSE, sin texto en vivo

- **Fecha**: 2026-11-25
- **Estado**: aceptado
- **Relacionado**: ADR 0005 (máquina de estados del turno), ADR 0027 (pool de workers)

## Contexto

Desde el 25-nov la línea de progreso muestra solo el reloj (`⏳ N min`), sin el texto que el motor
va produciendo. El cambio fue de interfaz, pero el camino de código que alimentaba al `ToolGuard`
era el mismo que empujaba ese texto: el worker dejó de pasar `on_progress`, `_stream_turn` quedó sin
llamadores y **el guardrail de loops dejó de correr en producción**. La suite seguía en verde porque
el guard tiene tests propios y el camino no tenía ninguno.

Un turno que repite la misma tool quema la ventana del modelo gratuito sin que nada lo corte. Es el
caso que el guard existe para evitar.

## Decisión

1. Cada turno se suscribe a `GET /api/event` **solo para observar tool calls**; los deltas de texto
   se descartan y la línea de progreso sigue mostrando minutos.
2. La respuesta y el `usage` se leen de `GET /api/session/{id}/message` cuando el turno termina. Así
   el ledger de costos no depende del feed.
3. Si el feed muere o vence, el turno **no falla**: se sigue con el polling, sin guardrail para ese
   turno. La suscripción es una entrada del guard, no la fuente de la respuesta.

## Alternativas descartadas

- **Volver al texto en vivo**: era ruido en el chat y cada delta es una edición HTTP. Se descartó
  como interfaz, no como fuente del guard.
- **Guard después del turno**: cuando llega el mensaje final ya se gastó la cuota; el guard tiene que
  cortar mientras el turno corre.
- **Sin guardrail**: aceptar el riesgo de loop con una ventana gratuita acotada no se sostiene.

## Consecuencias

- Se gana el guardrail y el ledger completo, sin cambiar lo que ve el usuario.
- Cuesta una conexión HTTP extra por turno, viva mientras dura el turno.
- Un feed caído deja el turno sin protección: se acepta y se declara, porque perder la respuesta
  sería peor.
