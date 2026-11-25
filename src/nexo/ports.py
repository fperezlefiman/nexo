"""Ports: the contract between the domain and the agent engine.

Nothing here imports an adapter or an HTTP library (ADR 0002).
"""

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol


@dataclass(frozen=True)
class SessionHandle:
    id: str
    model_id: str
    provider_id: str


@dataclass
class TurnResult:
    text: str
    model_id: str
    provider_id: str
    cost: float
    tokens: dict = field(default_factory=dict)
    messages: list = field(default_factory=list)
    latency_ms: int = 0


class AgentRuntime(Protocol):
    def health(self) -> bool: ...

    def open_session(self, model_id: str, provider_id: str = "opencode", title: str = "") -> SessionHandle: ...

    def prompt(self, session_id: str, text: str) -> int: ...

    def wait_turn(self, session_id: str, timeout: float = 180.0, poll: float = 0.15, since: int | None = None) -> TurnResult: ...

    def stream(self, session_id: str, timeout: float = 180.0) -> AsyncIterator[str]: ...

    def interrupt(self, session_id: str) -> None: ...

# Dropped from the contract on 2026-11-24: `set_model`, `context` and `list_sessions` were declared
# here but never implemented by the adapter and never called by the domain — model switching happens
# with a per-conversation override in `conversations.model` plus a new session, not through the
# engine's model endpoint. A `Protocol` is not enforced at runtime (ADR 0002), so nothing caught the
# drift: four live contract tests failed with AttributeError. The contract now lists what exists.
