"""
Shift calendar for the APS MVP (Option B three-shift pattern).

- 早班 07:00-15:00, 中班 15:00-23:00, 晚班 23:00-07:00(+1)
The calendar is a flat, chronologically ordered slot list; the scheduler decides
how many production lots land in each slot, so a slot is the atomic time unit.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List

SHIFT_HOURS = 8.0
SHIFT_SECONDS = int(SHIFT_HOURS * 3600)
MIN_SHIFT_UTILIZATION = 0.95
MIN_SHIFT_SECONDS = int(-(-SHIFT_SECONDS * MIN_SHIFT_UTILIZATION // 1))  # ceil
SHIFT_NAMES = ("早班", "中班", "晚班")


@dataclass
class ShiftBucket:
    index: int
    date_str: str  # e.g. "10/12"
    shift_name: str  # "早班", "中班", "晚班"
    start_dt: datetime
    end_dt: datetime


def generate_shift_calendar(start_date: datetime, num_days: int) -> List[ShiftBucket]:
    """Build `num_days` calendar days of three shifts each, starting at `start_date`."""
    shifts: List[ShiftBucket] = []
    for day_idx in range(num_days):
        day = start_date + timedelta(days=day_idx)
        date_str = f"{day.month}/{day.day}"
        boundaries = [
            datetime(day.year, day.month, day.day, 7, 0),
            datetime(day.year, day.month, day.day, 15, 0),
            datetime(day.year, day.month, day.day, 23, 0),
        ]
        for i, shift_name in enumerate(SHIFT_NAMES):
            shifts.append(ShiftBucket(
                index=len(shifts),
                date_str=date_str,
                shift_name=shift_name,
                start_dt=boundaries[i],
                end_dt=boundaries[i] + timedelta(hours=SHIFT_HOURS),
            ))
    return shifts


def last_shift_on_or_before(shifts: List[ShiftBucket], moment: datetime) -> int:
    """Index of the latest shift that fully completes by `moment` (0 if none)."""
    completed = [s.index for s in shifts if s.end_dt <= moment]
    return completed[-1] if completed else 0
