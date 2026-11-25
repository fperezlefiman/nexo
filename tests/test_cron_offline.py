"""Cron parsing tests: the scheduler's only piece of logic worth guarding."""

from datetime import datetime

import pytest

from nexo.cron import next_after, parse


def test_hourly_matches_next_hour():
    nxt = next_after("0 * * * *", datetime(2026, 11, 24, 21, 29))
    assert nxt == datetime(2026, 11, 24, 22, 0)


def test_every_30_minutes():
    assert next_after("30 * * * *", datetime(2026, 11, 24, 21, 31)) == datetime(2026, 11, 24, 22, 30)
    assert next_after("*/30 * * * *", datetime(2026, 11, 24, 21, 31)) == datetime(2026, 11, 24, 22, 0)


def test_daily_at_8_crosses_midnight():
    assert next_after("0 8 * * *", datetime(2026, 11, 24, 9, 0)) == datetime(2026, 11, 25, 8, 0)


def test_weekday_monday_only():
    # 2026-11-24 is a Tuesday; next Monday 09:00 is 2026-11-30.
    assert next_after("0 9 * * 1", datetime(2026, 11, 24, 10, 0)) == datetime(2026, 11, 30, 9, 0)


def test_lists_and_ranges():
    assert parse("0,15,30 9-11 * * *")[0] == {0, 15, 30}
    assert parse("0,15,30 9-11 * * *")[1] == {9, 10, 11}


def test_bad_expressions_are_rejected():
    with pytest.raises(ValueError):
        parse("* * * *")
    with pytest.raises(ValueError):
        parse("70 * * * *")
