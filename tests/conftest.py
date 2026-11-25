"""Shared fixtures. Live tests need the engine; they skip instead of failing when it is down."""

import os

import pytest

from nexo.adapters.opencode import OpenCodeRuntime
from nexo.errors import ModelRateLimited

FREE_MODEL = os.environ.get("NEXO_MODEL", "deepseek-v4.1-flash-free")


class _SkipOnQuota:
    """A quota failure mid-run is an environment condition too, not a code failure.

    The probe below only proves the window was open when the session started; it can close during
    the suite, and four live tests then failed as if the code were broken (2026-11-25).
    """

    def __init__(self, runtime: OpenCodeRuntime):
        self._runtime = runtime

    def __getattr__(self, name):
        return getattr(self._runtime, name)

    def wait_turn(self, *args, **kwargs):
        try:
            return self._runtime.wait_turn(*args, **kwargs)
        except ModelRateLimited:
            pytest.skip(f"{FREE_MODEL} free window exhausted mid-run")


@pytest.fixture(scope="session")
def runtime() -> OpenCodeRuntime:
    rt = OpenCodeRuntime()
    if not rt.health():
        pytest.skip("OpenCode server not reachable on 127.0.0.1:49374")
    # A rate-limited model is an environment condition, not a code failure: skip, never fail.
    # Export NEXO_MODEL=deepseek-v4.1-flash to run the suite against the paid model.
    handle = rt.open_session(FREE_MODEL, title="pytest-probe")
    rt.prompt(handle.id, "ok")
    try:
        rt.wait_turn(handle.id, timeout=90)
    except ModelRateLimited:
        pytest.skip(f"{FREE_MODEL} free window exhausted; export NEXO_MODEL to test with a paid model")
    return _SkipOnQuota(rt)


@pytest.fixture(scope="session")
def session(runtime):
    """One shared session for cheap read-only assertions."""
    return runtime.open_session(FREE_MODEL, title="pytest-contract")


@pytest.fixture
def ask(runtime):
    """Open a session, send one prompt, wait for the turn. Returns TurnResult."""

    def _ask(text: str, model: str = FREE_MODEL, timeout: float = 180.0):
        handle = runtime.open_session(model, title="pytest-turn")
        runtime.prompt(handle.id, text)
        return handle, runtime.wait_turn(handle.id, timeout=timeout)

    return _ask
