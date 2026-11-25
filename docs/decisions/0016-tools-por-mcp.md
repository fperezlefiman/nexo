# ADR 0016 — Las tools del agente van por MCP, no por un fork del motor

- **Fecha**: 2026-11-24
- **Estado**: aceptado
- **Reemplaza**: —

## Contexto

F6 pide cuatro capacidades que el motor no trae: que el agente escriba memoria, busque en el historial
de conversaciones, cree y cancele jobs desde el chat, y edite skills con commit. La regla del proyecto
es explícita: **no se reimplementa el loop ni las tools que ya provee el motor**.

Había tres caminos y solo uno no rompe esa regla.

## Decisión

Un **servidor MCP por stdio** (`nexo mcp`) registrado en la configuración del motor, con siete tools:
`memory_write`, `memory_search`, `session_search`, `job_create`, `job_list`, `job_cancel`, `skill_write`.

El motor lo lanza como proceso hijo y le habla JSON-RPC. Nexo no toca el loop, no intercepta el
streaming y no depende de cómo el motor decide llamar tools: solo implementa el protocolo.

**Identidad del perfil.** El servidor MCP es **uno solo** para todos los perfiles —el motor lo spawnea
una vez— así que las tools reciben `profile` como argumento y `dispatch` le dice al modelo, en el
bloque `## Entorno` del system prompt, cuál es el perfil activo. Un perfil inexistente es error, no
una fila huérfana.

## Alternativas descartadas

- **Forkear el loop para agregar tools**: exactamente lo que el proyecto decidió no hacer (ADR 0001).
  Además ataría las tools a una versión del motor.
- **Marcadores en el texto** (`<<memory.write ...>>` que nexo parsea después): no requiere
  infraestructura, pero el modelo puede escribirlos dentro de una respuesta normal, no hay validación
  de esquema y el fallo es silencioso. Un protocolo con esquema es más caro de escribir y más barato
  de operar.
- **Plugin en TypeScript en el motor**: más potente (acceso al ciclo de vida), pero mete lógica de
  nexo en el runtime que se quiere mantener reemplazable.

## Consecuencias

- El motor debe reiniciarse para descubrir el servidor (`nexo mcp` no se recarga solo).
- `session_search` usa `ILIKE` sobre `messages`: con 180 mensajes es instantáneo y honesto. Cuando el
  volumen lo pida será FTS o vectorial, no antes.
- `job_cancel` **desactiva**, no borra: el historial de `job_runs` sigue teniendo sentido.
- `skill_write` commitea con la identidad de `NEXO_GIT_IDENTITY` y luego reindexa y despliega. Si el `SKILL.md`
  no trae `name` igual al slug, la tool falla con mensaje claro y no escribe nada.
- Hay un MCP preexistente en la configuración del motor (`shared-memory`) que cumple una función
  parecida a `memory_write`. Se retiró el 2026-11-24 tras absorber su contenido: ver ADR 0017. Queda
  un solo camino de escritura de memoria.
