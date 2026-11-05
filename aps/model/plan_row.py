from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from ortools.sat.python import cp_model

from aps.calendar.geometry import MS_PER_HOUR, Slot
from aps.model.context import StructureContext

__all__ = [
    "LineShiftSummary",
    "PlanRow",
    "extract_line_shifts",
    "extract_plan_rows",
    "shift_name_for",
]


@dataclass(frozen=True)
class PlanRow:
    site: str
    line_code: str
    date_str: str                           # "YYYY-MM-DD"
    shift_name: str                         # "早班" | "中班" | "晚班"
    slot_index: int
    task_id: str
    pn: str
    stage: str                              # "FG" | "SF"
    quantity: int
    start_min: float
    end_min: float
    setup_min: float
    is_tail_shift: bool


@dataclass(frozen=True)
class LineShiftSummary:
    site: str
    line_code: str
    date_str: str
    shift_name: str
    slot_index: int
    is_open: bool
    is_tail: bool
    cap_hours: float
    actual_hours: float
    utilization_pct: float


def shift_name_for(shifts_per_day: int, shift_index: int) -> str:
    if shifts_per_day == 3:
        return ("早班", "中班", "晚班")[shift_index % 3]
    if shifts_per_day == 2:
        return ("白班", "夜班")[shift_index % 2]
    return "白班"


def extract_plan_rows(ctx: StructureContext, solver: cp_model.CpSolver) -> list[PlanRow]:
    rows: list[PlanRow] = []
    ds = ctx.dataset
    lines = {l.line_code: l for l in ds.lines}
    slots_by_line_and_idx: dict[tuple[str, int], Slot] = {
        (s.key.line_code, s.index): s for s in ds.slots
    }

    for line in ds.lines:
        l_code = line.line_code
        line_slots = ds.slot_count(l_code)

        for s in range(line_slots):
            current_min = 0.0
            is_tail = bool(solver.Value(ctx.is_tail[(l_code, s)]))
            slot = slots_by_line_and_idx.get((l_code, s))
            if slot:
                date_str = slot.key.work_date.isoformat()
                s_name = shift_name_for(line.shifts_per_day, slot.key.shift_index)
            else:
                day_offset = s // line.shifts_per_day
                s_idx = s % line.shifts_per_day
                date_str = (ds.horizon.start_date + dt.timedelta(days=day_offset)).isoformat()
                s_name = shift_name_for(line.shifts_per_day, s_idx)

            for t_id in ctx.task_ids:
                if (t_id, l_code, s) not in ctx.x:
                    continue
                qty = int(round(solver.Value(ctx.x[(t_id, l_code, s)])))
                if qty <= 0:
                    continue

                task = ctx.tasks_by_id[t_id]
                route = ds.route(task.pn, l_code)
                is_start = bool(solver.Value(ctx.started[(t_id, l_code, s)]))
                setup_min = (route.changeover_min if route and is_start else 0.0)

                run_ms = qty * (route.unit_ms if route else 20000)
                run_min = run_ms / 60000.0

                start_min = current_min + setup_min
                end_min = start_min + run_min
                current_min = end_min

                rows.append(
                    PlanRow(
                        site=ds.horizon.site,
                        line_code=l_code,
                        date_str=date_str,
                        shift_name=s_name,
                        slot_index=s,
                        task_id=t_id,
                        pn=task.pn,
                        stage=task.stage,
                        quantity=qty,
                        start_min=round(start_min, 1),
                        end_min=round(end_min, 1),
                        setup_min=round(setup_min, 1),
                        is_tail_shift=is_tail,
                    )
                )

    rows.sort(key=lambda r: (r.line_code, r.slot_index, r.start_min))
    return rows


def extract_line_shifts(ctx: StructureContext, solver: cp_model.CpSolver) -> list[LineShiftSummary]:
    summaries: list[LineShiftSummary] = []
    ds = ctx.dataset
    lines = {l.line_code: l for l in ds.lines}
    slots_by_line_and_idx: dict[tuple[str, int], Slot] = {
        (s.key.line_code, s.index): s for s in ds.slots
    }

    for line in ds.lines:
        l_code = line.line_code
        line_slots = ds.slot_count(l_code)
        cap_hours = line.shift_hours
        cap_ms = cap_hours * MS_PER_HOUR

        for s in range(line_slots):
            is_open = bool(solver.Value(ctx.is_open[(l_code, s)]))
            is_tail = bool(solver.Value(ctx.is_tail[(l_code, s)]))
            load_ms = int(round(solver.Value(ctx.load_ms[(l_code, s)])))
            actual_hours = load_ms / float(MS_PER_HOUR)
            utilization_pct = (actual_hours / cap_hours * 100.0) if cap_hours > 0 and is_open else 0.0

            slot = slots_by_line_and_idx.get((l_code, s))
            if slot:
                date_str = slot.key.work_date.isoformat()
                s_name = shift_name_for(line.shifts_per_day, slot.key.shift_index)
            else:
                day_offset = s // line.shifts_per_day
                s_idx = s % line.shifts_per_day
                date_str = (ds.horizon.start_date + dt.timedelta(days=day_offset)).isoformat()
                s_name = shift_name_for(line.shifts_per_day, s_idx)

            summaries.append(
                LineShiftSummary(
                    site=ds.horizon.site,
                    line_code=l_code,
                    date_str=date_str,
                    shift_name=s_name,
                    slot_index=s,
                    is_open=is_open,
                    is_tail=is_tail,
                    cap_hours=round(cap_hours, 2),
                    actual_hours=round(actual_hours, 2),
                    utilization_pct=round(utilization_pct, 1),
                )
            )

    summaries.sort(key=lambda s: (s.line_code, s.slot_index))
    return summaries
