"""Turn worker: claims queued turns and runs them, outside the channel process (F4).

One turn at a time per conversation is guaranteed by two things: the lease (a second worker cannot
claim the same row: SKIP LOCKED) and `turns_one_running`, a partial unique index that stops two
workers from taking two turns of the same chat. Several workers may run at once (ADR 0027).
"""

import os
import threading
import time
import traceback

from .adapters.opencode import OpenCodeRuntime
from .channels.telegram import TelegramBot, token_for
from .db import connect
from .dispatch import execute_turn
from .store import claim_stale_turns, claim_turn, get_turn, record_model_call, set_turn_state
from .store import touch_turn

POLL = 1.0


def _channel_for(conversation_id: str) -> dict | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT c.*, p.system_prompt, p.default_model, p.name AS profile_name
                 FROM conversations v
                 JOIN channels c ON c.id = v.channel_id
                 JOIN profiles p ON p.id = v.profile_id
                WHERE v.id = %s""",
            (conversation_id,),
        )
        return cur.fetchone()


def status_line(elapsed: float) -> str:
    """Ephemeral progress: no notification, deleted when the turn ends.

    Minutes only, on purpose: the beat edits once a minute, so seconds would sit stale.
    """
    return f"⏳ {int(elapsed) // 60} min"


def _beat(bot: TelegramBot, chat_id: str, message_id: int | None, started: float,
          stop: threading.Event, turn_id: str) -> None:
    """Tick the clock even when nothing streams, and renew the lease on the way.

    `NEXO_STATUS_EVERY` seconds per edit (60 by default): the message shows minutes, so a faster
    tick would rewrite the same text. The lease is renewed unconditionally: a turn without a status
    message still has to prove it is alive, or the reaper would close it.
    """
    every = float(os.environ.get("NEXO_STATUS_EVERY", "60"))
    while not stop.wait(every):
        touch_turn(turn_id)
        if message_id:
            bot.edit(chat_id, message_id, status_line(time.time() - started))


def _reap() -> None:
    """Close turns whose worker disappeared.

    Their "⏳ N min" message stays frozen forever otherwise, and after a crash it is the only thing
    the user sees: no error, no answer, just a clock that stopped.
    """
    for turn in claim_stale_turns():
        channel = _channel_for(turn["conversation_id"])
        if not channel or not turn["placeholder_message_id"]:
            continue
        token = token_for(channel)
        if not token:
            continue
        bot = TelegramBot(token)
        if not bot.delete(channel["external_id"], turn["placeholder_message_id"]):
            bot.edit(channel["external_id"], turn["placeholder_message_id"],
                     "⚠️ turno interrumpido — vuelve a enviar el mensaje")


def serve() -> None:
    runtime = OpenCodeRuntime()
    _reap()
    print("worker up", flush=True)
    last_reap = time.time()
    while True:
        try:
            if time.time() - last_reap > 30:
                _reap()
                last_reap = time.time()
            turn = claim_turn()
            if not turn:
                time.sleep(POLL)
                continue
            _run(turn, runtime)
        except Exception:
            traceback.print_exc()
            time.sleep(2)


def _run(turn: dict, runtime: OpenCodeRuntime) -> None:
    channel = _channel_for(turn["conversation_id"])
    if not channel:
        set_turn_state(turn["id"], "failed", "NoChannel")
        return
    token = token_for(channel)
    chat_id = channel["external_id"]
    if not token:  # a turn with nowhere to answer is worse than a loud failure
        set_turn_state(turn["id"], "failed", "NoToken")
        print(f"turn {turn['seq']}: no telegram token for channel {channel['id']}", flush=True)
        return
    bot = TelegramBot(token)
    started = time.time()
    stop_beat = threading.Event()
    threading.Thread(target=_beat, args=(bot, chat_id, turn["placeholder_message_id"], started,
                                         stop_beat, turn["id"]), daemon=True).start()
    try:
        result = execute_turn(turn, channel, runtime)
    finally:
        stop_beat.set()
    elapsed = time.time() - started

    turn = get_turn(turn["id"]) or turn  # the placeholder id may have landed after the claim
    set_turn_state(turn["id"], result["state"], result.get("error"), partial=result["text"] or None)
    if result.get("usage"):
        try:
            record_model_call(turn["id"], channel["profile_id"], result["usage"])
        except Exception:
            # A ledger write must never take the turn down: the reply already happened.
            traceback.print_exc()
    _show(bot, chat_id, turn["placeholder_message_id"], result)
    print(f"turn {turn['seq']} {result['state']} {elapsed:.1f}s", flush=True)


def _show(bot: TelegramBot, chat_id: str, message_id: int | None, result: dict) -> None:
    text = (result.get("text") or "").strip()
    notice = (result.get("notice") or "").strip()
    if notice:
        # The status line is deleted with the turn, so the quota notice has to survive in the reply.
        text = f"{text}\n\n{notice}".strip()
    if result["state"] == "paused":
        text = (text + "\n\n⏸ pausado — `/resume` para continuar").strip()
    if not text:
        text = "⚠️ el turno terminó sin texto"
    # The status message is a scratchpad: it goes away, the answer arrives as a new message so it
    # notifies and stays in the chat. Same shape as the old system, without the extra noise.
    if message_id and not bot.delete(chat_id, message_id):
        # A leftover ⏳ is worse than a stale tick: overwrite it so it stops looking "in progress".
        bot.edit(chat_id, message_id, "✅")
    bot.send(chat_id, text)
