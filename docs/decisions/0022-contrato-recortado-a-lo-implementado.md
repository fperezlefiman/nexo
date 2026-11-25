# ADR 0022 — El contrato del motor se recorta a lo que el adapter implementa

- **Fecha**: 2026-11-24
- **Estado**: aceptado
- **Relacionado**: ADR 0002 (adapter con contrato propio)

## Contexto

`ports.AgentRuntime` declaraba nueve operaciones. El adapter `OpenCodeRuntime` implementaba seis:
`health`, `open_session`, `prompt`, `wait_turn`, `stream`, `interrupt`.

Las otras tres — `set_model`, `context`, `list_sessions` — no existían en el adapter y **nadie las
llamaba**. Revisión completa del dominio: el cambio de modelo se resuelve con `set_model_override()`
sobre la conversación en Postgres y una sesión nueva; el contexto se controla con nuestro propio
`context_epoch`; y ninguna pieza lista sesiones del motor, porque la sesión es descartable (ADR 0004).

El costo de la deriva se hizo visible al correr la suite: **cuatro contract tests en vivo fallaban con
`AttributeError`**. Y como `AgentRuntime` es un `Protocol`, Python no lo verifica en runtime: nada
avisaba hasta que alguien ejecutaba los tests.

## Alternativas

1. **Implementar las tres en el adapter.** Son endpoints que existen en el motor (`POST /api/session/{id}/model`,
   `GET /api/session/{id}/context`, `GET /api/session`). Rechazada: se escribiría código nuevo para
   capacidades que ningún camino del sistema usa, y cada una es una superficie más contra una API
   experimental. El cambio de modelo por override además tiene una ventaja que el endpoint no da: queda
   registrado en nuestro transcript, no en el estado del motor.
2. **Convertir `AgentRuntime` en clase base abstracta (`ABC`).** Obligaría al adapter a implementar todo
   o fallar al instanciar. Rechazada por ahora: rompe el estilo del port (`Protocol`, tipado estructural)
   y no resuelve el fondo — un método abstracto sin llamadores sigue siendo deuda, solo que ruidosa.
3. **Recortar el contrato a lo implementado y usado.** Es la elegida.

## Decisión

El `Protocol` declara seis operaciones. Se eliminan `set_model`, `context` y `list_sessions`, con el
motivo escrito en el propio archivo para que nadie las reponga "por completitud". Los dos tests que las
usaban como disparador de error (`AuthFailed`, `RuntimeUnavailable`) ahora disparan con
`open_session`, que es el camino real.

## Consecuencias

- Suite: **67 verdes, 6 skipped, 0 rojos**.
- El contrato deja de prometer capacidades inexistentes. Un lector del `Protocol` ve exactamente lo que
  el motor soporta en este sistema.
- **Queda abierto el problema de fondo:** un `Protocol` no se verifica en runtime, así que el contrato
  puede volver a divergir en silencio. Mitigación actual: los contract tests en vivo, que en CI solo
  corren con el motor arriba. Si vuelve a pasar, la siguiente medida es un chequeo de firma en un test
  offline que compare `dir(OpenCodeRuntime)` contra `AgentRuntime.__protocol_attrs__`.
