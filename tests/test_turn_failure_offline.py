"""A turn that dies inside the engine must end `failed`, not stay `running` forever.

Regression for 2026-11-25: `wait_turn` raised RuntimeUnavailable after 180 s, the exception escaped
the worker, and the row kept `running` until the lease expired -- the turn looked stuck and would
have been retried.
"""

import pytest

import nexo.dispatch as dispatch
from nexo.errors import RuntimeUnavailable


@pytest.fixture
def conv(monkeypatch):
    monkeypatch.setattr(dispatch, "get_conversation",
                        lambda _id: {"id": "c1", "model_override": None, "engine_session_id": "s1"})
    monkeypatch.setattr(dispatch, "add_message", lambda *a, **k: None)
    monkeypatch.setattr(dispatch, "system_prompt_for", lambda _ch: "system")
    monkeypatch.setattr(dispatch, "memory_for", lambda *a, **k: [])
    monkeypatch.setattr(dispatch, "recent_messages", lambda *a, **k: [])


def test_runtime_timeout_is_a_failed_turn(conv, monkeypatch):
    def boom(*a, **k):
        raise RuntimeUnavailable("turn did not finish in 600s")

    stored = []
    monkeypatch.setattr(dispatch, "add_message", lambda *a, **k: stored.append(a))
    monkeypatch.setattr(dispatch, "_run_turn", boom)
    out = dispatch.execute_turn(
        {"id": "t1", "conversation_id": "c1", "input_text": "hola"},
        {"default_model": "m", "profile_id": "p"},
        None,
    )
    assert out["state"] == "failed"
    assert out["error"] == "RuntimeUnavailable"
    assert "no se completó" in out["text"]
    # The notice is part of the transcript: an unanswered user message gets replayed next turn.
    assert [call[1] for call in stored] == ["user", "assistant"]
    assert stored[-1][2] == out["text"]


def test_turn_budget_matches_the_stream_budget():
    assert dispatch.TURN_TIMEOUT >= 600
