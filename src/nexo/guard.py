"""Loop guardrails: stop a turn that repeats itself or calls too many tools.

The old system had `tool_loop_guardrails` (warn_after, idempotent_no_progress). Losing it
was a concrete regression: a looping turn burns the allowance with no signal.
"""

import hashlib
import json
import os

MAX_TOOL_CALLS = int(os.environ.get("NEXO_MAX_TOOL_CALLS", "30"))
MAX_SAME_CALL = int(os.environ.get("NEXO_MAX_SAME_CALL", "3"))


class ToolGuard:
    def __init__(self, max_calls: int = MAX_TOOL_CALLS, max_same: int = MAX_SAME_CALL):
        self.max_calls = max_calls
        self.max_same = max_same
        self.calls = 0
        self.seen: dict[str, int] = {}
        self.reason: str | None = None

    def observe(self, name: str, payload) -> bool:
        """True = keep going, False = trip the guard."""
        self.calls += 1
        fingerprint = hashlib.sha256(
            json.dumps([name, payload], sort_keys=True, default=str).encode()
        ).hexdigest()
        self.seen[fingerprint] = self.seen.get(fingerprint, 0) + 1

        if self.calls > self.max_calls:
            self.reason = f"{self.calls} llamadas a herramientas (máximo {self.max_calls})"
            return False
        if self.seen[fingerprint] > self.max_same:
            self.reason = f"la misma llamada a {name} repetida {self.seen[fingerprint]} veces"
            return False
        return True
