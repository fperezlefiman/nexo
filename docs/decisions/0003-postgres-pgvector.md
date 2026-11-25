# 0003 — Postgres 17 + pgvector en vez de SQLite

**Estado:** aceptado · **Fecha:** 2026-11-24

## Contexto

El sistema necesita, en la misma base: transcript append-only, estado de turnos con reintentos,
cola de jobs con varios workers, búsqueda semántica sobre memoria y skills, y un ledger de costos con
agregación por día/perfil/modelo.

Volumen real esperado: ~480 llamadas de modelo por día, ~34 M tokens de input y 1.800 M de cache read
al mes; datos en el orden de **megabytes**. Un solo operador. Homelab Linux, sin costo de nube, ya
con Docker y experiencia operando Postgres.

Requisito concreto que descarta la opción "simple": la cola de turnos y la de jobs necesitan **varios
consumidores concurrentes** con toma atómica y lease.

## Decisión

Postgres 17 con la extensión `pgvector`, en Docker, escuchando solo en loopback. Tres usos no
negociables:

1. **Cola:** `FOR UPDATE SKIP LOCKED` sobre `turns` y `jobs`, con `locked_until` como lease.
2. **Búsqueda híbrida:** `vector_cosine_ops` (HNSW) + `tsvector('spanish')` con GIN, en una sola
   consulta y con fusión por rangos (ADR 0008).
3. **Ledger:** `model_calls` con vista diaria; el costo por turno, perfil y fase sale en SQL.

## Consecuencias

- **Se gana:** una sola pieza resuelve cola, vectores, texto y agregación, sin código propio de
  sincronización; el vocabulario y las herramientas son las del stack que piden los avisos del
  mercado objetivo; backup y restauración son un comando conocido.
- **Cuesta:** ~100 MB de RAM y un servicio más que mantener; `pg_dump` diario y **restauración probada
  mensualmente** (un backup sin restauración no es un backup); una migración que corre en un paso
  previo y no en el arranque de los workers.
- **Se vuelve difícil:** distribuir la base (no hace falta); usar el sistema sin el contenedor
  levantado (mitigado con `health` y arranque ordenado en systemd).
- **Verificación:** test de concurrencia que lanza dos consumidores y comprueba que un job se ejecuta
  una sola vez; `EXPLAIN ANALYZE` del plan de la búsqueda híbrida registrado en `evals.md`; fecha de la
  última restauración en `operations.md`.

## Alternativas rechazadas

- **SQLite + `sqlite-vec`** — más simple de operar, pero un escritor serializa turnos, indexado y
  jobs; el ledger y la agregación se vuelven código de aplicación; y no hay paralelismo real entre
  workers.
- **Archivos JSON + un índice en memoria** — se corrompe con una escritura concurrente y obliga a
  reconstruir todo al arrancar; el costo de recuperación crece con la historia.
- **Base vectorial dedicada (Qdrant/Weaviate) + Postgres para el resto** — dos sistemas para ~20 k
  vectores; se paga sincronización y operación por nada. Se reconsidera solo si la memoria supera
  el millón de items.

## Lo que habilita en una entrevista

"Postgres entra por tres requisitos que se pisan —cola con `SKIP LOCKED`, búsqueda híbrida y ledger de
costos— no por moda. SQLite lo resolvía aceptando un escritor serializado y reescribiendo la
agregación en código. Con 20 mil vectores, una base vectorial dedicada era operar dos sistemas para
nada." Y el número al lado: ~100 MB de RAM y p95 de búsqueda < 150 ms.
