"""Retrieval evaluation: does the new memory search actually beat the one that is live today?

The gate in the roadmap says hybrid search must beat the lexical baseline by 15 points of recall@5.
That number is meaningless without a gold set and a harness that runs both retrievers over the same
questions, so this module exists before any embedding does.

Every retriever returns the memory ids it would have shown, best first. Metrics are computed on ids.
"""

from __future__ import annotations

import json
import os
import unicodedata
import re
from pathlib import Path

from .db import connect

GOLD = Path(os.environ.get(
    "NEXO_EVALS_GOLD",
    Path(__file__).resolve().parent.parent.parent / "docs" / "evals" / "gold-set.jsonl",
))

# Words too common to mean anything ("cuando", "quiero", "hace"). Postgres' spanish config already
# drops most of these; this list only keeps the SQL readable.
STOP = {
    "que", "los", "las", "del", "por", "para", "con", "una", "uno", "mis", "tus", "sus", "como",
    "cuando", "donde", "cuanto", "cuantos", "cual", "cuales", "hay", "tiene", "tengo", "hace",
    "haces", "eres", "esta", "este", "esto", "son", "muy", "mas", "pero", "sin", "sobre", "entre",
    "todo", "todos", "algo", "cada", "otra", "otro", "solo", "tambien", "puede", "puedo", "quiero",
}


def load_gold(path: Path | None = None, split: str | None = None) -> list[dict]:
    """Read the gold set. The split is stored in the file, not derived by a hash.

    A hash would reshuffle the test set the day someone touches the function, which is exactly how a
    test set stops being a test set. `split` lives next to the question, so it is reviewable in the diff.
    """
    path = path or GOLD
    with open(path, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    if split and split != "all":
        rows = [r for r in rows if r.get("split") == split]
    return rows


def terms(question: str) -> list[str]:
    plain = unicodedata.normalize("NFD", question.lower())
    plain = "".join(c for c in plain if unicodedata.category(c) != "Mn")
    words = re.findall(r"[a-zñü]+", plain)
    return [w for w in words if len(w) > 2 and w not in STOP]


def retrieve_substring(question: str, limit: int = 5) -> list[int]:
    """What memory_search does today: the whole question as an ILIKE pattern."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id FROM memory_items
                WHERE deleted_at IS NULL AND content ILIKE %s
                ORDER BY created_at DESC LIMIT %s""",
            (f"%{question}%", limit),
        )
        return [r["id"] for r in cur.fetchall()]


def retrieve_substring_terms(question: str, limit: int = 5) -> list[int]:
    """The same ILIKE search, but with keywords instead of a whole sentence.

    This is how the tool is actually called by the model in practice, so it is the fair reading of
    "what runs today": a keyword matcher, not a question answerer.
    """
    words = terms(question)
    if not words:
        return []
    pattern = "|".join(re.escape(w) for w in words)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id,
                      (SELECT count(*) FROM unnest(%s::text[]) w WHERE content ILIKE '%%'||w||'%%') AS hits
                 FROM memory_items
                WHERE deleted_at IS NULL AND content ~* %s
                ORDER BY hits DESC, created_at DESC LIMIT %s""",
            (words, pattern, limit),
        )
        return [r["id"] for r in cur.fetchall()]


def retrieve_lexical(question: str, limit: int = 5) -> list[int]:
    """Fair lexical baseline: Spanish full text search, ranked by ts_rank."""
    words = terms(question)
    if not words:
        return []
    tsquery = " | ".join(words)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id FROM memory_items
                WHERE deleted_at IS NULL
                  AND to_tsvector('spanish', content) @@ to_tsquery('spanish', %s)
                ORDER BY ts_rank(to_tsvector('spanish', content), to_tsquery('spanish', %s)) DESC,
                         created_at DESC
                LIMIT %s""",
            (tsquery, tsquery, limit),
        )
        return [r["id"] for r in cur.fetchall()]


def retrieve_vector(question: str, limit: int = 5) -> list[int]:
    """Cosine over memory vectors. This is the retriever that is live in `memory_search`."""
    from .vectors import embed_query

    vector = str(embed_query(question))
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT v.memory_id FROM memory_vectors v
                 JOIN memory_items m ON m.id = v.memory_id
                WHERE m.deleted_at IS NULL
                ORDER BY v.embedding <=> %s::vector LIMIT %s""",
            (vector, limit),
        )
        return [r["memory_id"] for r in cur.fetchall()]


def retrieve_hybrid(question: str, limit: int = 5) -> list[int]:
    """Vector plus lexical fused with RRF. Kept for the record: it measured *worse*, see ADR 0021."""
    from .vectors import embed_query

    words = terms(question)
    vector_hits: list[int] = []
    vector = embed_query(question)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT v.memory_id FROM memory_vectors v
                 JOIN memory_items m ON m.id = v.memory_id
                WHERE m.deleted_at IS NULL
                ORDER BY v.embedding <=> %s::vector LIMIT 20""",
            (str(vector),),
        )
        vector_hits = [r["memory_id"] for r in cur.fetchall()]
    lexical_hits = retrieve_lexical(question, limit=20) if words else []

    scores: dict[int, float] = {}
    for rank, mid in enumerate(vector_hits, start=1):
        scores[mid] = scores.get(mid, 0) + 1 / (60 + rank)
    for rank, mid in enumerate(lexical_hits, start=1):
        scores[mid] = scores.get(mid, 0) + 1 / (60 + rank)
    return [mid for mid, _ in sorted(scores.items(), key=lambda kv: -kv[1])][:limit]


RETRIEVERS = {
    "substring": retrieve_substring,
    "keywords": retrieve_substring_terms,
    "lexical": retrieve_lexical,
    "vector": retrieve_vector,
    "hybrid": retrieve_hybrid,
}


def score(gold: list[dict], retrieve, k: int = 5) -> dict:
    """recall@k over questions that have an answer, MRR over all, plus the misses to look at."""
    hits = 0
    answerable = 0
    rr_total = 0.0
    misses = []
    for item in gold:
        expected = set(item.get("expected") or [])
        got = retrieve(item["question"], limit=k)
        if not expected:
            continue
        answerable += 1
        if expected & set(got):
            hits += 1
        for rank, mid in enumerate(got, start=1):
            if mid in expected:
                rr_total += 1 / rank
                break
        else:
            misses.append(item["question"])
    return {
        "recall": hits / answerable if answerable else 0.0,
        "mrr": rr_total / answerable if answerable else 0.0,
        "hits": hits,
        "n": answerable,
        "misses": misses,
    }


def run(retriever: str = "lexical", k: int = 5, split: str = "all") -> dict:
    gold = load_gold(split=split)
    return score(gold, RETRIEVERS[retriever], k=k)
