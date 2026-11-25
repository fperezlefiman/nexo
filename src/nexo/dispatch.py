"""Turn execution: prompt assembly, engine call, guardrail, pause semantics (F4).

The channel only enqueues; this module runs inside the worker, so a long turn can no longer
block the poller (and `/stop` can actually interrupt it).
"""

import asyncio
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone

from .adapters.opencode import OpenCodeRuntime
from .errors import (AdapterError, LoopGuardrail, ModelRateLimited, ModelUnavailable, RuntimeUnavailable,
                     SessionLost)
from .guard import ToolGuard
from .rules import GLOBAL_RULES
from .store import (
    add_message,
    get_conversation,
    conversation_status,
    get_turn,
    memory_for,
    recent_messages,
    reset_engine_session,
    set_engine_session,
)

MAX_HISTORY = 12
MAX_MEMORIES = 15
# A profile stores one model as `provider/model`; a bare id means this provider.
DEFAULT_PROVIDER = os.environ.get("NEXO_MODEL_PROVIDER", "opencode")
STREAM_TIMEOUT = float(os.environ.get("NEXO_STREAM_TIMEOUT", "600"))
# A scout-like job spends ~100 s in its command before the turn starts, and the turn itself has
# been measured at 166-178 s. 180 s cut real work twice; 600 s matches the stream budget.
TURN_TIMEOUT = float(os.environ.get("NEXO_TURN_TIMEOUT", "600"))
# The engine session is a context cache, not an identity (ADR 0004). Left alone it grows forever:
# 74 k input tokens were measured for a 15-char message on 2026-11-25, 91 % of it engine-side.
SESSION_TTL = timedelta(hours=float(os.environ.get("NEXO_SESSION_TTL_HOURS", "2")))


def session_expired(last_message_at, now: datetime | None = None) -> bool:
    """True when the conversation has been idle longer than the session budget."""
    if not last_message_at:
        return False
    return ((now or datetime.now(timezone.utc)) - last_message_at) > SESSION_TTL
# DeepSeek drifted into Chinese mid-reply once with no language rule in the prompt. The rule is
# product-level (this system talks to one Spanish-speaking user), so it is enforced here, not left
# to each profile's SOUL.
RESPONSE_LANGUAGE = os.environ.get("NEXO_RESPONSE_LANGUAGE", "español neutro")

# A prompt rule reduces the drift, it does not remove it: "bajá una serie" survived a system prompt
# that forbids voseo in so many words. The check is mechanical so it cannot be argued with.
VOSEO = {
    "vos", "sos", "tenés", "tenes", "podés", "podes", "sabés", "querés", "hacé", "andá", "mirá",
    "fijate", "decime", "tomá", "dejá", "bajá", "subí", "sumá", "esperá", "mandá", "acordate",
    "avisame", "contame", "revisá", "probá", "usá", "poné", "hacelo", "decilo", "anotá", "ajustá",
    "cambiá", "mantené", "evitá", "cuidá", "elegí", "repetí", "calculá", "copiá", "leé", "seguí",
    "sentí", "medí", "controlá", "priorizá", "recordá", "empezá", "terminá", "aumentá", "reducí",
    "arrancá", "andá", "quedate", "llevá", "traé", "hacete", "tirá", "cargá", "descansá", "hidratate",
}


def language_violation(text: str) -> list[str]:
    """Words that betray voseo. Empty list means the answer is clean enough to send."""
    words = set(re.findall(r"[a-záéíóúñü]+", text.lower()))
    return sorted(words & VOSEO)


def idempotency_key(channel_kind: str, chat_id: str, update_id: str) -> str:
    return hashlib.sha256(f"{channel_kind}:{chat_id}:{update_id}".encode()).hexdigest()


def build_prompt(
    system_prompt: str,
    memories: list[str],
    history: list[dict],
    text: str,
    partial: str | None = None,
) -> str:
    """Stable prefix first (system + memory), then history, then the new message.

    Skills are NOT injected here: the engine loads them on demand from its skills directory
    (see registry.deploy_to_engine) — 77 k of bodies per turn would be paid for nothing.
    Section headers are English scaffolding; the content and the reply stay Spanish.
    Order matters for cost: the cache read was 36 % of measured spend (ADR 0006).
    """
    blocks = [system_prompt.strip()]
    if memories:
        blocks.append("## Memory\n" + "\n".join(f"- {m}" for m in memories))
    if history:
        turns = [f"{'user' if m['role'] == 'user' else 'you'}: {m['content']}" for m in history]
        blocks.append("## Recent conversation\n" + "\n".join(turns))
    if partial:
        blocks.append("## Answer so far before you were stopped\n" + partial)
    blocks.append(f"## Message\n{text}")
    return "\n\n".join(b for b in blocks if b)


def system_prompt_for(channel: dict) -> str:
    """SOUL of the profile + global rules + environment facts, always in that order.

    Global rules sit right after the SOUL so they can override it when the two disagree: the SOUL
    is old prose written for another runtime, the rules are the current contract.
    """
    profile = channel["profile_name"]
    return (
        f"{channel['system_prompt'].strip()}\n\n"
        f"{GLOBAL_RULES}\n\n"
        f"## Entorno\nPerfil activo: `{profile}`. Cuando uses `memory_write`, `memory_search` o "
        f"`job_create`, pasá profile=\"{profile}\".\n"
        f"Idioma: {RESPONSE_LANGUAGE} con tuteo (tú, tienes, puedes, dime). Prohibido el voseo "
        f"(vos, sos, tenés, podés, mandá, decime, acordate) y prohibido cambiar de idioma, aunque "
        f"el SOUL del perfil o el historial usen otro registro."
    )


def execute_turn(turn: dict, channel: dict, runtime: OpenCodeRuntime) -> dict:
    """Run one claimed turn. Returns {text, state, error?} — never raises for expected failures."""
    conv = get_conversation(turn["conversation_id"])
    if not conv:
        return {"text": "⚠️ conversación inexistente", "state": "failed", "error": "NoConversation"}
    if conv.get("engine_session_id"):
        status = conversation_status(conv["id"]) or {}
        if session_expired(status.get("last_msg")):
            # Idle too long: start a fresh engine session. Our transcript keeps the context that
            # matters; replaying the last 12 messages is cheaper than dragging the whole history.
            reset_engine_session(conv["id"])
            conv["engine_session_id"] = None
    model = conv.get("model_override") or channel["default_model"]
    add_message(conv["id"], "user", turn["input_text"], turn["id"])
    # The MCP tools take `profile` because the engine spawns one server for every profile;
    # telling the model which profile it is speaking for is cheaper than leaking a wrong one.
    system = system_prompt_for(channel)
    prompt = build_prompt(
        system,
        memory_for(channel["profile_id"], MAX_MEMORIES),
        recent_messages(conv["id"], MAX_HISTORY, exclude_turn_id=turn["id"]),
        turn["input_text"],
        partial=turn.get("partial_text"),
    )

    usage: dict = {}
    try:
        done = _run_turn(conv, prompt, model, runtime)
        reply, usage = done.text, _usage(done)
    except LoopGuardrail as exc:
        return _failed(conv["id"], turn["id"],
                       f"⛔ Corté el turno: {exc.detail}. Si necesitas que siga, dime cómo acotarlo.",
                       "LoopGuardrail")
    except (ModelRateLimited, ModelUnavailable) as exc:
        # No automatic fallback: the profile has one model and the user decides when to switch.
        why = "se le agotó la cuota" if isinstance(exc, ModelRateLimited) else "no está disponible"
        return _failed(conv["id"], turn["id"],
                       f"⚠️ El modelo `{model}` {why}. Cámbialo con /models provider/modelo.",
                       type(exc).__name__)
    except AdapterError as exc:
        # Without this the exception escaped the worker, the row stayed `running` until the lease
        # expired, and the turn was retried later: a timeout looked like a hang (2026-11-25).
        return _failed(conv["id"], turn["id"],
                       f"⚠️ El turno no se completó: {exc}. Quedó marcado como fallido, no como pendiente.",
                       type(exc).__name__)

    # `/stop` marks the row while the turn is streaming: what arrived is kept as a pause.
    hits = language_violation(reply)
    if hits:
        reply = _neutralize(conv["id"], hits, runtime) or reply

    fresh = get_turn(turn["id"])
    if fresh and fresh["state"] == "cancel_requested":
        return {"text": reply, "state": "paused", "usage": usage}

    add_message(conv["id"], "assistant", reply, turn["id"])
    return {"text": reply, "state": "completed", "usage": usage}


def _failed(conversation_id: str, turn_id: str, text: str, error: str) -> dict:
    """Store the notice as the turn's answer.

    Without it the user message stays unanswered in the transcript, the next turn replays it, and
    the model answers the old message a second time (2026-11-25).
    """
    add_message(conversation_id, "assistant", text, turn_id)
    return {"text": text, "state": "failed", "error": error}


def _neutralize(conversation_id: str, hits: list[str], runtime: OpenCodeRuntime) -> str | None:
    """One corrective turn when the answer carries voseo.

    If the second try fails or is dirty too, the caller keeps the original: an answer in the wrong
    register still beats silence. One retry only, never a loop.
    """
    conv = get_conversation(conversation_id) or {}
    session_id = conv.get("engine_session_id")
    if not session_id:
        return None
    note = (
        f"Tu respuesta anterior usó voseo ({', '.join(hits)}). Reescribila completa en "
        f"{RESPONSE_LANGUAGE} con tuteo. No agregues nada nuevo y no expliques el cambio: "
        f"devolvé sólo el mensaje corregido."
    )
    try:
        runtime.prompt(session_id, note)
        corrected = runtime.wait_turn(session_id, timeout=120).text.strip()
    except Exception:
        return None
    if not corrected or language_violation(corrected):
        return None
    return corrected


def _usage(result) -> dict:
    """What the engine reported for this turn: the only honest source for the ledger."""
    return {
        "model_id": getattr(result, "model_id", None),
        "tokens": getattr(result, "tokens", {}) or {},
        "cost": getattr(result, "cost", 0.0) or 0.0,
        "latency_ms": getattr(result, "latency_ms", 0) or 0,
    }


def parse_model(entry: str) -> tuple[str, str]:
    """`provider/model`, or a bare model id with the default provider.

    Provider first keeps the whole setting in one column: `opencode-go/deepseek-v4.1-flash`.
    """
    provider, sep, model = entry.partition("/")
    return (model, provider) if sep else (entry, DEFAULT_PROVIDER)


def _run_turn(conv: dict, prompt: str, entry: str, runtime: OpenCodeRuntime):
    """Run the turn on the profile's model. No fallback: if it fails, the user is told (F8)."""
    model, provider = parse_model(entry)
    session_id = conv.get("engine_session_id")
    try:
        if not session_id:
            session_id = runtime.open_session(model, provider_id=provider,
                                              title=f"conv:{conv['id']}").id
            set_engine_session(conv["id"], session_id)
        return _run_on(session_id, prompt, runtime)
    except SessionLost:
        # The engine forgot the session: replay from our transcript on a fresh one.
        handle = runtime.open_session(model, provider_id=provider, title=f"conv:{conv['id']}:rebuild")
        set_engine_session(conv["id"], handle.id)
        return _run_on(handle.id, prompt, runtime)


def _run_on(session_id: str, prompt: str, runtime: OpenCodeRuntime):
    """Send the prompt, watch tool calls on the SSE feed, then read the finished message.

    Only the guard needs the feed: deltas are ignored because the placeholder shows minutes, not
    text (2026-11-25). Answer and usage come from /message once the turn is over, so a feed that
    dies costs the guard, not the turn.
    """
    guard = ToolGuard()

    async def watch() -> None:
        stream = runtime.stream(session_id, timeout=STREAM_TIMEOUT, on_tool=guard.observe)
        pending = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0.3)  # subscribe before the prompt or the first events are lost
        runtime.prompt(session_id, prompt)
        try:
            while True:
                try:
                    await pending
                except StopAsyncIteration:
                    return
                pending = asyncio.ensure_future(stream.__anext__())
        except RuntimeUnavailable:
            return  # the turn itself is still running; polling below gets the answer

    asyncio.run(watch())
    if guard.reason:
        raise LoopGuardrail(guard.reason)
    return runtime.wait_turn(session_id, timeout=TURN_TIMEOUT)
