# 0001 — OpenCode server como motor, sin reimplementar el loop

**Estado:** aceptado · **Fecha:** 2026-11-24

## Contexto

El sistema al que reemplaza resuelve todo en un solo proceso: loop del agente, tools de
terminal/filesystem, búsqueda web, cron, skills y memoria. Reimplementar eso es trabajo de meses y no
es donde está el valor diferencial: el loop de un agente con tools es un problema resuelto y muy
sensible a errores (parsing de tool calls, reintentos, manejo de contexto, permisos).

En el homelab corre `opencode serve` en `127.0.0.1:49374`, con una API HTTP (OpenAPI 3.1, 139 rutas,
marcada experimental v0.0.1) que expone sesiones, prompts, interrupción, cambio de modelo, fork,
revert, compact, contexto, diff, permisos y modelos. Verificado end-to-end: se creó una sesión con
`deepseek-v4.1-flash-free`, se envió un prompt y respondió con `cost: 0`. Los modelos gratuitos **solo
funcionan a través del cliente/servidor de OpenCode**; por API pública devuelven
`429 FreeUsageLimitError`.

Restricción de tiempo: ~2 h/día, un operador.

## Decisión

nexo **no implementa el loop del agente ni las tools**. Usa el servidor de OpenCode como motor y pone
su código en cinco capacidades propias: estado durable de turnos, pausa/reanudación, memoria
vectorial, skills compartidas y canales. El motor es reemplazable por diseño: queda detrás de un port
(ADR 0002).

## Consecuencias

- **Se gana:** meses de trabajo evitados; el sistema arranca con tools reales (terminal, archivos,
  búsqueda) desde el día uno; los modelos gratuitos quedan usables, cosa imposible por API directa.
- **Cuesta:** dependencia de un componente de terceros cuya API está declarada experimental y que
  puede cambiar sin aviso; un runtime más en la máquina; el diagnóstico de fallas se reparte entre
  dos sistemas.
- **Se vuelve difícil:** optimizar el uso del contexto a nivel de mensajes individuales (el motor
  decide cómo arma su propia ventana); cambiar de proveedor exige escribir otro adapter.
- **Verificación:** suite de contract tests contra el servidor real + suite offline con fixtures
  (ADR 0002); el turno no compila contra tipos del motor, solo contra `AgentRuntime`.

## Alternativas rechazadas

- **Loop propio con SDK del proveedor** — reimplementa tools, permisos y contexto para no ganar nada
  diferencial; además pierde acceso a los modelos gratuitos.
- **Framework de agentes (LangChain/LangGraph o similar)** — agrega una abstracción sobre otra
  abstracción y una dependencia más que cambia de API seguido; el motor ya trae el loop.
- **Quedarse en el sistema actual y solo extenderlo** — no resuelve memoria semántica ni pausa, que
  son los dos motivos del reemplazo.

## Lo que habilita en una entrevista

"Tenía un motor que resolvía loop y tools pero no mi problema, que era estado, memoria y canales.
Construir el loop era un mes de trabajo sin ventaja competitiva; lo aislé detrás de un port y puse el
esfuerzo donde sí había valor." Es la respuesta de build-vs-buy, y el ADR 0002 es la prueba de que la
decisión no fue ingenua.
