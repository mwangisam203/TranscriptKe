"""Planning targets: weekdays after submission, at end of day in Nairobi."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def planning_target(submitted_at, snapshot):
    if not submitted_at or not isinstance(snapshot, dict):
        return None
    items = snapshot.get("items")
    if not isinstance(items, list) or not items:
        return None
    values = [
        item.get("processing_days_max") if isinstance(item, dict) else None
        for item in items
    ]
    if any(type(value) is not int or not 0 <= value <= 365 for value in values):
        return None
    if submitted_at.tzinfo is None:
        submitted_at = submitted_at.replace(tzinfo=timezone.utc)
    zone = ZoneInfo("Africa/Nairobi")
    day = submitted_at.astimezone(zone).date()
    remaining = max(values)
    while remaining:
        day += timedelta(days=1)
        if day.weekday() < 5:
            remaining -= 1
    return datetime.combine(day, time(23, 59, 59), tzinfo=zone).astimezone(timezone.utc)


def as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
