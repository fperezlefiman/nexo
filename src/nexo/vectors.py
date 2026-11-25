"""Local embeddings for memory retrieval.

Local on purpose (ADR 0007): the memories are personal facts and they must not leave the house. That
costs a model on disk and CPU inference, which for a few hundred short texts is irrelevant.

`multilingual-e5-large` produces 1024 dimensions and needs its prefixes: passages are embedded with
"passage: " and questions with "query: ". Without them the model still runs and the scores quietly
degrade, which is the kind of bug that shows up as "vectors don't help".
"""

from __future__ import annotations

import os

MODEL_NAME = os.environ.get("NEXO_EMBED_MODEL", "intfloat/multilingual-e5-large")
DIM = 1024
BATCH = 16

_model = None


def _load():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(MODEL_NAME, device="cpu")
    return _model


def embed_passages(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    vectors = _load().encode(
        [f"passage: {t}" for t in texts],
        batch_size=BATCH,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [v.tolist() for v in vectors]


def embed_query(text: str) -> list[float]:
    vector = _load().encode(
        [f"query: {text}"], normalize_embeddings=True, show_progress_bar=False
    )
    return vector[0].tolist()


def index(profile_id: str | None = None, force: bool = False) -> dict:
    """Embed memories that have no vector for the current model. Idempotent by design."""
    from .db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT m.id, m.content
                 FROM memory_items m
                 LEFT JOIN memory_vectors v ON v.memory_id = m.id AND v.model = %s
                WHERE m.deleted_at IS NULL
                  AND (%s::text IS NULL OR m.profile_id = %s)
                  AND (%s OR v.memory_id IS NULL)
                ORDER BY m.id""",
            (MODEL_NAME, profile_id, profile_id, force),
        )
        rows = cur.fetchall()

        done = 0
        for start in range(0, len(rows), BATCH):
            chunk = rows[start : start + BATCH]
            vectors = embed_passages([r["content"] for r in chunk])
            for row, vector in zip(chunk, vectors):
                cur.execute(
                    """INSERT INTO memory_vectors (memory_id, model, embedding)
                       VALUES (%s, %s, %s::vector)
                       ON CONFLICT (memory_id, model)
                       DO UPDATE SET embedding = EXCLUDED.embedding, updated_at = now()""",
                    (row["id"], MODEL_NAME, str(vector)),
                )
                done += 1
            conn.commit()
    return {"model": MODEL_NAME, "indexed": done, "pending": len(rows) - done}


def index_one(memory_id: int, content: str) -> None:
    from .db import connect

    vector = embed_passages([content])[0]
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO memory_vectors (memory_id, model, embedding) VALUES (%s, %s, %s::vector)
               ON CONFLICT (memory_id, model)
               DO UPDATE SET embedding = EXCLUDED.embedding, updated_at = now()""",
            (memory_id, MODEL_NAME, str(vector)),
        )
        conn.commit()


def stats() -> dict:
    from .db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) AS vectors FROM memory_vectors WHERE model = %s""", (MODEL_NAME,)
        )
        vectors = cur.fetchone()["vectors"]
        cur.execute("SELECT count(*) AS memories FROM memory_items WHERE deleted_at IS NULL")
        memories = cur.fetchone()["memories"]
    return {"model": MODEL_NAME, "vectors": vectors, "memories": memories}
