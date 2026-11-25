"""Queries used by dispatch. Small on purpose: one function per fact needed."""

import uuid

from .db import connect


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def get_channel_by_external(external_id: str) -> dict | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT c.*, p.name AS profile_name, p.system_prompt, p.default_model
                 FROM channels c JOIN profiles p ON p.id = c.profile_id
                WHERE c.external_id = %s AND c.enabled""",
            (external_id,),
        )
        return cur.fetchone()


def get_or_create_conversation(profile_id: str, channel_id: str, title: str) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM conversations WHERE channel_id = %s AND profile_id = %s ORDER BY created_at LIMIT 1",
            (channel_id, profile_id),
        )
        row = cur.fetchone()
        if row:
            return row
        conv = new_id("cnv")
        cur.execute(
            "INSERT INTO conversations (id, profile_id, channel_id, title) VALUES (%s, %s, %s, %s)",
            (conv, profile_id, channel_id, title),
        )
        conn.commit()
        cur.execute("SELECT * FROM conversations WHERE id = %s", (conv,))
        return cur.fetchone()


def add_message(conversation_id: str, role: str, content: str, turn_id: str | None = None) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO messages (conversation_id, turn_id, role, content) VALUES (%s, %s, %s, %s)",
            (conversation_id, turn_id, role, content),
        )
        cur.execute("UPDATE conversations SET last_turn_at = now() WHERE id = %s", (conversation_id,))
        conn.commit()


def recent_messages(conversation_id: str, limit: int = 12, exclude_turn_id: str | None = None) -> list[dict]:
    """Newest `limit` messages, oldest first. `exclude_turn_id` drops the turn being built."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT role, content FROM messages
                WHERE conversation_id = %s AND (%s::text IS NULL OR turn_id IS DISTINCT FROM %s::text)
                ORDER BY created_at DESC LIMIT %s""",
            (conversation_id, exclude_turn_id, exclude_turn_id, limit),
        )
        rows = cur.fetchall()
    return list(reversed(rows))


def memory_for(profile_id: str, limit: int = 15) -> list[str]:
    """Hechos primero, después preferencias y por último contexto; lo más nuevo gana dentro de cada grupo.

    Antes era solo `created_at DESC` y los imports comparten timestamp: el bloque de 15 quedaba
    decidido por el orden físico, así que un hecho viejo podía quedar fuera mientras entraba
    contexto largo de hace semanas.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT content FROM memory_items
                WHERE profile_id = %s AND deleted_at IS NULL
                ORDER BY CASE kind WHEN 'fact' THEN 0 WHEN 'preference' THEN 1 ELSE 2 END,
                         created_at DESC, id DESC
                LIMIT %s""",
            (profile_id, limit),
        )
        return [row["content"] for row in cur.fetchall()]


def set_engine_session(conversation_id: str, session_id: str | None) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE conversations SET engine_session_id = %s WHERE id = %s", (session_id, conversation_id))
        conn.commit()


def reset_engine_session(conversation_id: str) -> None:
    """Forget the engine session. The next turn opens a new one and replays our transcript.

    The transcript is ours (ADR 0004); the engine session is only a context cache. Dropping it is
    the reset: `/new` and the inactivity rule both land here.
    """
    set_engine_session(conversation_id, None)


def enqueue_turn(conversation_id: str, input_text: str, idempotency_key: str, kind: str = "message",
                 placeholder_message_id: int | None = None, partial: str | None = None) -> dict | None:
    """Insert a queued turn. None means the update was already processed.

    The sequence number is computed inside the INSERT: two writers of the same conversation
    (the channel and a job) used to race on `max(seq)+1` and collide.

    `partial` carries the text a stopped turn had already produced, so `/resume` continues from it
    instead of answering from scratch.
    """
    turn = new_id("trn")
    with connect() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                """INSERT INTO turns (id, conversation_id, seq, state, kind, idempotency_key, input_text,
                                     placeholder_message_id, partial_text)
                   SELECT %s, %s, coalesce(max(seq), 0) + 1, 'queued', %s, %s, %s, %s, %s
                     FROM turns WHERE conversation_id = %s
                   RETURNING seq""",
                (turn, conversation_id, kind, idempotency_key, input_text,
                 placeholder_message_id, partial, conversation_id),
            )
        except Exception as exc:
            conn.rollback()
            if "idempotency_key" in str(exc):
                return None
            raise
        seq = cur.fetchone()["seq"]
        conn.commit()
        return {"id": turn, "seq": seq}


def set_placeholder(turn_id: str, message_id: int) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE turns SET placeholder_message_id = %s WHERE id = %s", (message_id, turn_id))
        conn.commit()


def claim_turn() -> dict | None:
    """Take one queued turn, never two at once in the same conversation.

    Only `queued` is claimable. A `running` turn belongs to a live worker that renews its lease on
    every beat (`touch_turn`); reclaiming a stale one re-ran work that had already started and left
    its "⏳ N min" message frozen in the chat. `reap_turns` closes those instead.

    Serial per conversation is enforced by `turns_one_running` (partial unique index): two workers
    may race for two turns of the same chat, and one of them loses with a UniqueViolation. Losing
    the race is not an error, it is the answer.

    The subselect skips conversations that are already busy: without it, the oldest queued turn of
    a busy chat was always picked, lost the index race and left every other chat waiting behind it
    (2026-11-25, reproduced on nexo_test with two workers and three turns).
    """
    from psycopg.errors import UniqueViolation

    with connect() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                """UPDATE turns SET state = 'running', started_at = now(), locked_until = now() + interval '3 minutes'
                    WHERE id = (
                      SELECT t.id FROM turns t
                       WHERE t.state = 'queued'
                         AND NOT EXISTS (
                           SELECT 1 FROM turns r
                            WHERE r.conversation_id = t.conversation_id
                              AND r.state IN ('running', 'cancel_requested'))
                       ORDER BY t.created_at
                       FOR UPDATE SKIP LOCKED
                       LIMIT 1)
                    RETURNING *"""
            )
            row = cur.fetchone()
            conn.commit()
            return row
        except UniqueViolation:
            conn.rollback()
            return None


def touch_turn(turn_id: str) -> None:
    """Renew the lease: the beat proves the worker is alive, so the reaper leaves the turn alone."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE turns SET locked_until = now() + interval '3 minutes'
                WHERE id = %s AND state = 'running'""",
            (turn_id,),
        )
        conn.commit()


def claim_stale_turns() -> list[dict]:
    """Claim running turns nobody renewed: the worker crashed, restarted or was killed mid-turn.

    The claim is part of the UPDATE: three workers ran this loop every 30 s and, without it, each
    one told the user the same turn was interrupted. `cancel_requested` is included because a
    worker that dies after `/stop` leaves the same frozen clock.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE turns SET state = 'failed', error_class = 'Interrupted',
                      ended_at = now(), locked_until = NULL
                WHERE id IN (
                  SELECT id FROM turns
                   WHERE state IN ('running', 'cancel_requested')
                     AND (locked_until IS NULL OR locked_until < now())
                   ORDER BY created_at
                   FOR UPDATE SKIP LOCKED
                   LIMIT 5)
                RETURNING id, seq, conversation_id, placeholder_message_id"""
        )
        rows = cur.fetchall()
        conn.commit()
    return rows


def get_turn(turn_id: str) -> dict | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM turns WHERE id = %s", (turn_id,))
        return cur.fetchone()


def set_turn_state(turn_id: str, state: str, error_class: str | None = None, partial: str | None = None) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE turns SET state = %s, error_class = COALESCE(%s, error_class),
                      partial_text = COALESCE(%s, partial_text),
                      ended_at = CASE WHEN %s IN ('completed','failed','paused') THEN now() ELSE ended_at END,
                      locked_until = NULL
                WHERE id = %s""",
            (state, error_class, partial, state, turn_id),
        )
        conn.commit()


def request_stop(conversation_id: str) -> dict | None:
    """Cancel the conversation's pending work: the running turn AND anything still queued.

    Only touching `running` meant `/stop` answered "no hay turno en curso" and the queued turn ran
    anyway two seconds later.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE turns SET state = 'cancelled', ended_at = now(), locked_until = NULL
                WHERE conversation_id = %s AND state = 'queued'""",
            (conversation_id,),
        )
        cur.execute(
            """UPDATE turns SET state = 'cancel_requested'
                WHERE id = (
                  SELECT id FROM turns WHERE conversation_id = %s AND state = 'running'
                   ORDER BY created_at DESC LIMIT 1)
                RETURNING *""",
            (conversation_id,),
        )
        row = cur.fetchone()
        conn.commit()
    return row


def last_paused_turn(conversation_id: str) -> dict | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM turns WHERE conversation_id = %s AND state = 'paused'
                ORDER BY created_at DESC LIMIT 1""",
            (conversation_id,),
        )
        return cur.fetchone()


def set_model_override(conversation_id: str, model: str | None) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE conversations SET model_override = %s WHERE id = %s", (model, conversation_id))
        conn.commit()


def get_conversation(conversation_id: str) -> dict | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM conversations WHERE id = %s", (conversation_id,))
        return cur.fetchone()


def conversation_status(conversation_id: str) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT (SELECT count(*) FROM turns WHERE conversation_id = %(c)s) AS turns,
                      (SELECT count(*) FROM turns WHERE conversation_id = %(c)s AND state = 'paused') AS paused,
                      (SELECT coalesce(max(created_at), now()) FROM messages WHERE conversation_id = %(c)s) AS last_msg,
                      model_override, engine_session_id
                 FROM conversations WHERE id = %(c)s""",
            {"c": conversation_id},
        )
        return cur.fetchone() or {}


def record_model_call(turn_id: str, profile_id: str | None, usage: dict) -> None:
    """Append one ledger row. `usage` comes straight from TurnResult: no estimates."""
    tokens = usage.get("tokens") or {}
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO model_calls
                   (turn_id, profile_id, model_id, tokens_input, tokens_output, cost_usd, latency_ms)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                turn_id,
                profile_id,
                usage.get("model_id"),
                int(tokens.get("input") or 0),
                int(tokens.get("output") or 0),
                float(usage.get("cost") or 0),
                int(usage.get("latency_ms") or 0),
            ),
        )
