"""Cron parsing and job execution (ADR 0010, 0013).

Minute resolution, 5 fields. `next_after` walks minutes instead of doing date arithmetic:
slower on paper, obviously correct on ranges, lists and steps.
"""

from datetime import datetime, timedelta

FIELDS = ("minute", "hour", "day", "month", "weekday")


def _expand(field: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, raw_step = part.split("/", 1)
            step = int(raw_step)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            raw_start, raw_end = part.split("-", 1)
            start, end = int(raw_start), int(raw_end)
        else:
            start = end = int(part)
        if not (low <= start <= end <= high):
            raise ValueError(f"cron value out of range: {field}")
        values.update(range(start, end + 1, step))
    return values


def parse(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron needs 5 fields, got {len(parts)}: {expr!r}")
    minute, hour, day, month, weekday = parts
    weekdays = _expand(weekday, 0, 7)
    if 7 in weekdays:  # cron accepts 7 as Sunday
        weekdays = (weekdays - {7}) | {0}
    return (
        _expand(minute, 0, 59),
        _expand(hour, 0, 23),
        _expand(day, 1, 31),
        _expand(month, 1, 12),
        weekdays,
    )


def next_after(expr: str, after: datetime) -> datetime:
    minute, hour, day, month, weekdays = parse(expr)
    candidate = (after + timedelta(minutes=1)).replace(second=0, microsecond=0)
    for _ in range(60 * 24 * 366):
        # cron: 0 = Sunday. datetime.weekday(): 0 = Monday -> shift the candidate only.
        cron_weekday = (candidate.weekday() + 1) % 7
        if (
            candidate.minute in minute
            and candidate.hour in hour
            and candidate.day in day
            and candidate.month in month
            and cron_weekday in weekdays
        ):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError(f"no match within a year for cron {expr!r}")
