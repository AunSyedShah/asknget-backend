"""Reporting periods for KPI cards ("12.8% vs. previous period")."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

PERIODS = ("today", "week", "month", "quarter", "year")


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime          # exclusive
    prev_start: datetime
    prev_end: datetime     # exclusive


def period_range(name: str = "month", now: datetime | None = None) -> Period:
    """Calendar periods (this month vs last month …), in the active timezone."""
    now = now or timezone.localtime()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if name == "today":
        start, end = day, day + timedelta(days=1)
        prev_start = start - timedelta(days=1)
    elif name == "week":
        start = day - timedelta(days=day.weekday())
        end = start + timedelta(days=7)
        prev_start = start - timedelta(days=7)
    elif name == "quarter":
        q_month = 3 * ((day.month - 1) // 3) + 1
        start = day.replace(month=q_month, day=1)
        end = _add_months(start, 3)
        prev_start = _add_months(start, -3)
    elif name == "year":
        start = day.replace(month=1, day=1)
        end = start.replace(year=start.year + 1)
        prev_start = start.replace(year=start.year - 1)
    else:  # month
        start = day.replace(day=1)
        end = _add_months(start, 1)
        prev_start = _add_months(start, -1)
    return Period(start, end, prev_start, start)


def _add_months(dt: datetime, n: int) -> datetime:
    month = dt.month - 1 + n
    year = dt.year + month // 12
    month = month % 12 + 1
    return dt.replace(year=year, month=month, day=min(dt.day, calendar.monthrange(year, month)[1]))


def change_pct(current: float, previous: float) -> float | None:
    """None when there is no previous value to compare with (the UI hides the badge)."""
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


def month_buckets(year: int) -> list[datetime]:
    tz = timezone.get_current_timezone()
    return [datetime(year, m, 1, tzinfo=tz) for m in range(1, 13)]
