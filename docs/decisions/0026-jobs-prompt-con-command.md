# ADR 0026 — Un job `prompt` puede traer `command`: el script corre y su salida entra al prompt

- **Fecha**: 2026-11-24
- **Estado**: aceptado
- **Origen**: Corte 5 (`career`), scout de empleos

## Contexto

El único job de `career` es el scout de empleos: corre `scout_all.py` (ocho fuentes en paralelo, hasta
15 minutos) y **después** el agente deduplica, descarta por idioma y por rol, abre tres avisos y reporta
cinco ofertas. La salida cruda del script no sirve como mensaje: son cientos de líneas de listados
repetidos entre portales.

En el sistema viejo eso era un job con `script` **y** `prompt`: la plataforma corría el script e
inyectaba su salida en el prompt del turno. En `nexo` los jobs eran excluyentes — `script` manda el
stdout tal cual, `prompt` corre un turno sin salida de script previa.

Migrar el scout como `script` habría entregado el listado crudo: peor que no tener scout. Como `prompt`
puro, el agente tendría que correr el script él mismo dentro del turno, con un `shell` de 15 minutos
dentro de un turno que además tiene que razonar sobre el resultado.

## Alternativas

1. **Job `script` y que el agente filtre en otro job.** Dos jobs encadenados por estado: más piezas,
   más modos de falla, y la salida intermedia habría que guardarla en algún lado.
2. **`prompt` y que el agente corra el script con `shell`.** Rechazada: mete 15 minutos de ejecución
   dentro del presupuesto del turno, y cualquier timeout del motor corta la mitad de las fuentes sin
   dejar registro en `job_runs`.
3. **`kind='prompt'` + `command` opcional: el scheduler corre el comando y pega la salida al prompt.**
   Es la elegida, y no necesita cambio de esquema: `payload` y `command` ya son columnas de `jobs`.

## Decisión

`run_once` corre `job["command"]` **antes** de encolar el turno (`_merge_command_output`) y appendea el
resultado al payload bajo un encabezado explícito:

```
=== SALIDA DEL COMANDO (<comando>) ===
```

Reglas:

- **Un comando que falla no es un job que falla.** `stdout` y `stderr` entran igual al prompt, para que
  el agente reporte *qué fuente* se rompió. El prompt del scout ya tenía esa regla escrita
  ("si alguna fuente falla, decilo en el recuento") y ahora tiene con qué cumplirla.
- **Timeout configurable, 1 200 s por defecto** (`NEXO_SCRIPT_TIMEOUT`). El valor anterior (900 s)
  empataba con el timeout interno de `scout_indeed.py`, así que el job moría justo en el borde y sin
  salida. Al vencer, el prompt recibe un `[error]` con el comando, no una excepción.
- **La salida se trunca a 30 000 caracteres** con un marcador de cuánto se omitió. 3 500 (el límite de
  `job_runs.output`) es para el registro; el prompt necesita más para no perder ofertas.
- El `job_runs.output` guarda el resultado del **turno**, no la salida del comando: el comando es
  insumo, no entregable.

## Consecuencias

- El scout se migra sin cambiar su comportamiento: mismas ocho fuentes, mismo prompt, misma curación.
- **El scheduler se bloquea mientras corre el comando.** Hoy está bien porque el scout corre a las 9 y
  a las 18, pero un job de 15 minutos retrasa hasta 15 minutos cualquier job que venza en ese rato
  (los de `shopping` corren cada 30 min). Queda anotado como deuda: cuando haya dos jobs largos, hay
  que mover la ejecución a un hilo o a un worker aparte.
- Cinco tests offline (`tests/test_prompt_command_offline.py`): salida pegada, `stderr` conservado,
  comando roto que no levanta, timeout reportado dentro del prompt y truncado con marcador.
