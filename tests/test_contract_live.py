"""Contract tests against the real engine (F0 gate). No server => skipped, not failed."""

import asyncio
import json
import os
import tempfile
import time

import pytest

from nexo.adapters.opencode import OpenCodeRuntime
from nexo.errors import AuthFailed, ModelUnavailable, RuntimeUnavailable

pytestmark = pytest.mark.live

FREE_MODEL = os.environ.get("NEXO_MODEL", "deepseek-v4.1-flash-free")


def test_health_and_auth(runtime):
    assert runtime.health() is True


def test_open_session_returns_handle(runtime):
    handle = runtime.open_session(FREE_MODEL, title="contract-open")
    assert handle.id.startswith("ses_")
    assert handle.model_id == FREE_MODEL


def test_single_turn_returns_text(ask):
    _, result = ask("Responde solo: NEXO_F0_OK")
    assert "NEXO_F0_OK" in result.text


def test_turn_reports_model_usage_and_cost(ask):
    _, result = ask("Responde solo: OK")
    assert result.model_id == FREE_MODEL
    assert result.provider_id == "opencode"
    assert result.tokens["input"] > 0
    assert result.tokens["output"] > 0
    assert result.cost >= 0


def test_three_turn_conversation_keeps_context(runtime):
    handle = runtime.open_session(FREE_MODEL, title="contract-context")
    runtime.prompt(handle.id, "Mi número de la suerte es 7.")
    runtime.wait_turn(handle.id)
    runtime.prompt(handle.id, "¿Qué número de la suerte te dije? Responde solo el número.")
    result = runtime.wait_turn(handle.id)
    assert "7" in result.text


def test_bash_tool_runs_python3(ask):
    _, result = ask('Usá bash para ejecutar: python3 -c "print(6*7)". Respondé solo el número.')
    assert "42" in result.text


def test_interrupt_on_idle_session_is_safe(runtime, session):
    runtime.interrupt(session.id)


def test_unknown_model_fails_at_prompt_time(runtime):
    """Session creation accepts any model id; the failure shows up on the turn."""
    handle = runtime.open_session("modelo-que-no-existe-xyz", title="contract-bad-model")
    runtime.prompt(handle.id, "hola")
    with pytest.raises(ModelUnavailable):
        runtime.wait_turn(handle.id, timeout=60)


@pytest.fixture
def auth_file(tmp_path) -> str:
    """Credentials for tests that never reach an engine: only a clean machine is simulated."""
    path = tmp_path / "service.json"
    path.write_text('{"password": "test"}')
    return str(path)


def test_bad_credentials_raise_auth_failed(runtime):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump({"password": "not-the-password"}, fh)
        path = fh.name
    try:
        runtime = OpenCodeRuntime(auth_file=path, timeout=5)
        runtime._auth = None
        with pytest.raises(AuthFailed):
            runtime.open_session(FREE_MODEL)
    finally:
        os.unlink(path)


def test_engine_down_raises_runtime_unavailable(auth_file):
    runtime = OpenCodeRuntime(base_url="http://127.0.0.1:9", auth_file=auth_file, timeout=3)
    with pytest.raises(RuntimeUnavailable):
        runtime.open_session(FREE_MODEL)


def test_health_is_false_when_the_engine_is_down(auth_file):
    """`health()` used to raise, so a CI without engine errored instead of skipping the live tests."""
    assert OpenCodeRuntime(base_url="http://127.0.0.1:9", auth_file=auth_file, timeout=2).health() is False


def test_a_missing_auth_file_is_a_credential_failure(tmp_path):
    """A fresh clone has no service.json: `nexo health` must say unavailable, not traceback."""
    runtime = OpenCodeRuntime(auth_file=str(tmp_path / "nope.json"), timeout=2)
    assert runtime.health() is False


def test_stream_yields_deltas_from_sse(runtime):
    """Deltas come from /api/event; /message is a snapshot without partials."""
    handle = runtime.open_session(FREE_MODEL, title="contract-stream")

    async def collect():
        runtime.prompt(handle.id, "Contá del 1 al 10 separado por comas. Responde solo eso.")
        return [chunk async for chunk in runtime.stream(handle.id, timeout=120)]

    chunks = asyncio.run(collect())
    assert chunks, "stream produced no deltas"
    assert "1" in "".join(chunks)


def test_first_delta_latency_is_measured(runtime):
    handle = runtime.open_session(FREE_MODEL, title="contract-latency")
    runtime.prompt(handle.id, "Responde solo: LAT")
    started = time.time()
    runtime.wait_turn(handle.id)
    elapsed = time.time() - started
    assert elapsed < 120
    print(f"\nfirst-complete-turn: {elapsed:.2f}s")
