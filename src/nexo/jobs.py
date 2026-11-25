"""Job scheduler: claim with SKIP LOCKED, run, record, deliver.

kind=script costs no tokens: it runs a command and publishes stdout only when it is not
empty (ADR 0013). kind=prompt is deferred to the worker and only enqueued here.
"""

import os
import subprocess
import time
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .cron import next_after
from .db import connect

TICK = 10
MAX_OUTPUT = 3500
# Configurable porque el techo no es el mismo para todos los jobs: el scout de empleos corre ocho
# fuentes en paralelo y una de ellas (`scout_indeed.py`) tiene su propio timeout de 900 s. Un techo
# externo igual al interno mataba el job justo en el borde, sin salida.
SCRIPT_TIMEOUT = float(os.environ.get("NEXO_SCRIPT_TIMEOUT", "1200"))
# Cron expressions are written in local time (docs/operations.md): evaluating them in UTC
# would fire an "8:00" job at 05:00 in Chile.
LOCAL_TZ = ZoneInfo(os.environ.get("NEXO_TZ", "America/Santiago"))


def next_run_local(expr: str, after: datetime | None = None) -> datetime:
    local_now = (after or datetime.now(LOCAL_TZ)).astimezone(LOCAL_TZ).replace(tzinfo=None)
    return next_after(expr, local_now).replace(tzinfo=LOCAL_TZ)


def list_jobs() -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, kind, schedule, owner, enabled, next_run_at, state FROM jobs ORDER BY name")
        return cur.fetchall()


def add_job(name: str, kind: str, schedule: str | None = None, profile_id: str | None = None,
            channel_id: str | None = None, command: str | None = None, payload: str | None = None,
            owner: str = "nexo", run_at: datetime | None = None) -> str:
    """Create or update a job.

    `run_at` makes it a one-shot: it runs once at that instant and switches itself off. Migrating a
    profile surfaced the case — a reminder for a specific date is not a cron, and writing it as
    `0 10 4 1 *` would fire again every January.
    """
    if run_at is not None:
        schedule = "once"
    elif not schedule:
        raise ValueError("a job needs either a cron schedule or run_at")
    first_run = run_at.astimezone(LOCAL_TZ) if run_at else next_run_local(schedule)
    with connect() as conn, conn.cursor() as cur:
        job_id = f"job_{name.replace('-', '_')[:32]}"
        cur.execute(
            """INSERT INTO jobs (id, kind, name, profile_id, channel_id, schedule, command, payload, owner, next_run_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (name) DO UPDATE
                 SET kind=EXCLUDED.kind, schedule=EXCLUDED.schedule, command=EXCLUDED.command,
                     payload=EXCLUDED.payload, owner=EXCLUDED.owner, channel_id=EXCLUDED.channel_id,
                     profile_id=EXCLUDED.profile_id""",
            (job_id, kind, name, profile_id, channel_id, schedule, command, payload, owner, first_run),
        )
        conn.commit()
    return job_id


def set_enabled(name: str, enabled: bool) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE jobs SET enabled = %s WHERE name = %s", (enabled, name))
        conn.commit()


def claim_due() -> list[dict]:
    """Take up to 5 due jobs. A second scheduler cannot take the same row.

    An expired lease counts as due: a scheduler killed mid-run left the job `running` forever, and
    nothing else ever brought it back (2026-11-25).
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE jobs SET state = 'running', locked_until = now() + interval '15 minutes'
                WHERE id IN (
                  SELECT id FROM jobs
                   WHERE (state = 'queued' OR (state = 'running' AND locked_until < now()))
                     AND enabled AND owner = 'nexo' AND next_run_at <= now()
                   ORDER BY next_run_at
                   FOR UPDATE SKIP LOCKED
                   LIMIT 5)
                RETURNING *"""
        )
        rows = cur.fetchall()
        conn.commit()
    return rows


def run_once(job: dict, scheduled_for: datetime | None = None) -> dict:
    """Run a job now. `scheduled_for` is the slot being consumed.

    The scheduler passes the job's next_run_at; a manual run passes nothing and uses the
    current minute, so it does not eat the next scheduled slot (job_runs is unique by slot).
    """
    if scheduled_for is None:
        scheduled_for = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO job_runs (job_id, scheduled_for) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING id",
            (job["id"], scheduled_for),
        )
        row = cur.fetchone()
        conn.commit()
    if row is None:  # already ran for this schedule: idempotent
        return {"status": "skipped", "output": ""}

    run_id = row["id"]
    if job["kind"] == "prompt":
        # Un job `prompt` también puede traer `command`: primero se corre el script y su salida se
        # inyecta al prompt. Es lo que hacía el sistema viejo con el scout de empleos — ocho fuentes
        # crudas que el agente tiene que deduplicar y filtrar, no un stdout que se manda tal cual.
        if job.get("command"):
            job = {**job, "payload": _merge_command_output(job)}
        result = _enqueue_prompt(job, scheduled_for)
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE job_runs SET ended_at = now(), status = %s, exit_code = NULL, output = %s, error = %s
                    WHERE id = %s""",
                (result["status"], result["output"][:MAX_OUTPUT], result["error"], run_id),
            )
            conn.commit()
        return {**result, "run_id": run_id}

    try:
        completed = subprocess.run(
            job["command"], shell=True, capture_output=True, text=True, timeout=SCRIPT_TIMEOUT
        )
        output = (completed.stdout or "").strip()
        status = "ok" if completed.returncode == 0 else "failed"
        error = (completed.stderr or "").strip()[:500] or None
    except subprocess.TimeoutExpired:
        completed, output, status, error = None, "", "failed", f"timeout after {SCRIPT_TIMEOUT}s"

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE job_runs SET ended_at = now(), status = %s, exit_code = %s, output = %s, error = %s
                WHERE id = %s""",
            (status, getattr(completed, "returncode", None), output[:MAX_OUTPUT], error, run_id),
        )
        conn.commit()
    return {"status": status, "output": output, "error": error, "run_id": run_id}


def finish(job: dict, result: dict) -> None:
    """Schedule the next run and clear the lease.

    A failure retries in 5 minutes (up to `max_attempts`), not at the next cron slot: a daily job
    that failed at 08:00 used to wait a full day, which the docstring called "a short delay".
    """
    attempts = job["attempts"] + (1 if result["status"] != "ok" else 0)
    with connect() as conn, conn.cursor() as cur:
        if result["status"] != "ok" and attempts >= job["max_attempts"]:
            cur.execute(
                "UPDATE jobs SET state='failed', locked_until=NULL, attempts=%s, last_error=%s WHERE id=%s",
                (attempts, (result.get("error") or "failed")[:500], job["id"]),
            )
        elif result["status"] != "ok":
            cur.execute(
                """UPDATE jobs SET state='queued', locked_until=NULL, attempts=%s, last_error=%s,
                          next_run_at=now() + interval '5 minutes' WHERE id=%s""",
                (attempts, result.get("error"), job["id"]),
            )
        elif job.get("schedule") == "once":
            # Un one-shot no tiene próximo: se apaga en vez de quedar re-armado para dentro de un año.
            cur.execute(
                """UPDATE jobs SET state='done', enabled=false, locked_until=NULL, attempts=%s,
                          last_error=NULL WHERE id=%s""",
                (attempts, job["id"]),
            )
        else:
            cur.execute(
                """UPDATE jobs SET state='queued', locked_until=NULL, attempts=%s, last_error=NULL,
                          next_run_at=%s WHERE id=%s""",
                (attempts, next_run_local(job["schedule"]), job["id"]),
            )
        conn.commit()


def _merge_command_output(job: dict, limit: int = 30_000) -> str:
    """Run the job's command and return the prompt with its output appended.

    A failing command is not a failing job: the output (stdout + stderr) goes into the prompt anyway so
    the agent can report *which* source broke instead of the whole report vanishing. The prompt in the
    old system already had a rule for that ("si alguna fuente falla, decilo en el recuento").
    """
    try:
        completed = subprocess.run(
            job["command"], shell=True, capture_output=True, text=True, timeout=SCRIPT_TIMEOUT
        )
        body = (completed.stdout or "").strip()
        extra = (completed.stderr or "").strip()
        if extra:
            body = f"{body}\n\n[stderr]\n{extra}" if body else f"[stderr]\n{extra}"
    except subprocess.TimeoutExpired:
        body = f"[error] el comando no terminó en {SCRIPT_TIMEOUT}s: {job['command']}"
    if len(body) > limit:
        body = body[:limit] + f"\n… [salida truncada: {len(body) - limit} caracteres omitidos] …"
    return f"{job['payload']}\n\n=== SALIDA DEL COMANDO ({job['command']}) ===\n{body}"


def _enqueue_prompt(job: dict, slot: datetime) -> dict:
    """A prompt job runs nothing itself: it puts a turn in the queue and gets out of the way.

    Delivery belongs to the worker, which edits the message as the answer streams. Doing it here
    would send an empty message and then a second one with the real content.
    """
    from .dispatch import idempotency_key
    from .store import enqueue_turn, get_or_create_conversation

    if not job.get("payload"):
        return {"status": "failed", "output": "", "error": "prompt job without payload"}
    if not job.get("channel_id") or not job.get("profile_id"):
        return {"status": "failed", "output": "", "error": "prompt job without channel or profile"}
    chat = chat_id_for(job["channel_id"])
    conv = get_or_create_conversation(
        job["profile_id"], job["channel_id"], title=chat or job["channel_id"]
    )
    turn = enqueue_turn(
        conv["id"], job["payload"], idempotency_key("job", job["name"], str(slot)), kind="message"
    )
    if turn is None:
        return {"status": "ok", "output": "duplicate slot, nothing queued", "error": None}
    return {"status": "ok", "output": f"turn {turn['seq']} queued", "error": None}


def job_metrics() -> list[dict]:
    """Real runtime per job: started_at is when it began, not when it was due."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT j.name, count(*) AS runs,
                      round(avg(extract(epoch from (r.ended_at - r.started_at)))::numeric, 1) AS avg_s,
                      round(max(extract(epoch from (r.ended_at - r.started_at)))::numeric, 1) AS max_s,
                      count(*) FILTER (WHERE r.status <> 'ok') AS failures,
                      max(r.ended_at) AS last_run
                 FROM job_runs r JOIN jobs j ON j.id = r.job_id
                WHERE r.ended_at IS NOT NULL
                GROUP BY j.name ORDER BY j.name"""
        )
        return cur.fetchall()


def deliver(chat_id: str, text: str, channel_id: str | None = None) -> None:
    """Hand the output to the channel that owns the job. Each channel may have its own bot.

    Raises when Telegram refused every chunk: `send` swallows the error to keep the poller alive,
    and swallowing it here would mark a run as delivered when nobody received it.
    """
    from .channels.telegram import TelegramBot, token_for

    channel = get_channel(channel_id) if channel_id else None
    token = token_for(channel or {})
    if not token:
        raise RuntimeError("no telegram token for this channel")
    if TelegramBot(token).send(chat_id, text) is None:
        raise RuntimeError(f"telegram refused the message for chat {chat_id}")


def notify(target: str, text: str) -> str:
    """Send one message to a channel. `target` is what the caller already has:

    - a profile name (`freelance`),
    - a Telegram chat id (`-5476036060`),
    - or the bare `telegram`, meaning the operator's own chat (profile `default`).
    """
    if target == "telegram":
        target = "default"
    with connect() as conn, conn.cursor() as cur:
        if target.lstrip("-").isdigit():
            cur.execute(
                "SELECT id, external_id FROM channels WHERE enabled AND external_id = %s", (target,)
            )
        else:
            cur.execute(
                """SELECT c.id, c.external_id FROM channels c JOIN profiles p ON p.id = c.profile_id
                    WHERE p.name = %s AND c.enabled ORDER BY c.created_at LIMIT 1""",
                (target,),
            )
        row = cur.fetchone()
    if not row:
        raise RuntimeError(f"no enabled channel for target '{target}'")
    deliver(row["external_id"], text, row["id"])
    return row["external_id"]


def get_channel(channel_id: str) -> dict | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM channels WHERE id = %s", (channel_id,))
        return cur.fetchone()


def chat_id_for(channel_id: str) -> str | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT external_id FROM channels WHERE id = %s", (channel_id,))
        row = cur.fetchone()
    return row["external_id"] if row else None


def mark_delivery_failed(run_id: int | None, error: str) -> None:
    """A run whose output nobody received is not a success. Record it as such."""
    if not run_id:
        return
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE job_runs SET status='delivery_failed', error=%s WHERE id=%s",
            (error[:500], run_id),
        )
        conn.commit()


def serve() -> None:
    print("scheduler up", flush=True)
    while True:
        try:
            for job in claim_due():
                result = run_once(job, scheduled_for=job["next_run_at"])
                if result["output"] and job.get("channel_id"):
                    chat = chat_id_for(job["channel_id"])
                    if chat:
                        try:
                            deliver(chat, result["output"], job.get("channel_id"))
                        except Exception as exc:
                            mark_delivery_failed(
                                result.get("run_id"), f"deliver failed: {type(exc).__name__}: {exc}"
                            )
                            print(f"{job['name']}: delivery failed ({type(exc).__name__})", flush=True)
                finish(job, result)
                print(f"{job['name']}: {result['status']} ({len(result['output'])} chars)", flush=True)
        except Exception:
            traceback.print_exc()
        time.sleep(TICK)
