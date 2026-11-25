# ADR 0024 — `python` como tool MCP: un proceso nuevo por llamada, no un kernel persistente

- **Fecha**: 2026-11-24
- **Estado**: aceptado
- **Desbloquea**: Corte 4 (`freelance`) — era el último gap de su inventario

## Contexto

En `freelance`, `execute_code` fue **2.290 de 7.001 llamadas de tools (33 %)**, y en `career` 738. No es
una capacidad nueva: `bash` ya corre `python3 -c`. Es **ergonomía**: código de varias líneas con
comillas, heredocs y escapes falla lo suficiente como para que el modelo reintente, y cada reintento es
un turno completo. Medido en el inventario de `migration.md` §4.4, era el último bloqueante del Corte 4.

La referencia (Hermes) implementaba `execute_code` con un **kernel persistente**: las variables
sobreviven entre llamadas y el código tiene acceso a las tools del propio agente.

## Alternativas

1. **Heredoc de bash (lo que hay).** Gratis, cero código. Rechazada por el número: en un tercio de las
   llamadas del perfil la herramienta de trabajo obliga a escapar código, y el costo de un reintento es
   un turno de modelo, no un comando.
2. **Kernel persistente (IPython kernel o `pty` con REPL).** Es lo que hacía el sistema anterior.
   Rechazada: hay que gestionar el ciclo de vida del proceso (huérfanos, reinicios, un kernel por
   conversación), los fallos se vuelven difíciles de atribuir ("¿por qué `x` no existe?" puede ser un
   kernel muerto o una llamada que no corrió), y la persistencia **no está pedida por ningún uso
   observado**: los scripts del perfil son análisis de un solo pase, no sesiones de exploración.
3. **`python3 -c` vía `bash`.** Es la alternativa 1 con distinto nombre: mismo problema de comillas.
4. **Proceso nuevo por llamada, con captura completa.** Es la elegida.

## Decisión

`python(code, timeout?, cwd?)` como tool MCP (`src/nexo/pytool.py`):

- Escribe el snippet a un archivo temporal y lo ejecuta con **el intérprete del proyecto** (`sys.executable`,
  el venv con `psycopg`/`sentence-transformers` disponibles), así el agente no adivina qué Python corre.
- Timeout por defecto 60 s, techo 300 s; al vencer **mata el proceso** y devuelve `returncode: None` con
  el motivo, en vez de colgar el turno.
- Captura `stdout`, `stderr` y `returncode`; trunca a 20 k caracteres por stream con un marcador explícito
  de cuánto se omitió.
- `cwd` opcional, validado; por defecto el home del operador.

## Consecuencias

- **Sin estado entre llamadas.** Está escrito en la descripción de la tool: un modelo que asume
  persistencia escribe un script largo en la segunda llamada y recibe un `NameError` que no puede
  explicar. Hay un test (`test_state_does_not_persist_between_calls`) que fija ese contrato.
- **Overhead de ~50–100 ms por llamada** (arrancar el intérprete). Contra un turno de modelo de 8–20 s,
  es ruido.
- **La superficie de riesgo no cambia**: corre como el mismo usuario que el `bash` del motor, en la misma
  máquina. No es un permiso nuevo; es la misma capacidad con mejor forma.
- Si algún día hace falta persistencia real, el camino es un kernel por conversación **con el ciclo de
  vida resuelto** (arranque perezoso, muerte al cerrar la conversación, `id` en la respuesta). No se
  agrega hasta que un uso concreto lo pida.
