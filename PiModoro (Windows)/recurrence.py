from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Mapping


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _weekday_set(value: str | Iterable[int] | None) -> set[int]:
    if value is None:
        return set()
    if isinstance(value, str):
        result: set[int] = set()
        for item in value.split(","):
            item = item.strip()
            if item.isdigit():
                number = int(item)
                if 0 <= number <= 6:
                    result.add(number)
        return result
    return {int(item) for item in value if 0 <= int(item) <= 6}


def _month_distance(start: date, target: date) -> int:
    return (target.year - start.year) * 12 + target.month - start.month


def is_exception(target: date, exceptions: Iterable[Mapping[str, object]]) -> bool:
    for item in exceptions:
        start = parse_iso_date(str(item.get("start_date") or ""))
        end = parse_iso_date(str(item.get("end_date") or "")) or start
        if start and end and start <= target <= end:
            return True
    return False


def matches_recurrence(
    task: Mapping[str, object],
    target: date,
    exceptions: Iterable[Mapping[str, object]] = (),
) -> bool:
    if not bool(task.get("recurrence_enabled")):
        return False

    start = parse_iso_date(str(task.get("recurrence_start") or ""))
    if start is None or target < start:
        return False

    end = parse_iso_date(str(task.get("recurrence_end") or ""))
    if end and target > end:
        return False

    if is_exception(target, exceptions):
        return False

    interval = max(1, int(task.get("recurrence_interval") or 1))
    kind = str(task.get("recurrence_kind") or "days")

    if kind == "days":
        matches = (target - start).days % interval == 0
    elif kind == "weeks":
        weekdays = _weekday_set(task.get("recurrence_weekdays")) or {start.weekday()}
        week_index = (target - start).days // 7
        matches = target.weekday() in weekdays and week_index % interval == 0
    elif kind == "months":
        month_index = _month_distance(start, target)
        matches = (
            month_index >= 0
            and month_index % interval == 0
            and target.day == min(start.day, _days_in_month(target.year, target.month))
        )
    else:
        matches = False

    if not matches:
        return False

    maximum = task.get("recurrence_max")
    if maximum not in (None, ""):
        try:
            max_count = int(maximum)
        except (TypeError, ValueError):
            max_count = 0
        if max_count > 0:
            count = 0
            cursor = start
            while cursor <= target:
                if _matches_without_max(task, cursor, exceptions):
                    count += 1
                    if cursor == target:
                        return count <= max_count
                cursor += timedelta(days=1)
            return False

    return True


def _matches_without_max(
    task: Mapping[str, object],
    target: date,
    exceptions: Iterable[Mapping[str, object]],
) -> bool:
    copy = dict(task)
    copy["recurrence_max"] = None
    return matches_recurrence(copy, target, exceptions)


def occurrence_dates(
    task: Mapping[str, object],
    range_start: date,
    range_end: date,
    exceptions: Iterable[Mapping[str, object]] = (),
) -> list[date]:
    if range_end < range_start:
        return []
    dates: list[date] = []
    cursor = range_start
    while cursor <= range_end:
        if matches_recurrence(task, cursor, exceptions):
            dates.append(cursor)
        cursor += timedelta(days=1)
    return dates


def next_occurrence(
    task: Mapping[str, object],
    after: date,
    exceptions: Iterable[Mapping[str, object]] = (),
    search_days: int = 3660,
) -> date | None:
    cursor = after
    for _ in range(search_days + 1):
        if matches_recurrence(task, cursor, exceptions):
            return cursor
        cursor += timedelta(days=1)
    return None


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        following = date(year + 1, 1, 1)
    else:
        following = date(year, month + 1, 1)
    return (following - date(year, month, 1)).days
