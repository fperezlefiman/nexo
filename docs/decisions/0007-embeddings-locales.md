# 0007 — Embeddings multilingües locales de 1024 dimensiones

**Estado:** aceptado · **Fecha:** 2026-11-24

## Contexto

La memoria del sistema es en español, con anglicismos técnicos mezclados ("tool calling", "pgvector",
"cache read", "allowance"), nombres propios, siglas de empresa y montos ("CLP 1,23 M"). Los
documentos son cortos: hechos, preferencias, decisiones, eventos — no papers.

El hardware es un homelab Linux sin GPU. El volumen de memoria es de decenas de miles de items como
techo, no millones. El sistema corre en el mismo host que el motor y la base, así que cada milisegundo
y cada megabyte de RAM cuentan.

## Decisión

Embeddings **multilingües de 1024 dimensiones**, ejecutados **localmente en CPU** (ONNX runtime),
vector almacenado en `pgvector` con índice HNSW y distancia coseno.

Razones:
- El corpus es español: un modelo centrado en inglés (MiniLM y familia) rinde peor en consultas en
  español y no vale el ahorro de tamaño.
- Costo marginal cero y sin dependencia de red: el indexado corre en background y no bloquea nada.
- 1024 dims es el punto razonable entre calidad y tamaño de índice para decenas de miles de vectores.

Detalle operativo: modelo versionado y fijado por hash; `memory_items.embedding_model` guarda la
versión para poder reindexar sin mezclar espacios vectoriales.

## Consecuencias

- **Se gana:** búsqueda semántica en español con costo cero, sin clave de API ni proveedor extra, y
  con el corpus completo dentro del homelab.
- **Cuesta:** ~10–40 ms por chunk en CPU (aceptable en background, no en el camino crítico del turno);
  el modelo ocupa RAM de forma permanente; CPU compartida con el motor durante el indexado.
- **Se vuelve difícil:** cambiar de modelo de embeddings sin reindexar todo (mitigado con la columna
  de versión y un script de reindexado).
- **Verificación:** p95 de `embed()` medido y registrado; test que comprueba que la dimensión del
  vector coincide con la columna; `evals.md` con el recall de la búsqueda construida sobre estos
  vectores.

## Alternativas rechazadas

- **API de embeddings de un proveedor** — con este volumen el costo es irrelevante, pero agrega un
  proveedor, una clave, latencia de red y un punto de falla para una pieza que el hardware resuelve.
- **TF-IDF / BM25 solo** — es la línea base, no la solución: falla justo en el caso que motiva el
  proyecto (lo anotado con otras palabras). Se conserva como componente de la búsqueda híbrida
  (ADR 0008).
- **Modelo grande multilingüe (7B+)** — calidad marginal sobre documentos de dos frases, y no entra en
  RAM junto al resto sin GPU.

## Lo que habilita en una entrevista

"El corpus es español con anglicismos; un modelo inglés-centrado acierta menos y el ahorro no paga la
pérdida. Corro embeddings de 1024 dims en CPU: costo cero, sin proveedor, 10–40 ms en background. Y
guardo la versión del modelo en cada fila para poder reindexar sin mezclar espacios vectoriales."
