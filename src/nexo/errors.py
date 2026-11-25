"""Error taxonomy of the runtime port.

The domain never sees HTTP status codes or strings from the engine: the adapter
translates them here (ADR 0002).
"""


class AdapterError(Exception):
    """Base for every failure coming from the agent engine."""

    def __init__(self, detail: str = "", status: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


class RuntimeUnavailable(AdapterError):
    """The engine is not reachable (down, wrong port, restarting)."""


class AuthFailed(AdapterError):
    """The engine rejected our credentials."""


class SessionLost(AdapterError):
    """The session no longer exists on the engine; rebuild context from the transcript."""


class ModelRateLimited(AdapterError):
    """The model refused for quota reasons (free tier window, 429)."""


class ModelUnavailable(AdapterError):
    """The requested model is unknown to the engine."""


class ToolFailed(AdapterError):
    """A tool executed by the engine reported failure."""


class LoopGuardrail(AdapterError):
    """The turn was interrupted by our own guard: repeated or excessive tool calls."""


class Unknown(AdapterError):
    """Anything the adapter could not classify."""
