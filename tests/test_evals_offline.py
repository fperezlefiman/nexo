"""Offline tests for the retrieval metrics. No database, no model: the math and the gold set."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nexo.evals import load_gold, score, terms  # noqa: E402


def test_terms_drops_stopwords_and_short_words():
    assert terms("¿Cómo quieres que sean los avisos?") == ["quieres", "sean", "avisos"]


def test_recall_counts_a_question_as_hit_if_any_expected_id_appears():
    gold = [{"question": "q1", "expected": [1, 2]}, {"question": "q2", "expected": [9]}]

    def retrieve(question, limit=5):
        return [2] if question == "q1" else []

    result = score(gold, retrieve)
    assert result["hits"] == 1
    assert result["n"] == 2
    assert result["recall"] == 0.5


def test_mrr_uses_the_rank_of_the_first_hit():
    gold = [{"question": "q", "expected": [7]}]

    def retrieve(question, limit=5):
        return [3, 5, 7]

    assert score(gold, retrieve)["mrr"] == 1 / 3


def test_questions_without_expected_ids_are_not_counted():
    gold = [{"question": "sin respuesta", "expected": []}, {"question": "q", "expected": [1]}]

    def retrieve(question, limit=5):
        return [1]

    result = score(gold, retrieve)
    assert result["n"] == 1
    assert result["recall"] == 1.0


def test_gold_set_is_well_formed():
    # El set real es privado (preguntas sobre el operador); el repo trae un ejemplo mínimo.
    gold = load_gold()
    assert gold, "the gold set must have at least one question"
    ids = [item["id"] for item in gold]
    assert ids == sorted(ids), "the ids are the stable reference, they must be unique and in order"
    answerable = [item for item in gold if item.get("expected")]
    assert answerable, "a gold set of unanswered questions measures nothing"
    for item in answerable:
        assert all(isinstance(mid, int) for mid in item["expected"]), item
