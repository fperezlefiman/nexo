"""Queue and state machine against a real Postgres: the paths the offline tests cannot reach.

Run with a throwaway database:
    NEXO_TEST_DATABASE_URL=postgresql://nexo:PW@127.0.0.1:5546/nexo_test pytest tests/test_queue_db.py
"""

import os

import pytest

TEST_URL = os.environ.get("NEXO_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="set NEXO_TEST_DATABASE_URL to run queue tests")

CONV = "cnv_test"


@pytest.fixture(autouse=True)
def clean_db(monkeypatch):
    monkeypatch.setenv("NEXO_DATABASE_URL", TEST_URL)
    from nexo.db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """TRUNCATE turns, messages, conversations, channels, profiles, jobs, job_runs,
                        memory_items RESTART IDENTITY CASCADE"""
        )
        cur.execute(
            "INSERT INTO profiles (id, name, system_prompt, default_model) VALUES ('prf_t','t','SOUL','m')"
        )
        cur.execute(
            """INSERT INTO channels (id, kind, external_id, profile_id)
               VALUES ('chn_t','telegram','-1','prf_t')"""
        )
        cur.execute(
            """INSERT INTO conversations (id, profile_id, channel_id, title)
               VALUES (%s,'prf_t','chn_t','-1')""",
            (CONV,),
        )
        conn.commit()


def test_enqueue_then_claim_then_complete():
    from nexo.store import claim_turn, enqueue_turn, get_turn, set_turn_state

    turn = enqueue_turn(CONV, "hola", "k1")
    assert turn["seq"] == 1

    claimed = claim_turn()
    assert claimed["id"] == turn["id"] and claimed["state"] == "running"
    assert claim_turn() is None  # nothing else is queued

    set_turn_state(turn["id"], "completed", partial="listo")
    row = get_turn(turn["id"])
    assert row["state"] == "completed" and row["partial_text"] == "listo"
    assert row["locked_until"] is None and row["ended_at"] is not None


def test_resume_turn_keeps_the_partial_it_continues_from():
    from nexo.store import enqueue_turn, get_turn

    turn = enqueue_turn(CONV, "Continuá desde donde quedaste.", "k-resume", kind="resume",
                        partial="respuesta a medias")
    assert get_turn(turn["id"])["partial_text"] == "respuesta a medias"


def test_sequence_numbers_do_not_repeat():
    from nexo.store import enqueue_turn

    assert [enqueue_turn(CONV, f"m{i}", f"k{i}")["seq"] for i in range(1, 4)] == [1, 2, 3]


def test_duplicate_update_is_ignored():
    from nexo.store import enqueue_turn

    assert enqueue_turn(CONV, "hola", "same") is not None
    assert enqueue_turn(CONV, "hola", "same") is None


def test_stop_cancels_a_queued_turn():
    from nexo.store import claim_turn, enqueue_turn, get_turn, request_stop

    turn = enqueue_turn(CONV, "mensaje largo", "k-stop")
    assert request_stop(CONV) is None  # nothing was running yet
    assert get_turn(turn["id"])["state"] == "cancelled"
    assert claim_turn() is None  # a cancelled turn is never executed


def test_stop_marks_the_running_turn():
    from nexo.store import claim_turn, enqueue_turn, get_turn, request_stop

    turn = enqueue_turn(CONV, "corriendo", "k-run")
    claim_turn()
    assert request_stop(CONV)["id"] == turn["id"]
    assert get_turn(turn["id"])["state"] == "cancel_requested"


def test_history_excludes_the_current_turn():
    from nexo.store import add_message, enqueue_turn, recent_messages

    turn = enqueue_turn(CONV, "nuevo", "k-hist")
    add_message(CONV, "user", "viejo")
    add_message(CONV, "assistant", "respuesta vieja")
    add_message(CONV, "user", "nuevo", turn["id"])

    history = recent_messages(CONV, 12, exclude_turn_id=turn["id"])
    assert [m["content"] for m in history] == ["viejo", "respuesta vieja"]


def test_two_turns_of_the_same_chat_never_run_at_once():
    """ADR 0027: ordering per conversation is a schema rule, not a promise about workers."""
    from nexo.store import claim_turn, enqueue_turn

    first = enqueue_turn(CONV, "uno", "k-one")
    enqueue_turn(CONV, "dos", "k-two")
    claimed = claim_turn()
    assert claimed["id"] == first["id"]
    assert claim_turn() is None  # the second turn of the chat waits


def test_different_chats_can_be_claimed_in_parallel():
    from nexo.db import connect
    from nexo.store import claim_turn, enqueue_turn

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO conversations (id, profile_id, channel_id, title)
               VALUES ('cnv_other','prf_t','chn_t','-2')"""
        )
    a = enqueue_turn(CONV, "uno", "k-a")
    b = enqueue_turn("cnv_other", "dos", "k-b")
    ids = {claim_turn()["id"], claim_turn()["id"]}
    assert ids == {a["id"], b["id"]}


def test_a_queued_turn_does_not_block_another_chat():
    """Before 2026-11-25 the claim always took the oldest queued turn, lost the index race and
    returned None: a busy chat froze every other chat until its running turn ended."""
    from nexo.db import connect
    from nexo.store import claim_turn, enqueue_turn, set_turn_state

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO conversations (id, profile_id, channel_id, title)
               VALUES ('cnv_b','prf_t','chn_t','-3')"""
        )
    a1 = enqueue_turn(CONV, "uno", "kb-a1")
    a2 = enqueue_turn(CONV, "dos", "kb-a2")
    b1 = enqueue_turn("cnv_b", "tres", "kb-b1")

    assert claim_turn()["id"] == a1["id"]
    assert claim_turn()["id"] == b1["id"]  # A2 is older, but its chat is busy
    assert claim_turn() is None            # A2 still waits for A1

    set_turn_state(a1["id"], "completed")
    assert claim_turn()["id"] == a2["id"]


def test_a_stale_turn_is_reaped_exactly_once():
    from nexo.db import connect
    from nexo.store import claim_stale_turns, claim_turn, enqueue_turn

    turn = enqueue_turn(CONV, "hola", "k-reap")
    claim_turn()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE turns SET locked_until = now() - interval '1 minute' WHERE id = %s",
                    (turn["id"],))
        conn.commit()

    first = claim_stale_turns()
    assert [row["id"] for row in first] == [turn["id"]]
    assert claim_stale_turns() == []  # the other workers get nothing: it was claimed by the UPDATE


def test_a_cancelled_turn_with_a_dead_worker_is_reaped():
    """`/stop` releases the lease but not the clock: a worker that dies then leaves the ⏳ frozen."""
    from nexo.db import connect
    from nexo.store import claim_stale_turns, claim_turn, enqueue_turn, request_stop

    turn = enqueue_turn(CONV, "hola", "k-cancel")
    claim_turn()
    request_stop(CONV)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE turns SET locked_until = now() - interval '1 minute' WHERE id = %s",
                    (turn["id"],))
        conn.commit()

    assert [row["id"] for row in claim_stale_turns()] == [turn["id"]]


def test_notify_sends_to_the_profile_channel(monkeypatch):
    """Scripts outside the scheduler (the Workana scout) deliver through the profile's channel."""
    from nexo import jobs

    sent = []
    monkeypatch.setattr(jobs, "deliver", lambda chat, text, channel=None: sent.append((chat, text, channel)))
    assert jobs.notify("t", "hola") == "-1"
    assert sent == [("-1", "hola", "chn_t")]


def test_notify_fails_loudly_for_a_profile_without_channel():
    from nexo import jobs

    with pytest.raises(RuntimeError, match="no enabled channel"):
        jobs.notify("no-existe", "hola")


def test_notify_accepts_a_chat_id(monkeypatch):
    """mediax already knows the chat id; it should not have to know profile names."""
    from nexo import jobs

    sent = []
    monkeypatch.setattr(jobs, "deliver", lambda chat, text, channel=None: sent.append((chat, text, channel)))
    assert jobs.notify("-1", "hola") == "-1"
    assert sent == [("-1", "hola", "chn_t")]


def test_notify_maps_the_bare_word_telegram_to_the_operator_chat(monkeypatch):
    from nexo import jobs
    from nexo.db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO profiles (id, name, system_prompt, default_model)
                       VALUES ('prf_d','default','SOUL','m')""")
        cur.execute("""INSERT INTO channels (id, kind, external_id, profile_id)
                       VALUES ('chn_d','telegram','8965894123','prf_d')""")
        conn.commit()
    sent = []
    monkeypatch.setattr(jobs, "deliver", lambda chat, text, channel=None: sent.append((chat, channel)))
    assert jobs.notify("telegram", "hola") == "8965894123"
    assert sent == [("8965894123", "chn_d")]
