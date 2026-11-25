"""Prompt jobs do not execute anything: they enqueue a turn. These are the guards before the DB."""

from datetime import datetime, timezone

from nexo.jobs import _enqueue_prompt


def test_prompt_job_without_payload_fails_loudly():
    result = _enqueue_prompt({"kind": "prompt", "name": "x", "payload": None}, datetime.now(timezone.utc))
    assert result["status"] == "failed" and "payload" in result["error"]


def test_prompt_job_without_channel_fails_loudly():
    result = _enqueue_prompt(
        {"kind": "prompt", "name": "x", "payload": "hola", "channel_id": None, "profile_id": "p"},
        datetime.now(timezone.utc),
    )
    assert result["status"] == "failed" and "channel" in result["error"]


def test_prompt_job_without_profile_fails_loudly():
    result = _enqueue_prompt(
        {"kind": "prompt", "name": "x", "payload": "hola", "channel_id": "c", "profile_id": None},
        datetime.now(timezone.utc),
    )
    assert result["status"] == "failed"
