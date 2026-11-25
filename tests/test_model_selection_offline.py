"""One model per profile: `provider/model`, no automatic fallback (2026-11-25)."""

from types import SimpleNamespace

import pytest

import nexo.dispatch as dispatch
from nexo.errors import ModelRateLimited, ModelUnavailable


def test_entry_without_provider_uses_the_default():
    assert dispatch.parse_model("deepseek-v4.1-flash") == ("deepseek-v4.1-flash", "opencode")


def test_entry_with_provider_splits_on_the_first_slash():
    assert dispatch.parse_model("opencode-go/deepseek-v4.1-flash") == (
        "deepseek-v4.1-flash", "opencode-go")


class FakeRuntime:
    """Fails whatever happens inside a turn, and records which sessions it opened."""

    def __init__(self, error=None):
        self.error = error
        self.opened: list[tuple[str, str]] = []

    def open_session(self, model, provider_id=None, title=""):
        self.opened.append((model, provider_id))
        return SimpleNamespace(id=f"s-{model}")

    def prompt(self, session_id, text):
        if self.error:
            raise self.error

    def wait_turn(self, session_id, timeout=None):
        return SimpleNamespace(text="respuesta", tokens={}, cost=0.0, latency_ms=1, model_id="m")

    async def stream(self, session_id, timeout=None, on_tool=None):
        yield ""  # the feed only feeds the guard; the failure comes from prompt()


@pytest.mark.parametrize("error", [ModelRateLimited("429"), ModelUnavailable("404")])
def test_a_dead_model_fails_the_turn_and_names_itself(monkeypatch, error):
    monkeypatch.setattr(dispatch, "get_conversation",
                        lambda _id: {"id": "c1", "model_override": None, "engine_session_id": None})
    monkeypatch.setattr(dispatch, "add_message", lambda *a, **k: None)
    monkeypatch.setattr(dispatch, "system_prompt_for", lambda _ch: "system")
    monkeypatch.setattr(dispatch, "memory_for", lambda *a, **k: [])
    monkeypatch.setattr(dispatch, "recent_messages", lambda *a, **k: [])
    monkeypatch.setattr(dispatch, "set_engine_session", lambda *a, **k: None)

    runtime = FakeRuntime(error)
    out = dispatch.execute_turn(
        {"id": "t1", "conversation_id": "c1", "input_text": "hola"},
        {"default_model": "opencode-go/deepseek-v4.1-flash", "profile_id": "p"},
        runtime,
    )
    assert out["state"] == "failed"
    assert "deepseek-v4.1-flash" in out["text"] and "/models" in out["text"]
    assert len(runtime.opened) == 1, "no second model must be tried"


def test_the_override_wins_over_the_profile_model(monkeypatch):
    seen = []
    monkeypatch.setattr(dispatch, "set_engine_session", lambda *a, **k: None)
    monkeypatch.setattr(dispatch, "get_conversation", lambda _id: {
        "id": "c1", "model_override": "opencode/free-one", "engine_session_id": None})
    monkeypatch.setattr(dispatch, "_run_on", lambda sid, prompt, rt: (seen.append(sid), ("ok", {}))[1])
    runtime = FakeRuntime()
    dispatch._run_turn({"id": "c1", "model_override": "opencode/free-one"}, "hola",
                       "opencode/free-one", runtime)
    assert runtime.opened == [("free-one", "opencode")]
