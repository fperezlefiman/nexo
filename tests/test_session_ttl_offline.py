"""Context budget: a session idle for more than the TTL starts fresh.

The reset primitive is one: `reset_engine_session` sets `engine_session_id` to NULL, and the next
turn opens a new session and replays the transcript. `/new` calls it on demand; this rule calls it
after 2 hours of silence, because a 15-char message was measured at 74 k input tokens (2026-11-25).
"""

from datetime import datetime, timedelta, timezone

from nexo.dispatch import SESSION_TTL, session_expired

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def test_no_messages_means_nothing_to_expire():
    assert session_expired(None, NOW) is False


def test_recent_activity_keeps_the_session():
    assert session_expired(NOW - timedelta(minutes=30), NOW) is False


def test_two_hours_of_silence_expires_it():
    assert session_expired(NOW - timedelta(hours=2, minutes=1), NOW) is True


def test_the_rule_is_configurable_but_defaults_to_two_hours():
    assert SESSION_TTL == timedelta(hours=2)
