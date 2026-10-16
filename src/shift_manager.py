"""
Shift calendar manager and dynamic calendar scheduler for the APS MVP.

Implements R-AR-01 (Line-Calendar decoupling), R-CL-01..03 (Three-tier intelligent
calendar selection: 4/3, 5/2, 7/0), and R-BK-01 (holiday backwards due date shift).

Option B three-shift pattern:
- 早班 07:00-15:00, 中班 15:00-23:00, 晚班 23:00-07:00(+1)
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Set

SHIFT_HOURS = 8.0
SHIFT_SECONDS = int(SHIFT_HOURS * 3600)
MIN_SHIFT_UTILIZATION = 0.95
MIN_SHIFT_SECONDS = int(-(-SHIFT_SECONDS * MIN_SHIFT_UTILIZATION // 1))  # ceil
SHIFT_NAMES = ("早班", "中班", "晚班")


class CalendarMode(str, Enum):
    AUTO = "AUTO"              # Auto-select based on demand load ratio
    FOUR_THREE = "FOUR_THREE"  # 做四休三: Mon-Thu worked, Fri-Sun off
    FIVE_TWO = "FIVE_TWO"      # 做五休二: Mon-Fri worked, Sat-Sun off
    SEVEN_ZERO = "SEVEN_ZERO"  # 连续生产: Mon-Sun worked, no off days


# Monday is 0, Sunday is 6
WORKING_DAYS_BY_MODE: Dict[CalendarMode, Set[int]] = {
    CalendarMode.FOUR_THREE: {0, 1, 2, 3},       # Mon, Tue, Wed, Thu
    CalendarMode.FIVE_TWO: {0, 1, 2, 3, 4},       # Mon, Tue, Wed, Thu, Fri
    CalendarMode.SEVEN_ZERO: {0, 1, 2, 3, 4, 5, 6}, # All 7 days
}


@dataclass
class ShiftBucket:
    index: int
    date_str: str          # e.g. "10/12"
    shift_name: str        # "早班", "中班", "晚班"
    start_dt: datetime
    end_dt: datetime
    weekday: int           # 0..6 (Mon..Sun)
    is_work_shift: bool = True


@dataclass
class LineCalendar:
    line: str
    mode: CalendarMode
    working_weekdays: Set[int] = field(default_factory=set)

    def __post_init__(self):
        if not self.working_weekdays and self.mode in WORKING_DAYS_BY_MODE:
            self.working_weekdays = WORKING_DAYS_BY_MODE[self.mode].copy()

    def is_workday(self, dt: datetime) -> bool:
        return dt.weekday() in self.working_weekdays


def decide_calendar_mode(demand_hours: float, full_weekly_capacity_hours: float) -> CalendarMode:
    """R-CL-01..03: Select calendar mode by load ratio against full 7-day capacity."""
    if full_weekly_capacity_hours <= 0:
        return CalendarMode.SEVEN_ZERO
    ratio = demand_hours / full_weekly_capacity_hours
    if ratio <= 4.0 / 7.0:
        return CalendarMode.FOUR_THREE
    elif ratio <= 5.0 / 7.0:
        return CalendarMode.FIVE_TWO
    else:
        return CalendarMode.SEVEN_ZERO


def generate_shift_calendar(
    start_date: datetime,
    num_days: int,
    calendar_mode: CalendarMode = CalendarMode.SEVEN_ZERO,
    custom_working_days: Optional[Set[int]] = None,
    filter_non_working: bool = True,
) -> List[ShiftBucket]:
    """Build calendar shifts according to the specified calendar pattern.

    If `filter_non_working` is True, shifts on off-days are excluded from the
    solver slots, turning off-day avoidance into a structural guarantee.
    """
    working_days = custom_working_days or WORKING_DAYS_BY_MODE.get(
        calendar_mode, WORKING_DAYS_BY_MODE[CalendarMode.SEVEN_ZERO]
    )

    shifts: List[ShiftBucket] = []
    slot_idx = 0
    for day_idx in range(num_days):
        day = start_date + timedelta(days=day_idx)
        date_str = f"{day.month}/{day.day}"
        is_working = day.weekday() in working_days

        if not is_working and filter_non_working:
            continue

        boundaries = [
            datetime(day.year, day.month, day.day, 7, 0),
            datetime(day.year, day.month, day.day, 15, 0),
            datetime(day.year, day.month, day.day, 23, 0),
        ]
        for i, shift_name in enumerate(SHIFT_NAMES):
            shifts.append(
                ShiftBucket(
                    index=slot_idx,
                    date_str=date_str,
                    shift_name=shift_name,
                    start_dt=boundaries[i],
                    end_dt=boundaries[i] + timedelta(hours=SHIFT_HOURS),
                    weekday=day.weekday(),
                    is_work_shift=is_working,
                )
            )
            slot_idx += 1
    return shifts


def adjust_due_datetime_for_holidays(
    due_dt: datetime,
    working_weekdays: Set[int],
) -> datetime:
    """R-BK-01: Shift holiday due date back to the end of the last working shift (15:00)."""
    curr = due_dt
    while curr.weekday() not in working_weekdays:
        curr -= timedelta(days=1)
    # Target morning shift finish time on the working day
    return datetime(curr.year, curr.month, curr.day, 15, 0, 0)


def last_shift_on_or_before(shifts: List[ShiftBucket], moment: datetime) -> int:
    """Index of the latest work shift that fully completes by `moment` (0 if none)."""
    completed = [s.index for s in shifts if s.end_dt <= moment]
    return completed[-1] if completed else 0
