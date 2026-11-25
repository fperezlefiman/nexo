# 0006 — Política de modelos por clase de tarea, con circuit breaker y fallback

**Estado:** aceptado · **Fecha:** 2026-11-24

## Contexto

Los modelos disponibles tienen perfiles de costo y límite muy distintos, medidos con datos de 30 días
(~14.4 k llamadas):

- Pago barato (`deepseek-v4.1-flash`): `$0.15/$0.60/$0.003` por millón (in/out/cache read), con
  multiplicador ×2 de lunes a viernes 01–04 y 06–10 UTC.
- El mismo modelo por la vía pay-as-you-go plana: `$0.12/$0.48/$0.0024`, sin horarios.
- Gratuitos: funcionan **solo** a través del cliente/servidor de OpenCode; por API directa devuelven
  `429 FreeUsageLimitError`. Tienen límite por ventana de 5 h.

Consumo medido: 34,0 M input · 7,8 M output · 1.800 M cache read ≈ **USD 15,2/mes** a tarifa Go
(output 31 %, cache read 36 %, input 33 %). A tarifa Zen plana, ≈ USD 12,1.

Un turno agéntico consume muchísimo contexto. Usar gratuitos en el loop principal es tentador y
frágil: el límite de 5 h se alcanza a mitad de un turno, y el reintento cuesta más que haber usado el
modelo barato desde el principio.

## Decisión

El modelo se elige **por clase de tarea** (`ModelPolicy.pick`), no por conversación, y la decisión se
registra:

| Clase | Modelo | Motivo |
|---|---|---|
| `chat_principal` | pago barato | turnos largos, contexto grande, no se puede cortar a la mitad |
| `resumen` / `compact` | gratuito | tarea corta, salida acotada, reintentable |
| `clasificar` / `indexar` | gratuito | alto volumen, bajo contexto, idempotente |
| `proactivo` (cron) | gratuito, con fallback | si falla, se saltea la corrida; no es urgente |

Reglas de protección:

- **Circuit breaker por modelo:** ventana deslizante de 20 llamadas; con >30 % de
  `ModelRateLimited`, el modelo sale de rotación 15 min y el evento queda registrado.
- **Fallback chain** explícita por clase; el intento fallback se registra como
  `model_calls.status='fallback'`.
- **Tope por turno:** `max_tokens_out` y `max_tool_steps`. Un loop sin tope es indistinguible de un
  ataque contra la propia tarjeta.
- El turno guarda `model` y `reason` (`policy:chat_principal`, `slash:/modelo`,
  `fallback:rate_limited`), expuestos por `/porque`.

## Consecuencias

- **Se gana:** costo por debajo del allowance con el mismo uso; degradación predecible cuando un
  gratuito se agota; cada gasto es explicable con un motivo.
- **Cuesta:** una política que hay que ajustar con datos (y una tabla de precios que puede quedar
  desactualizada si cambia el proveedor); el fallback puede empeorar la calidad de un turno sin que se
  note, así que la respuesta registra con qué modelo se generó.
- **Se vuelve difícil:** pensar en "el modelo del perfil" como algo estático; el perfil define un
  modelo por defecto, pero la clase de tarea manda.
- **Verificación:** ratio de `429` por modelo (alerta de operación); `model_calls` por turno con
  `status`; test de la política como función pura (mismas entradas → misma elección); test que simula
  20 respuestas 429 y comprueba que el modelo sale de rotación.

## Alternativas rechazadas

- **Gratuitos siempre** — el límite por ventana corta los turnos largos; el reintento cuesta más que
  el modelo barato.
- **Pago siempre** — deja ~USD 12–15/mes sobre la mesa para tareas donde la calidad no cambia, y
  desperdicia un recurso disponible.
- **Elegir modelo por perfil, estático** — ignora que dentro de un perfil conviven tareas de costos
  muy distintos (un resumen de 400 tokens y un turno agéntico de 20 llamadas).

## Lo que habilita en una entrevista

"El enrutamiento de modelos es una decisión de costos y de disponibilidad, no de preferencia. Con
480 llamadas por día medí que el output y el cache read eran dos tercios del gasto, así que puse los
gratuitos donde la tarea es corta y reintentable y el pago donde un corte a mitad de turno sale más
caro. Hay circuit breaker por tasa de 429 y cada turno guarda por qué usó el modelo que usó."
