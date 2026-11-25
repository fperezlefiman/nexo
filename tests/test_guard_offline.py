"""Loop guardrail tests: the behaviour that Hermes had and we cannot lose."""

from types import SimpleNamespace

import pytest

import nexo.dispatch as dispatch
from nexo.errors import LoopGuardrail, RuntimeUnavailable
from nexo.guard import ToolGuard


def test_allows_normal_use():
    g = ToolGuard(max_calls=5, max_same=2)
    assert g.observe("shell", {"command": "ls"}) is True
    assert g.observe("shell", {"command": "cat a"}) is True
    assert g.reason is None


def test_trips_on_repeated_identical_call():
    g = ToolGuard(max_calls=99, max_same=2)
    assert g.observe("shell", {"command": "ls /nope"}) is True
    assert g.observe("shell", {"command": "ls /nope"}) is True
    assert g.observe("shell", {"command": "ls /nope"}) is False
    assert "repetida" in g.reason


def test_trips_on_total_calls():
    g = ToolGuard(max_calls=3, max_same=99)
    for i in range(3):
        assert g.observe("shell", {"command": f"echo {i}"}) is True
    assert g.observe("shell", {"command": "echo final"}) is False
    assert "máximo" in g.reason


def test_same_tool_with_different_input_is_not_a_loop():
    g = ToolGuard(max_calls=99, max_same=1)
    assert g.observe("shell", {"command": "echo 1"}) is True
    assert g.observe("shell", {"command": "echo 2"}) is True
    assert g.reason is None


class _Feed:
    """The engine's SSE feed, without the engine: yields deltas and calls `on_tool`."""

    def __init__(self, calls: int = 0, die: bool = False):
        self.calls, self.die = calls, die
        self.prompts: list[str] = []

    def prompt(self, session_id, text):
        self.prompts.append(text)

    async def stream(self, session_id, timeout=None, on_tool=None):
        if self.die:
            yield ""
            raise RuntimeUnavailable("feed died")
        for _ in range(self.calls):
            if on_tool and on_tool("bash", {"command": "ls"}) is False:
                return
            yield "delta"

    def wait_turn(self, session_id, timeout=None):
        return SimpleNamespace(text="respuesta", tokens={"input": 1}, cost=0.0,
                               latency_ms=1, model_id="m")


def test_the_guard_watches_tool_calls_even_without_live_text():
    """The worker shows minutes, not text, so nobody passed `on_progress` and the guard never ran
    in production (2026-11-25). The feed is subscribed for the guard now; deltas are ignored."""
    runtime = _Feed(calls=4)  # the fourth identical call trips MAX_SAME_CALL=3
    with pytest.raises(LoopGuardrail):
        dispatch._run_on("s1", "hola", runtime)
    assert runtime.prompts == ["hola"]


def test_a_dead_feed_costs_the_guard_not_the_turn():
    runtime = _Feed(die=True)
    done = dispatch._run_on("s1", "hola", runtime)
    assert done.text == "respuesta"  # polling still returns the answer and its usage
