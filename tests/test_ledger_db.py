"""Ledger de costos: una fila por turno, con lo que reportó el motor.

La deuda anterior era al revés: `operations.md` alertaba sobre `model_calls` y la tabla no existía.
Este test fija las dos cosas que importan: la fila se escribe y los números son los del engine,
no una estimación.
"""

import pytest

from nexo.db import connect
from nexo.store import record_model_call

TURN = "trn_ledger_test"


def _profile_id():
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM profiles ORDER BY id LIMIT 1")
        row = cur.fetchone()
    return row["id"] if row else None


def _cleanup():
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM model_calls WHERE turn_id = %s", (TURN,))


def test_row_is_written_with_engine_numbers():
    profile = _profile_id()
    if not profile:
        pytest.skip("sin perfiles en la base")
    _cleanup()
    record_model_call(TURN, profile, {
        "model_id": "deepseek-v4.1-flash",
        "tokens": {"input": 1200, "output": 340},
        "cost": 0.0042,
        "latency_ms": 178000,
    })
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT profile_id, model_id, tokens_input, tokens_output, cost_usd, latency_ms "
            "FROM model_calls WHERE turn_id = %s", (TURN,)
        )
        row = cur.fetchone()
    _cleanup()
    assert row["profile_id"] == profile
    assert row["model_id"] == "deepseek-v4.1-flash"
    assert (row["tokens_input"], row["tokens_output"]) == (1200, 340)
    assert float(row["cost_usd"]) == pytest.approx(0.0042)
    assert row["latency_ms"] == 178000


def test_missing_usage_does_not_explode():
    profile = _profile_id()
    if not profile:
        pytest.skip("sin perfiles en la base")
    _cleanup()
    record_model_call(TURN, profile, {})
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT tokens_input, tokens_output, cost_usd FROM model_calls WHERE turn_id = %s", (TURN,))
        row = cur.fetchone()
    _cleanup()
    assert row["tokens_input"] == 0 and row["tokens_output"] == 0
