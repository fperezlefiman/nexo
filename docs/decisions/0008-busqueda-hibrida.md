# 0008 — Búsqueda híbrida (vector + full-text) con fusión por rangos

**Estado:** aceptado · **Fecha:** 2026-11-24 · **Relación:** implementa la memoria del ADR 0007

## Contexto

La búsqueda vectorial sola falla de forma predecible en este corpus:

- **Nombres propios y siglas**: "EAS", "Ktor", "MySQL", "pgvector" — el embedding los diluye entre
  vecinos semánticos.
- **Números y montos**: "CLP 1.234.567" o "2,5 M líquidos" se recuperan mal por similitud.
- **Términos exactos de un error o de un comando**: cuando se busca un string, el vector es peor
  herramienta que un índice invertido.

Y la búsqueda léxica sola falla en el caso que motiva el proyecto: lo que se anotó con otras palabras
no aparece. Ninguna de las dos alcanza.

## Decisión

Búsqueda **híbrida** sobre `memory_items`, con dos recuperadores y fusión:

1. **Vectorial** con HNSW y distancia coseno (`vector_cosine_ops`), `top_k = 40`.
2. **Léxica** con `tsvector('spanish')` generado en la misma tabla e índice GIN, `top_k = 40`.
   `ts_rank_cd` como orden.
3. **Fusión RRF** (`k = 60`) más un empujón de importancia:

```
score = 1/(60+rank_vec) + 1/(60+rank_fts) + 0.05 * importance_norm
```

4. **Filtros duros** antes de puntuar: `profile_id`, `deleted_at IS NULL`, `kind` opcional.
5. **Ponderación por uso:** cada item recuperado incrementa `use_count` y actualiza `last_used_at`.
   Es la señal que después permite olvidar lo que nunca se usa.

**Regla de adopción (gate):** la híbrida tiene que superar la línea base léxica (grep/BM25) por **≥ 15
puntos de recall@5** sobre el set de 50 preguntas doradas. Si no lo logra, no se adopta y se escribe
el ADR que lo explique. Un sistema de memoria sin comparación contra una alternativa es una creencia.

## Consecuencias

- **Se gana:** se recupera tanto por significado como por término exacto, y el caso de montos/siglas
  deja de ser un agujero; la fusión por rangos evita calibrar escalas heterogéneas (coseno y `ts_rank`
  no son comparables).
- **Cuesta:** dos índices que mantener y dos consultas por búsqueda; `top_k` alto (40+40) para que la
  fusión tenga material; una `k` de RRF que hay que justificar con números, no con costumbre.
- **Se vuelve difícil:** explicar una recuperación sin mirar los dos rankings (mitigado guardando en
  logs los ids y el rank de cada rama por consulta).
- **Verificación:** `docs/evals.md` con recall@5, MRR y la comparación contra la línea base, con fecha
  y commit; `EXPLAIN ANALYZE` del plan híbrido registrado para detectar seq scans inesperados; p95 de
  búsqueda < 150 ms con 20 k items.

## Alternativas rechazadas

- **Solo vectorial** — falla en siglas, nombres propios y montos, que son la mitad de lo que busco.
- **Solo léxica (BM25/grep)** — es exactamente lo que el sistema actual ya hace y la razón del cambio.
- **Reranker cross-encoder sobre el top 40** — mejora la precisión, pero agrega ~50–200 ms y un modelo
  más; queda como mejora medible si recall@5 no llega al umbral con la híbrida.
- **Normalizar scores (min-max) en vez de RRF** — depende de la distribución de cada consulta y se
  degrada con colas largas; RRF necesita solo el orden.

## Lo que habilita en una entrevista

"Vectorial sola falla en siglas y montos; léxica sola falla en paráfrasis. Uso las dos con RRF, que
solo necesita el orden y evita calibrar escalas. Y el número que lo respalda: recall@5 de X contra Y
de la línea base, con el dataset y la fecha." Si no hay número, la respuesta no está lista.
