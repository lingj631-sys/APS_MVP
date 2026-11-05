from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from aps.calendar.pattern import CalendarMode, is_working_day
from aps.data.issues import IssueLog

__all__ = [
    "DAY_START_HOUR",
    "MS_PER_HOUR",
    "LineGeometry",
    "Slot",
    "SlotKey",
    "build_slots",
]

DAY_START_HOUR = 7
MS_PER_HOUR = 3_600_000


@dataclass(frozen=True)
class SlotKey:
    line_code: str
    work_date: dt.date
    shift_index: int


@dataclass(frozen=True)
class LineGeometry:
    line_code: str
    shift_hours: float
    shifts_per_day: int
    trp_efficiency: float


@dataclass(frozen=True)
class Slot:
    key: SlotKey
    index: int
    start: dt.datetime
    end: dt.datetime
    capacity_ms: int
    is_open: bool


def _window(work_date: dt.date, shift_index: int, shift_hours: float) -> tuple[dt.datetime,
                                                                              dt.datetime]:
    midnight = dt.datetime.combine(work_date, dt.time(0, 0))
    start = midnight + dt.timedelta(hours=DAY_START_HOUR + shift_index * shift_hours)
    return start, start + dt.timedelta(hours=shift_hours)


def build_slots(lines: Sequence[LineGeometry], start: dt.date, horizon_days: int,
                mode: CalendarMode,
                overrides: Mapping[SlotKey, bool] | None = None,
                log: IssueLog | None = None) -> tuple[Slot, ...]:
    overrides = overrides or {}
    slots: list[Slot] = []
    for line in sorted(lines, key=lambda l: l.line_code):
        capacity_ms = int(round(line.shift_hours * MS_PER_HOUR))
        for day_offset in range(max(0, horizon_days)):
            work_date = start + dt.timedelta(days=day_offset)
            pattern_open = is_working_day(mode, work_date)
            for shift_index in range(line.shifts_per_day):
                key = SlotKey(line.line_code, work_date, shift_index)
                start_at, end_at = _window(work_date, shift_index, line.shift_hours)
                is_open = pattern_open
                if key in overrides:
                    declared = bool(overrides[key])
                    if declared != pattern_open and log is not None:
                        log.warning(
                            entity="calendar_day", field="is_open", sheet="工作日历",
                            row=None, column="是否开班", value=declared,
                            message=f"工作日历对 {line.line_code} {work_date} "
                                    f"班次 {shift_index} 的开班声明 {declared} 与 "
                                    f"{mode.value} 推导结果 {pattern_open} 冲突，"
                                    f"以工作日历为准",
                        )
                    is_open = declared
                slots.append(Slot(
                    key=key,
                    index=day_offset * line.shifts_per_day + shift_index,
                    start=start_at, end=end_at,
                    capacity_ms=capacity_ms, is_open=is_open,
                ))
    return tuple(slots)
