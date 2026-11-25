# Evals de la memoria

Un sistema de recuperación sin medición es una creencia. Este documento define **qué se mide, cómo, y
qué número bloquea la adopción** (ADR 0008).

Estado: **medido el 2026-11-24** (`30dd11c`, ADR 0021). El dataset es `docs/evals/gold-set.jsonl`:
36 preguntas sobre las 52 memorias reales de los perfiles migrados, 35 con respuesta esperada y una sin
respuesta. Lo que **todavía no existe** está marcado abajo como brecha, no como hecho.

## 1. Dataset

- **36 preguntas doradas** escritas sobre las 52 memorias reales (`gold-set.jsonl`), con la respuesta
  esperada como id de memoria. Mezcla a propósito paráfrasis sin vocabulario en común y preguntas que sí
  lo comparten, para no favorecer a los vectores por diseño.
- **Split fijo, guardado en el archivo** (campo `split`), no derivado de un hash: un hash rebarajaría el
  test el día que alguien toque la función, que es justo cómo un test deja de ser test. Hoy: **25 dev /
  11 test**, balanceado por tipo (paráfrasis y léxico en ambos lados) con `nexo evals --split`.
  > **Brecha que queda:** el plan pedía 50 preguntas; hay 36. El split ya existe, el tamaño no.
- Cada ítem lleva: `question`, `expected_memory_ids` (1–3), `profile`, `kind`, `difficulty`
  (`facil` = léxico solapado, `media` = paráfrasis, `dificil` = siglas/montos/nombres propios).
- **Split fijo, guardado en el archivo:** hoy **25 dev / 11 test** (el plan pedía 35/15 con 50
  preguntas; con 36 se reparte en tercios). El test se toca **una vez por cambio de arquitectura**, no
  por iteración de parámetros: ajustar `k` o pesos contra el test es overfitting con otro nombre.
- El dataset vive versionado en el repo, sin datos personales sensibles: se reemplazan montos exactos
  por rangos y nombres de terceros por iniciales.

## 2. Métricas

| Métrica | Definición | Umbral que bloquea |
|---|---|---|
| **recall@5** | fracción de preguntas donde al menos un `expected_memory_id` está en el top 5 | **≥ 0,80** |
| **MRR** | media de `1/rank` del primer acierto | **≥ 0,65** |
| **Δ vs línea base** | diferencia de recall@5 contra búsqueda léxica pura (BM25/grep) | **≥ +15 pts** |
| p95 de búsqueda | latencia end-to-end con 20 k items | < 150 ms |
| Precisión de extracción | % de memorias auto-extraídas correctas, en 30 casos muestreados a mano | ≥ 0,90 |

Si `Δ vs línea base` < 15 pts, **no se adopta la búsqueda híbrida** y se escribe un ADR explicando qué
se hace en su lugar. Es la única forma de que la memoria vectorial sea una decisión y no un gusto.

## 3. Protocolo

1. Sembrar la base con datos reales anonimizados (o la historia completa del operador, si el test corre
   local).
2. `nexo evals --split dev` mientras se itera; `nexo evals --split test` solo al cerrar un cambio de
   arquitectura. Corre los cinco recuperadores sobre el mismo set.
3. Registrar en la tabla de §5 con **fecha y commit**.
4. El gate corre en CI con `path filter` sobre lo que mueve el score: extractor, modelo de embeddings,
   función de fusión, migraciones de `memory_items`. Un gate que no corre en CI no bloquea nada.

## 4. Resultados

| Fecha | Commit | Variante | recall@5 | MRR | p95 | Notas |
|---|---|---|---|---|---|---|
| 2026-11-24 | `30dd11c` | `substring` (lo que corría antes) | **0,000** | 0,000 | — | la frase entera como patrón ILIKE |
| 2026-11-24 | `30dd11c` | `keywords` (léxico laxo) | 0,657 | 0,536 | — | línea base de uso real |
| 2026-11-24 | `30dd11c` | `lexical` (FTS español) | 0,743 | 0,614 | — | línea base del gate |
| 2026-11-24 | `30dd11c` | **`vector` (e5-large, 1024d)** | **0,971** | **0,907** | — | **adoptado**: +22,8 pts sobre `lexical` |
| 2026-11-24 | `30dd11c` | `hybrid` (RRF) | 0,886 | 0,801 | — | descartado: +14,3 pts, no pasa el gate y es peor que el vector solo |

Casillas en blanco = **no medido**. El `p95` del gate (< 150 ms con 20 k items) sigue sin medirse: hoy
hay 52 memorias y medir latencia a esa escala no dice nada sobre 20 k.

### 4.1 Split dev/test — medido el 2026-11-24 (`nexo evals --split dev|test`)

| recuperador | dev (24 con respuesta) | test (11 con respuesta) |
|---|---|---|
| `substring` | 0,000 / 0,000 | 0,000 / 0,000 |
| `keywords` | 0,625 / 0,517 | 0,727 / 0,576 |
| `lexical` | 0,708 / 0,569 | 0,818 / 0,712 |
| **`vector`** | **0,958 / 0,865** | **1,000 / 1,000** |
| `hybrid` | 0,875 / 0,779 | 0,909 / 0,848 |

Lectura: el vector **generaliza al set que no se usó para elegirlo** — 11 de 11 en top-5 y MRR 1,000.
Eso es lo que el número agregado de §4 no podía demostrar. El híbrido vuelve a quedar por debajo del
vector **también fuera de muestra**, así que el descarte de ADR 0021 no es un artefacto del set de
desarrollo. Con n=11 el test es chico: mide dirección, no precisión de dos decimales.

**Un número sin fecha y commit es una anécdota.** Esta tabla se actualiza cada vez que corre el eval,
no cuando conviene.

## 5. Casos que se vigilan aparte

Fallos previsibles que el agregado esconde. Se miran uno por uno:

- **Siglas y nombres de producto** ("EAS", "Ktor", "pgvector"): el embedding los diluye.
- **Montos** ("CLP 1,23 M" vs "1,8 millones"): el léxico los acierta, el vector no siempre.
- **Negaciones** ("prefiero no usar X"): recuperar el hecho invertido es peor que no recuperarlo.
- **Duplicados**: dos memorias con coseno > 0,95 deben fusionarse antes de medir; si no, el recall
  sube artificialmente porque el mismo hecho aparece cinco veces.

## 6. Lo que no se mide acá

Calidad de la respuesta del agente en general (eso es del motor), latencia del turno completo
(`operations.md`), y costo por turno (`model_calls`). Mezclar todo en un solo "score" hace imposible
saber qué se rompió.
