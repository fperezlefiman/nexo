"""Progress scratchpad: an ephemeral line, not a message that notifies or stays."""

from nexo.worker import status_line


def test_starts_at_zero():
    assert status_line(0) == "⏳ 0 min"


def test_counts_whole_minutes_only():
    assert status_line(125) == "⏳ 2 min"


def test_no_seconds_and_no_activity():
    line = status_line(61)
    assert line == "⏳ 1 min" and ":" not in line


def test_beat_interval_is_configurable():
    import os
    assert float(os.environ.get("NEXO_STATUS_EVERY", "60")) == 60.0
