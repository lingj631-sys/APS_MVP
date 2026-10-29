"""Microscopic Intra-Shift Interval Scheduling Engine (Stage 2 of the Two-Stage Hybrid Architecture).

Solves the continuous-time scheduling within each shift:
1. HC-01: Mold exclusivity across lines (NoOverlap on mold interval [setup_start, processing_end]).
2. HC-02: Setup technician capacity (Cumulative on setup intervals <= technician_capacity).
3. HC-03: Minute-level PM avoidance (Tasks on the line NoOverlap with PM windows).
4. Line sequence: Tasks on the same line NoOverlap.

Because problem scale per shift is small (typically <= 10 tasks across 2-4 lines),
CP-SAT solves this in milliseconds without any MTZ sub-tour combinatorial explosion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ortools.sat.python import cp_model

from src.shift_manager import ShiftBucket


@dataclass
class MicroTaskSegment:
    task_id: str
    line: str
    part_no: str
    mold_id: str
    quantity: float
    duration_seconds: float
    setup_seconds: float = 0.0
    due_datetime: Optional[datetime] = None
    technicians_required: int = 1


@dataclass
class PMWindow:
    line: str
    start_seconds: float  # seconds from shift start
    end_seconds: float    # seconds from shift start
    reason: str = "PM"


@dataclass
class MicroScheduledSegment:
    task_id: str
    line: str
    part_no: str
    mold_id: str
    quantity: float
    setup_start: float
    setup_end: float
    processing_start: float
    processing_end: float
    technicians: int
    start_dt: datetime = field(default_factory=datetime.now)
    end_dt: datetime = field(default_factory=datetime.now)


@dataclass
class MicroShiftSchedule:
    shift_index: int
    shift_start_dt: datetime
    shift_duration_seconds: float
    segments: List[MicroScheduledSegment] = field(default_factory=list)
    setup_minutes_total: float = 0.0
    technicians_peak: int = 0
    is_feasible: bool = True


def schedule_intra_shift(
    shift: ShiftBucket,
    tasks: Sequence[MicroTaskSegment],
    *,
    pm_windows: Optional[Sequence[PMWindow]] = None,
    technician_capacity: int = 2,
    preserve_line_order: bool = True,
    time_limit_seconds: float = 2.0,
) -> MicroShiftSchedule:
    """Schedule continuous-time task execution within a single shift.

    Applies:
    - Non-overlapping task intervals on each line (respecting optimal line sequence);
    - HC-01: Mold exclusivity across lines via NoOverlap;
    - HC-02: Setup technician concurrency limit via Cumulative constraint;
    - HC-03: PM maintenance window avoidance on the line;
    - Objective: Minimize completion times and tardiness.
    """
    active_tasks = [t for t in tasks if t.duration_seconds > 0 or t.setup_seconds > 0]
    schedule = MicroShiftSchedule(
        shift_index=shift.index,
        shift_start_dt=shift.start_dt,
        shift_duration_seconds=shift.duration_seconds,
    )
    if not active_tasks:
        return schedule

    model = cp_model.CpModel()
    shift_limit = int(round(shift.duration_seconds))
    total_duration = sum(int(round(t.duration_seconds + t.setup_seconds)) for t in active_tasks)
    horizon = max(shift_limit * 2, total_duration * 2, 86400)

    # For each task, create setup and processing intervals
    task_vars = {}
    line_task_intervals: Dict[str, List[Any]] = {}
    tasks_by_line: Dict[str, List[MicroTaskSegment]] = {}
    mold_task_intervals: Dict[str, List[Any]] = {}
    setup_intervals: List[Any] = []
    setup_demands: List[int] = []

    for idx, t in enumerate(active_tasks):
        tasks_by_line.setdefault(t.line, []).append(t)
        setup_sec = max(0, int(round(t.setup_seconds)))
        proc_sec = max(1, int(round(t.duration_seconds)))
        tot_sec = setup_sec + proc_sec

        s_setup = model.NewIntVar(0, horizon, f"s_setup.{idx}.{t.task_id}")
        e_setup = model.NewIntVar(0, horizon, f"e_setup.{idx}.{t.task_id}")
        s_proc = model.NewIntVar(0, horizon, f"s_proc.{idx}.{t.task_id}")
        e_proc = model.NewIntVar(0, horizon, f"e_proc.{idx}.{t.task_id}")

        if setup_sec > 0:
            setup_iv = model.NewIntervalVar(s_setup, setup_sec, e_setup, f"setup_iv.{idx}.{t.task_id}")
            setup_intervals.append(setup_iv)
            setup_demands.append(max(1, t.technicians_required))
            # Processing starts immediately when setup finishes
            model.Add(s_proc == e_setup)
        else:
            model.Add(s_setup == e_setup)
            model.Add(s_proc == s_setup)

        proc_iv = model.NewIntervalVar(s_proc, proc_sec, e_proc, f"proc_iv.{idx}.{t.task_id}")
        total_iv = model.NewIntervalVar(s_setup, tot_sec, e_proc, f"tot_iv.{idx}.{t.task_id}")

        task_vars[t.task_id] = {
            "s_setup": s_setup,
            "e_setup": e_setup,
            "s_proc": s_proc,
            "e_proc": e_proc,
            "setup_sec": setup_sec,
            "proc_sec": proc_sec,
            "task": t,
        }

        # Collect for line non-overlap
        line_task_intervals.setdefault(t.line, []).append(total_iv)

        # Collect for mold non-overlap (HC-01)
        if t.mold_id:
            mold_task_intervals.setdefault(t.mold_id, []).append(total_iv)

    # Precedence on each line if line order is preserved
    if preserve_line_order:
        for line, l_tasks in tasks_by_line.items():
            for a, b in zip(l_tasks[:-1], l_tasks[1:]):
                model.Add(task_vars[b.task_id]["s_setup"] >= task_vars[a.task_id]["e_proc"])

    # 1. Line Non-Overlap & HC-03 PM Avoidance
    pms = pm_windows or []
    for line, intervals in line_task_intervals.items():
        # Add PM windows for this line
        line_pms = [p for p in pms if p.line == line]
        for p_idx, pm in enumerate(line_pms):
            pm_st = max(0, int(round(pm.start_seconds)))
            pm_dur = max(1, int(round(pm.end_seconds - pm.start_seconds)))
            pm_iv = model.NewFixedSizeIntervalVar(pm_st, pm_dur, f"pm_iv.{line}.{p_idx}")
            intervals.append(pm_iv)
        model.AddNoOverlap(intervals)

    # 2. HC-01: Mold Exclusivity across Lines
    for mold_id, intervals in mold_task_intervals.items():
        if len(intervals) > 1:
            model.AddNoOverlap(intervals)

    # 3. HC-02: Setup Technician Concurrency Limit
    if setup_intervals and technician_capacity > 0:
        model.AddCumulative(setup_intervals, setup_demands, max(1, technician_capacity))

    # Objective: Minimize total makespan and penalize completion beyond shift
    objective_terms = []
    for tv in task_vars.values():
        objective_terms.append(tv["e_proc"])
        # Heavy penalty if tasks spill over the shift duration
        over = model.NewIntVar(0, horizon, f"over.{tv['task'].task_id}")
        model.Add(over >= tv["e_proc"] - shift_limit)
        objective_terms.append(100 * over)

    model.Minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = 1

    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # Fallback to simple sequential layout if unconstrained
        schedule.is_feasible = False
        cursor = 0.0
        for t in active_tasks:
            st = cursor
            sp = st + t.setup_seconds
            ep = sp + t.duration_seconds
            schedule.segments.append(
                MicroScheduledSegment(
                    task_id=t.task_id,
                    line=t.line,
                    part_no=t.part_no,
                    mold_id=t.mold_id,
                    quantity=t.quantity,
                    setup_start=st,
                    setup_end=sp,
                    processing_start=sp,
                    processing_end=ep,
                    technicians=t.technicians_required if t.setup_seconds > 0 else 0,
                    start_dt=shift.start_dt + timedelta(seconds=st),
                    end_dt=shift.start_dt + timedelta(seconds=ep),
                )
            )
            cursor = ep
        return schedule

    # Extract successful micro schedule
    total_setup_min = 0.0
    scheduled_segments = []
    for t in active_tasks:
        tv = task_vars[t.task_id]
        s_setup_val = float(solver.Value(tv["s_setup"]))
        e_setup_val = float(solver.Value(tv["e_setup"]))
        s_proc_val = float(solver.Value(tv["s_proc"]))
        e_proc_val = float(solver.Value(tv["e_proc"]))

        setup_min = (e_setup_val - s_setup_val) / 60.0
        total_setup_min += setup_min

        scheduled_segments.append(
            MicroScheduledSegment(
                task_id=t.task_id,
                line=t.line,
                part_no=t.part_no,
                mold_id=t.mold_id,
                quantity=t.quantity,
                setup_start=s_setup_val,
                setup_end=e_setup_val,
                processing_start=s_proc_val,
                processing_end=e_proc_val,
                technicians=t.technicians_required if setup_min > 0 else 0,
                start_dt=shift.start_dt + timedelta(seconds=s_setup_val),
                end_dt=shift.start_dt + timedelta(seconds=e_proc_val),
            )
        )

    # Compute peak technicians used simultaneously
    peak_techs = 0
    if setup_intervals:
        # Check active setups across discretized seconds
        # Sort departures (-delta) before arrivals (+delta) at the exact same second
        events = []
        for seg in scheduled_segments:
            if seg.setup_end > seg.setup_start:
                events.append((seg.setup_start, seg.technicians))
                events.append((seg.setup_end, -seg.technicians))
        events.sort(key=lambda x: (x[0], x[1]))
        current = 0
        for _time, delta in events:
            current += delta
            if current > peak_techs:
                peak_techs = current

    schedule.segments = scheduled_segments
    schedule.setup_minutes_total = total_setup_min
    schedule.technicians_peak = peak_techs
    schedule.is_feasible = True
    return schedule
