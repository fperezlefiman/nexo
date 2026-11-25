"""Adapter for the OpenCode HTTP server (experimental API, ADR 0001/0002).

Verified against the live server on 2026-11-24:
- auth is HTTP Basic, user ``opencode`` and the password from service.json;
- POST /prompt returns *immediately* (queued), so a turn is completed by polling
  /message until an ``idle`` record appears after our user message;
- assistant messages carry model, cost and tokens, which is our cost ledger.
"""

import asyncio
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..errors import (
    AuthFailed,
    ModelRateLimited,
    ModelUnavailable,
    RuntimeUnavailable,
    SessionLost,
    ToolFailed,
    Unknown,
)
from ..ports import SessionHandle, TurnResult

DEFAULT_BASE_URL = "http://127.0.0.1:49374"
DEFAULT_AUTH_FILE = os.path.expanduser("~/.config/opencode/service.json")


def _auth_header(auth_file: str) -> str:
    """Basic auth from the engine's service.json.

    A machine that never ran the engine has no file: that is a credential problem, not a crash. It
    used to escape as FileNotFoundError, so `health()` never answered and the suite errored instead
    of skipping (2026-11-25).
    """
    try:
        password = json.loads(Path(auth_file).read_text())["password"]
    except (OSError, KeyError, ValueError):
        raise AuthFailed(f"no readable engine credentials at {auth_file}") from None
    return "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode()


class OpenCodeRuntime:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, auth_file: str = DEFAULT_AUTH_FILE, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.auth_file = auth_file
        self.timeout = timeout
        self._auth = None
        self._since: dict[str, int] = {}

    # -- transport ---------------------------------------------------------
    def _auth_header(self) -> str:
        if self._auth is None:
            self._auth = _auth_header(self.auth_file)
        return self._auth

    def _request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Authorization": self._auth_header(), "Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw.strip() else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            raise self._classify(exc.code, raw, path) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise RuntimeUnavailable(f"{method} {path}: {exc}") from None

    @staticmethod
    def _classify(status: int, raw: str, path: str):
        lowered = raw.lower()
        if status == 401:
            return AuthFailed(raw[:200], status)
        if status == 429 or "freeusagelimit" in lowered:
            return ModelRateLimited(raw[:200], status)
        # Only a path carrying a session id can mean "the session is gone".
        if status == 404 and path.count("/") >= 4:
            return SessionLost(raw[:200], status)
        if status == 404 or "model" in lowered:
            return ModelUnavailable(raw[:200], status)
        return Unknown(f"{status}: {raw[:200]}", status)

    # -- port --------------------------------------------------------------
    def health(self) -> bool:
        """True only when the engine answers. Down is an answer: the test suite skips, not errors."""
        try:
            self._request("GET", "/api/session")
            return True
        except (AuthFailed, RuntimeUnavailable):
            return False

    def open_session(self, model_id: str, provider_id: str = "opencode", title: str = "") -> SessionHandle:
        _, payload = self._request(
            "POST",
            "/api/session",
            {"title": title, "model": {"id": model_id, "providerID": provider_id}},
        )
        session = (payload or {}).get("data") or {}
        if "id" not in session:
            raise Unknown(f"unexpected session payload: {str(payload)[:200]}")
        return SessionHandle(id=session["id"], model_id=model_id, provider_id=provider_id)

    def prompt(self, session_id: str, text: str) -> int:
        """Queue a prompt. Returns the created timestamp of the user message.

        The engine answers asynchronously: POST /prompt returns immediately, so the
        returned timestamp is what later marks "messages of this turn".
        """
        _, payload = self._request("POST", f"/api/session/{session_id}/prompt", {"text": text})
        created = ((payload or {}).get("data") or {}).get("time", {}).get("created")
        self._since[session_id] = int(created or time.time() * 1000)
        return self._since[session_id]

    def wait_turn(self, session_id: str, timeout: float = 180.0, poll: float = 0.15, since: int | None = None) -> TurnResult:
        since = since or self._since.get(session_id) or 0
        started = time.time()
        while time.time() - started < timeout:
            messages = self._turn_messages(session_id, since)
            idle = next((m for m in messages if m.get("type") == "idle"), None)
            if idle:
                # The engine reports a failed turn as an idle record with an error, not
                # as an HTTP error; ignoring this returns an empty answer silently.
                if idle.get("outcome") == "failed":
                    raise self._classify_turn(idle.get("error") or {})
                assistants = [m for m in messages if m.get("type") == "assistant"]
                return _result(assistants, int((time.time() - started) * 1000))
            time.sleep(poll)
        raise RuntimeUnavailable(f"turn did not finish in {timeout}s")

    @staticmethod
    def _classify_turn(error: dict):
        kind = str(error.get("type") or "")
        message = str(error.get("message") or "")
        detail = f"{kind}: {message}"[:300]
        if "unavailable" in message.lower() or "no-route" in kind:
            return ModelUnavailable(detail)
        if "rate" in kind or "limit" in message.lower():
            return ModelRateLimited(detail)
        return ToolFailed(detail)

    async def stream(self, session_id: str, timeout: float = 180.0, on_tool=None):
        """Yield text deltas from the engine's SSE feed.

        /api/session/{id}/message is a snapshot: it only shows finished messages (verified
        2026-11-24, 0 chars until the whole answer arrived). Deltas only come from
        GET /api/event, so that is the source for streamed replies.

        `on_tool(name, payload)` sees every tool call; returning False interrupts the turn.
        """
        import threading

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        end = object()

        def pump() -> None:
            try:
                req = urllib.request.Request(
                    self.base_url + "/api/event",
                    headers={"Authorization": self._auth_header(), "Accept": "text/event-stream"},
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    for raw in resp:
                        line = raw.decode("utf-8", "replace").strip()
                        if not line.startswith("data:"):
                            continue
                        try:
                            event = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        data = event.get("data") or {}
                        if data.get("sessionID") != session_id:
                            continue
                        kind = event.get("type")
                        if kind == "session.text.delta":
                            loop.call_soon_threadsafe(queue.put_nowait, data.get("delta", ""))
                        elif kind == "session.tool.called":
                            loop.call_soon_threadsafe(
                                queue.put_nowait, ("tool", (data.get("name", ""), data.get("input"))))
                        elif kind == "session.step.ended":
                            loop.call_soon_threadsafe(queue.put_nowait, end)
                            return
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                loop.call_soon_threadsafe(queue.put_nowait, RuntimeUnavailable(str(exc)))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, end)

        threading.Thread(target=pump, daemon=True).start()
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise RuntimeUnavailable(f"stream did not finish in {timeout}s")
            item = await asyncio.wait_for(queue.get(), timeout=remaining)
            if item is end:
                return
            if isinstance(item, Exception):
                raise item
            if isinstance(item, tuple) and item and item[0] == "tool":
                name, payload = item[1]
                if on_tool and on_tool(name, payload) is False:
                    self.interrupt(session_id)
                    return
                continue
            if item:
                yield item

    def interrupt(self, session_id: str) -> None:
        self._request("POST", f"/api/session/{session_id}/interrupt")

    # -- helpers -----------------------------------------------------------
    def _messages(self, session_id: str) -> list:
        _, payload = self._request("GET", f"/api/session/{session_id}/message")
        return (payload or {}).get("data", [])

    def _turn_messages(self, session_id: str, since: int) -> list:
        """Messages created at or after `since`. The list is not chronological."""
        return [m for m in self._messages(session_id) if int((m.get("time") or {}).get("created") or 0) >= since]


def _result(assistants: list, latency_ms: int) -> TurnResult:
    text = "".join(
        part.get("text", "")
        for msg in assistants
        for part in msg.get("content", [])
        if part.get("type") == "text"
    )
    last = assistants[-1] if assistants else {}
    model = last.get("model", {})
    return TurnResult(
        text=text,
        model_id=model.get("id", ""),
        provider_id=model.get("providerID", ""),
        cost=sum(float(m.get("cost") or 0) for m in assistants),
        tokens=_sum_tokens(assistants),
        messages=assistants,
        latency_ms=latency_ms,
    )


def _sum_tokens(assistants: list) -> dict:
    total = {"input": 0, "output": 0}
    for msg in assistants:
        tokens = msg.get("tokens") or {}
        total["input"] += int(tokens.get("input") or 0)
        total["output"] += int(tokens.get("output") or 0)
    return total
