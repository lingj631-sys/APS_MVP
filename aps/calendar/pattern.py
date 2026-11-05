from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from enum import Enum

__all__ = [
    "DEFAULT_AUTO_THRESHOLDS",
    "REST_WEEKDAYS",
    "WORK_DAYS",
    "CalendarMode",
    "is_working_day",
    "recommend_mode",
    "working_days",
]


class CalendarMode(str, Enum):
    CONTINUOUS = "CONTINUOUS"
    SIX_ON_ONE_OFF = "6ON1OFF"
    FIVE_ON_TWO_OFF = "5ON2OFF"
    FOUR_ON_THREE_OFF = "4ON3OFF"


WORK_DAYS: dict[CalendarMode, int] = {
    CalendarMode.CONTINUOUS: 7,
    CalendarMode.SIX_ON_ONE_OFF: 6,
    CalendarMode.FIVE_ON_TWO_OFF: 5,
    CalendarMode.FOUR_ON_THREE_OFF: 4,
}

REST_WEEKDAYS: dict[CalendarMode, frozenset[int]] = {
    CalendarMode.CONTINUOUS: frozenset(),
    CalendarMode.SIX_ON_ONE_OFF: frozenset({6}),
    CalendarMode.FIVE_ON_TWO_OFF: frozenset({5, 6}),
    CalendarMode.FOUR_ON_THREE_OFF: frozenset({4, 5, 6}),
}

DEFAULT_AUTO_THRESHOLDS: dict[str, float] = {
    "4ON3OFF": 0.5714,
    "5ON2OFF": 0.7143,
    "6ON1OFF": 0.8571,
}

_ORDER = (
    ("4ON3OFF", CalendarMode.FOUR_ON_THREE_OFF),
    ("5ON2OFF", CalendarMode.FIVE_ON_TWO_OFF),
    ("6ON1OFF", CalendarMode.SIX_ON_ONE_OFF),
)


def recommend_mode(load_ratio: float,
                   thresholds: Mapping[str, float] = DEFAULT_AUTO_THRESHOLDS) -> CalendarMode:
    for key, mode in _ORDER:
        if load_ratio <= float(thresholds[key]):
            return mode
    return CalendarMode.CONTINUOUS


def is_working_day(mode: CalendarMode, day: dt.date) -> bool:
    return day.weekday() not in REST_WEEKDAYS[mode]


def working_days(mode: CalendarMode, start: dt.date,
                 horizon_days: int) -> tuple[dt.date, ...]:
    return tuple(
        start + dt.timedelta(days=offset)
        for offset in range(max(0, horizon_days))
        if is_working_day(mode, start + dt.timedelta(days=offset))
    )
