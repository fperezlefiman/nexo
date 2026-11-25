"""One-shot jobs against a real Postgres: created with `run_at`, they must run once and switch off.

Run with ./scripts/test.sh (creates the throwaway database and sets NEXO_TEST_DATABASE_URL).
"""

import datetime as dt
import os

import pytest

TEST_URL = os.environ.get("NEXO_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="set NEXO_TEST_DATABASE_URL to run queue tests")


@pytest.fixture(autouse=True)
def clean_db(monkeypatch):
    monkeypatch.setenv("NEXO_DATABASE_URL", TEST_URL)
    from nexo.db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE jobs, job_runs RESTART IDENTITY CASCADE")
        conn.commit()


def _job(name: str) -> dict:
    from nexo.db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM jobs WHERE name = %s", (name,))
        return cur.fetchone()


def test_run_at_creates_a_one_shot_scheduled_there():
    from nexo.jobs import LOCAL_TZ, add_job

    when = (dt.datetime.now(LOCAL_TZ) + dt.timedelta(days=3)).replace(microsecond=0)
    add_job("recordatorio", "prompt", profile_id=None, payload="hola", run_at=when)
    job = _job("recordatorio")
    assert job["schedule"] == "once"
    assert job["next_run_at"] == when
    assert job["enabled"] is True


def test_one_shot_switches_itself_off_after_running():
    from nexo.jobs import LOCAL_TZ, add_job, finish

    add_job("recordatorio", "prompt", payload="hola", run_at=dt.datetime.now(LOCAL_TZ) + dt.timedelta(hours=1))
    finish(_job("recordatorio"), {"status": "ok", "error": None})
    job = _job("recordatorio")
    assert job["enabled"] is False
    assert job["state"] == "done"


def test_a_failing_one_shot_retries_before_giving_up():
    from nexo.jobs import LOCAL_TZ, add_job, finish

    add_job("recordatorio", "prompt", payload="hola", run_at=dt.datetime.now(LOCAL_TZ) + dt.timedelta(hours=1))
    finish(_job("recordatorio"), {"status": "failed", "error": "boom"})
    job = _job("recordatorio")
    assert job["enabled"] is True          # todavía puede reintentar
    assert job["state"] == "queued"


def test_a_cron_job_still_reschedules_itself():
    from nexo.jobs import LOCAL_TZ, add_job, finish, next_run_local
    import datetime as dt

    add_job("diario", "script", schedule="0 9 * * *", command="true")
    slot = _job("diario")["next_run_at"]
    finish(_job("diario"), {"status": "ok", "error": None})
    job = _job("diario")
    assert job["enabled"] is True
    assert job["state"] == "queued"
    # El cron recalcula desde "ahora", que puede seguir siendo el mismo slot: lo que debe garantizarse
    # es que queda en el futuro. El avance real se verifica con `after` = el slot que acaba de correr.
    assert job["next_run_at"] > dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
    assert next_run_local("0 9 * * *", after=slot.astimezone(LOCAL_TZ)) > slot


def test_a_scheduled_slot_is_strictly_after_the_slot_that_ran():
    from nexo.jobs import LOCAL_TZ, next_run_local
    import datetime as dt

    ran_at = dt.datetime(2026, 12, 1, 9, 0, tzinfo=LOCAL_TZ)
    assert next_run_local("0 9 * * *", after=ran_at) == dt.datetime(2026, 12, 2, 9, 0, tzinfo=LOCAL_TZ)


def test_a_job_needs_a_schedule_or_run_at():
    from nexo.jobs import add_job

    with pytest.raises(ValueError):
        add_job("sin_nada", "script", command="true")


def test_a_job_left_running_by_a_dead_scheduler_is_claimed_again():
    """`Restart=always` plus a reboot mid-run used to leave the job `running` forever: nothing
    reset it and `claim_due` only looked at `queued` (2026-11-25)."""
    from nexo.db import connect
    from nexo.jobs import add_job, claim_due

    add_job("atascado", "script", schedule="0 9 * * *", command="true")
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE jobs SET next_run_at = now() - interval '1 hour'")
        conn.commit()

    assert [job["name"] for job in claim_due()] == ["atascado"]
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE jobs SET locked_until = now() - interval '1 minute'")
        conn.commit()

    assert [job["name"] for job in claim_due()] == ["atascado"]
