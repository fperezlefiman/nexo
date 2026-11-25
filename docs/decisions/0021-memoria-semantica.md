# ADR 0021 — Memoria semántica: vectores locales, y la fusión híbrida que se descartó al medirla

- **Fecha**: 2026-11-24
- **Estado**: aceptado
- **Reemplaza a**: la parte de ADR 0008 que asumía fusión RRF obligatoria

## Contexto

`memory_search` era `content ILIKE '%query%'` contra 52 memorias. La pregunta del roadmap era si valía
la pena agregar embeddings. Para responderla no alcanzaba con una intuición: había que medir.

Se construyó primero el arnés, antes que el código de vectores:

- **Set dorado** de 36 preguntas escritas sobre las 52 memorias reales (`docs/evals/gold-set.jsonl`),
  35 con respuesta esperada y una sin respuesta. A propósito mezcla paráfrasis (sin palabras en común
  con la memoria) y preguntas que comparten vocabulario, para no favorecer a los vectores por diseño.
- **Cinco recuperadores** medidos con el mismo set: `recall@5` y `MRR`.

## Medición (52 memorias, 35 preguntas con respuesta)

| recuperador | qué hace | recall@5 | MRR |
|---|---|---|---|
| `substring` | lo que corre hoy: la frase entera como patrón ILIKE | **0,000** | 0,000 |
| `keywords` | el mismo ILIKE pero con palabras clave, como lo llama el modelo en la práctica | 0,657 | 0,536 |
| `lexical` | full-text español (`to_tsvector('spanish')` + `ts_rank`) | 0,743 | 0,614 |
| `vector` | coseno sobre `multilingual-e5-large` (1024d) | **0,971** | **0,907** |
| `hybrid` | vector + lexical fusionados con RRF | 0,886 | 0,801 |

Gate del roadmap: recall@5 ≥ 0,80, MRR ≥ 0,65, y **+15 puntos sobre la línea base léxica**.

- `vector`: **+22,8 puntos** sobre `lexical` y +31,4 sobre `keywords`. **Pasa**.
- `hybrid`: +14,3 puntos. **No pasa**, y es peor que el vector solo.

## Decisión

1. **Vectores locales, no API.** Los hechos son personales; `multilingual-e5-large` corre en CPU y no
   sale nada de la casa. Costo real: 4,1 GB de caché de HuggingFace y ~1,5 s de carga del modelo por
   proceso.
2. **`memory_search` usa coseno puro.** La rama léxica queda solo como cobertura: si el índice está
   vacío (o el modelo no está instalado) devuelve la búsqueda por palabras en vez de fallar.
3. **La fusión RRF se descarta, y el código queda para que se pueda re-medir.** No se borra: se deja
   con el motivo escrito, porque el próximo guarde va a querer "mejorar" esto sumando léxico.
4. **Embedding en línea al escribir.** `memory_write` vectoriza la fila nueva antes de responder. Es
   best-effort: si el modelo falla, la memoria se guarda igual y pierde recuperabilidad semántica
   hasta el próximo `nexo vectors index`.

## Por qué la fusión híbrida fue peor

RRF suma `1/(60+rango)` de cada recuperador asumiendo que **ambos son comparables**. Acá no lo son: el
vector acierta 34 de 35 en el top-5 y el léxico 26. Al fusionar, el léxico aporta documentos que el
vector había dejado abajo, y en 4 preguntas desplaza al correcto fuera del top-5.

Subir el peso del vector lo arregla parcialmente (3:1 da 0,971), pero entonces la fusión no aporta
nada: se paga complejidad para obtener el resultado del vector solo. La conclusión no es "el híbrido
es malo", es **"el híbrido hay que medirlo, no asumirlo"**.

## Consecuencias

- `memory_search` gana ~23 puntos de recall y ~30 de MRR. Sobre 52 memorias la diferencia entre
  "encuentra la mitad" y "encuentra casi todo" es la diferencia entre tener memoria y no tenerla.
- El set dorado queda como activo permanente: cualquier cambio de modelo, dimensión o estrategia se
  re-mide con `nexo evals` antes de adoptarse.
- Cambiar de modelo es un re-indexado (`nexo vectors index --force`), no una migración: el nombre del
  modelo es parte de la clave de `memory_vectors`.
- El `ILIKE` de `session_search` no se tocó: mide sobre 227 mensajes y el costo de vectorizar el
  transcript entero no está justificado todavía. Queda anotado como candidato.
