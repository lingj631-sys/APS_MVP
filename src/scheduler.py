"""
Shift-slot CP-SAT scheduling engine for the APS MVP.

Decision variable: how many lots of a task land in a shift, a lot being one MPQ
batch for finished goods and a single piece for semi-finished work. Making the
shift the atomic time unit turns the WE34 hard constraints into linear
inequalities instead of post-hoc repairs:

- capacity    : line-shift seconds <= 8h
- utilization : line-shift seconds >= 95% * 8h for every worked slot except the
                closing one
- kitting     : the bushings available by the end of shift x-1 cover what shift
                x assembles, so the two stages stream lot by lot
- lots        : FG only whole MPQ batches; SF any integer count, but no shift may
                start a task below its MPQ minimum batch quantity
- changeover  : production runs are counted and priced, so a model gathers into
                one block instead of trickling across the horizon

CT / TRP gives a fractional seconds per piece and CP-SAT takes only integer
coefficients, so every duration here is an integer count of milliseconds.

Batch granularity makes the 95% rule unreachable on some datasets, so the engine
tries the constraints in the order the business ranks them: utilisation and
delivery both hard, then delivery hard with utilisation penalised, then delivery
itself penalised. The validator reports whichever violations survive.
"""
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ortools.sat.python import cp_model

from src.data_loader import TIME_SCALE, ProductionTask, RequirementPlan
from src.shift_manager import (
    CalendarMode,
    MIN_SHIFT_SECONDS,
    SHIFT_SECONDS,
    ShiftBucket,
    generate_shift_calendar,
    last_shift_on_or_before,
)

SHIFT_MS = SHIFT_SECONDS * TIME_SCALE
MIN_SHIFT_MS = MIN_SHIFT_SECONDS * TIME_SCALE

SHORTFALL_WEIGHT = 1
FG_COMPLETION_WEIGHT = 1
SF_COMPLETION_WEIGHT = 1
# A changeover is one model's run starting on a shift. Worth 300 seconds of shift
# time here: more than finishing a model a few shifts earlier, which is what gathers
# the model into one block instead of trickling it across the week.
CHANGEOVER_WEIGHT = 300_000
CONCURRENCY_WEIGHT = 50_000
LATENESS_SLACK_SHIFTS = 6

ShiftKey = Tuple[str, str]
Window = Tuple[int, int]


class _SolutionStopper(cp_model.CpSolverSolutionCallback):
    """Cuts a search short once a published solution passes the test."""

    def __init__(self, test):
        super().__init__()
        self._test = test

    def on_solution_callback(self):
        if self._test(self):
            self.StopSearch()


@dataclass
class ScheduledTask:
    task_id: str
    stage: str
    line: str
    part_no: str
    description: str
    ct_seconds: float
    trp: float
    mpq: int
    lot_size: int
    order_id: str
    quantity: float
    lots: int
    start_dt: datetime
    end_dt: datetime
    duration_hours: float
    due_datetime: Optional[datetime] = None
    shift_breakdown: Dict[ShiftKey, int] = field(default_factory=dict)
    shift_seconds: Dict[ShiftKey, float] = field(default_factory=dict)
    mold_id: Optional[str] = None
    material_arrival_shift: int = 0
    customer_tier: str = "A"
    line_pm_slots: List[int] = field(default_factory=list)


@dataclass
class ScheduleResult:
    tasks: List[ScheduledTask]
    shifts: List[ShiftBucket]
    strict_utilization: bool
    status_name: str
    objective_value: float
    wall_time_seconds: float


class APSScheduler:
    def __init__(
        self,
        plan: RequirementPlan,
        start_datetime: datetime = datetime(2026, 10, 12, 7, 0, 0),
        planning_days: int = 14,
        calendar_mode: CalendarMode = CalendarMode.SEVEN_ZERO,
        enforce_utilization_95: bool = True,
        enforce_on_time_delivery: bool = True,
        enforce_concurrency_limit: bool = True,
        enforce_mpq_batch: bool = True,
        enforce_material_balance: bool = True,
        price_changeovers: bool = True,
        day_shift_overrides: Optional[Dict[str, Any]] = None,
        enforce_sequential_pulling: bool = True,
        enforce_freeze_window: bool = True,
        freeze_shifts: int = 0,
        enforce_tooling_exclusivity: bool = True,
        enforce_material_arrival: bool = True,
        enforce_pm_maintenance: bool = True,
        pm_shifts: Optional[Dict[str, List[int]]] = None,
        enforce_customer_priority: bool = True,
        enforce_weekend_penalty: bool = True,
        enforce_exact_demand: bool = True,
        allow_105_utilization_fg: bool = False,
        allow_105_utilization_sf: bool = False,
    ):
        self.plan = plan
        self.start_dt = start_datetime
        self.calendar_mode = calendar_mode
        self.enforce_utilization_95 = enforce_utilization_95
        self.enforce_on_time_delivery = enforce_on_time_delivery
        self.enforce_concurrency_limit = enforce_concurrency_limit
        self.enforce_mpq_batch = enforce_mpq_batch
        self.enforce_material_balance = enforce_material_balance
        self.price_changeovers = price_changeovers
        self.day_shift_overrides = day_shift_overrides
        self.enforce_sequential_pulling = enforce_sequential_pulling
        self.enforce_freeze_window = enforce_freeze_window
        self.freeze_shifts = freeze_shifts
        self.enforce_tooling_exclusivity = enforce_tooling_exclusivity
        self.enforce_material_arrival = enforce_material_arrival
        self.enforce_pm_maintenance = enforce_pm_maintenance
        self.pm_shifts = pm_shifts or {}
        self.enforce_customer_priority = enforce_customer_priority
        self.enforce_weekend_penalty = enforce_weekend_penalty
        self.enforce_exact_demand = enforce_exact_demand
        self.allow_105_utilization_fg = allow_105_utilization_fg
        self.allow_105_utilization_sf = allow_105_utilization_sf

        self.shifts = generate_shift_calendar(
            start_datetime,
            planning_days,
            calendar_mode=calendar_mode,
            day_shift_overrides=day_shift_overrides,
        )
        self.num_shifts = len(self.shifts)
        self.tasks = plan.tasks
        self.task_by_id = {t.task_id: t for t in self.tasks}
        self.lines = sorted({t.line for t in self.tasks})
        self.windows = self._build_windows()

    def _get_line_max_shift_ms(self, line: str) -> int:
        is_fg = any(t.stage == "FG" for t in self.tasks if t.line == line)
        allow_105 = self.allow_105_utilization_fg if is_fg else self.allow_105_utilization_sf
        return int(round(SHIFT_MS * 1.05)) if allow_105 else SHIFT_MS

    def solve(self, time_limit_seconds: float = 90.0) -> ScheduleResult:
        # WE34 ranks the constraints: on-time delivery first, 95% shift utilisation
        # second, whole models per line third.
        start = time.perf_counter()
        # A due-date-feasible packing is hard for CP-SAT to *find* and cheap to
        # improve, so one is solved for up front and used to seed every tier;
        # without it the budget goes on search rather than on filling.
        hint = self._build_hint(time_limit_seconds * 0.06)

        # The tiers below only soften the constraint the business ranks lower, so the
        # first one that yields a plan wins and keeps whatever time is left for
        # compacting it.
        if not self.enforce_utilization_95:
            tiers = (
                ("关闭 95% 利用率约束 (" + ("硬约束交付" if self.enforce_on_time_delivery else "交付损失最小化") + ")", "off", self.enforce_on_time_delivery, 1.0),
            )
        elif self.enforce_on_time_delivery:
            tiers = (
                ("软约束 95% 利用率 + 硬约束准时交付", "soft", True, 0.55),
                ("软约束 95% 利用率 + 交付损失最小化", "soft", False, 1.0),
            )
        else:
            tiers = (
                ("软约束 95% 利用率 + 交付损失最小化", "soft", False, 1.0),
            )

        for index, (label, mode, hard_due, share) in enumerate(tiers):
            if index:
                print(f"  [warn]  上一口径无解，降级为: {label}")
            remaining = max(1.0, time_limit_seconds - (time.perf_counter() - start))
            result = self._solve_tier(mode, hard_due, remaining * share, hint)
            if result is not None:
                print(f"  [solver] 排产口径: {label}")
                left = time_limit_seconds - (time.perf_counter() - start)
                if mode == "soft" and left > 5.0 and self.enforce_utilization_95:
                    result = self._compact(result, hard_due, left)
                return result

        raise RuntimeError("Solver found no feasible schedule in any tier.")

    def _compact(self, result: ScheduleResult, hard_due: bool, budget: float) -> ScheduleResult:
        """Restore 95% where it slipped, then group the models around it.

        The filling tier only prices its gaps, so it hands over a plan that is close to
        compliant rather than certainly compliant, and in no particular order. Re-solving
        from that plan with 95% as a hard constraint does both jobs at once: warm-started
        the compliant region is within reach, and changeovers can be priced inside it
        because they can no longer be paid for with gaps. If the region proves empty -
        whole MPQ batches and a 95% band do not always add up - the pass falls back to
        the gaps the plan already has, capped, so grouping never costs utilisation.
        """
        blocks, late = self._plan_metrics(result)
        started = time.perf_counter()
        rungs = (
            ("硬约束 95%", "hard", None, 0.70),
            ("沿用当前缺口", "soft", self._gap_metrics(result), 1.0),
        )
        for label, mode, cap, share in rungs:
            left = budget - (time.perf_counter() - started)
            if left < 3.0:
                break
            model, x = self._build_model(mode, hard_due, gap_cap=cap)
            plan, status_name, _ = self._run(model, x, mode, left * share, self._lot_values(result))
            if plan is None:
                print(f"  [solver] 换型集中: {label} 未求得（{status_name}）")
                continue
            grouped, on_time = self._plan_metrics(plan)
            if on_time > late:
                print(f"  [solver] 换型集中: {label} 会多延误 {on_time - late} 个成品订单")
                continue
            if mode == "soft" and grouped >= blocks:
                print(f"  [solver] 换型集中: 已是当前利用率下的最少换型排法（{grouped} 块）")
                continue
            print(f"  [solver] 换型集中（{label}）: 型号生产块 {blocks} -> {grouped}，交付不降级")
            return plan

        print("  [solver] 换型集中: 保留原计划")
        return result

    def _plan_metrics(self, result: ScheduleResult) -> Tuple[int, int]:
        """Production blocks (changeovers to pay) and finished goods past their due date."""
        index_of = {(s.date_str, s.shift_name): s.index for s in self.shifts}
        blocks = 0
        late = 0
        for task in result.tasks:
            slots = sorted(index_of[key] for key in task.shift_breakdown)
            blocks += sum(1 for n, slot in enumerate(slots) if n == 0 or slot != slots[n - 1] + 1)
            if task.stage == "FG" and task.end_dt > task.due_datetime:
                late += 1
        return blocks, late

    def _gap_metrics(self, result: ScheduleResult) -> Tuple[int, int, int]:
        """Utilisation profile to preserve: total and worst sub-95% deficit in
        milliseconds, plus how many slots fall short (tail slots exempt)."""
        index_of = {(s.date_str, s.shift_name): s.index for s in self.shifts}
        loads: Dict[Tuple[str, int], float] = {}
        for task in result.tasks:
            for key, seconds in task.shift_seconds.items():
                spot = (task.line, index_of[key])
                loads[spot] = loads.get(spot, 0.0) + seconds

        total = worst = count = 0
        by_line: Dict[str, List[Tuple[int, float]]] = {}
        for (line, idx), seconds in loads.items():
            by_line.setdefault(line, []).append((idx, seconds))
        for entries in by_line.values():
            entries.sort()
            for _, seconds in entries[:-1]:
                gap = max(0, MIN_SHIFT_MS - int(round(seconds * TIME_SCALE)))
                total += gap
                worst = max(worst, gap)
                count += gap > 0
        return total, worst, count

    def _lot_values(self, result: ScheduleResult) -> Dict[Tuple[str, int], int]:
        """Flip an extracted plan back into per-(task, shift) lot counts for hinting."""
        index_of = {(s.date_str, s.shift_name): s.index for s in self.shifts}
        return {
            (task.task_id, index_of[key]): qty // task.lot_size
            for task in result.tasks
            for key, qty in task.shift_breakdown.items()
        }

    def _solve_tier(self, mode: str, hard_due: bool, budget: float, hint):
        label = "硬约束" if mode == "hard" else "软约束"
        model, x = self._build_model(mode, hard_due)
        # 'soft' is the tier that has to *find* a violation-free packing rather
        # than prove one optimal, so it stops the moment it holds one.
        stopper = None if mode == "hard" else _SolutionStopper(self._clean_filling(x))
        result, status_name, wall = self._run(model, x, mode, budget, hint, stopper)
        if result is None:
            print(f"  [solver] {label}: {status_name}")
            return None

        gaps = self._gap_metrics(result)
        print(f"\n--- Solver Finished (利用率约束: {label}) ---")
        print(f"Status: {result.status_name} | Objective: {result.objective_value:.0f} | Wall Time: {wall:.2f}s")
        print(f"  [solver] 利用率缺口: {gaps[2]} 个班次，合计 {gaps[0] / 1000:.0f}s")
        return result

    def _clean_filling(self, x):
        """Test used to cut the filling tier short: every worked slot but the tail clears 95%."""

        def is_clean(callback) -> bool:
            loads: Dict[Tuple[str, int], int] = {}
            for (task_id, slot), var in x.items():
                lots = callback.Value(var)
                if lots:
                    spot = (self.task_by_id[task_id].line, slot)
                    loads[spot] = loads.get(spot, 0) + lots * self.task_by_id[task_id].lot_millis
            by_line: Dict[str, List[Tuple[int, int]]] = {}
            for (line, slot), millis in loads.items():
                by_line.setdefault(line, []).append((slot, millis))
            return all(
                millis >= MIN_SHIFT_MS
                for entries in by_line.values()
                for _, millis in sorted(entries)[:-1]
            )

        return is_clean

    def _build_hint(self, time_limit_seconds: float) -> Optional[Dict[Tuple[str, int], int]]:
        """Capacity, kitting and due dates only: the quickest way to a valid plan."""
        model, x = self._build_model("off", hard_due=True)
        # Any due-feasible packing is a usable seed, so ask for feasibility rather
        # than optimality; the harder tiers do the improving from here.
        model.Minimize(0)
        solver = self._solver(time_limit_seconds)
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            print(f"  [solver] 初始可行提示: {solver.StatusName(status)}")
            return None
        print(f"  [solver] 初始可行提示: {solver.StatusName(status)} ({solver.WallTime():.1f}s)")
        return {key: solver.Value(var) for key, var in x.items()}

    def _solver(self, time_limit_seconds: float) -> cp_model.CpSolver:
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max(1.0, time_limit_seconds)
        solver.parameters.num_workers = 8
        solver.parameters.random_seed = 1
        solver.parameters.log_search_progress = False
        return solver

    def _run(self, model, x, mode: str, time_limit_seconds: float, hint, stopper=None):
        for key, value in (hint or {}).items():
            model.AddHint(x[key], value)

        solver = self._solver(time_limit_seconds)
        status = solver.Solve(model, stopper)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None, solver.StatusName(status), solver.WallTime()

        result = ScheduleResult(
            tasks=self._extract(solver, x),
            shifts=self.shifts,
            strict_utilization=mode == "hard",
            status_name=solver.StatusName(status),
            objective_value=solver.ObjectiveValue(),
            wall_time_seconds=solver.WallTime(),
        )
        return result, result.status_name, solver.WallTime()

    def _due_shift(self, task: ProductionTask) -> int:
        return last_shift_on_or_before(self.shifts, task.due_datetime)

    def _build_windows(self) -> Dict[str, Window]:
        windows: Dict[str, Window] = {}
        for task in self.plan.fg_tasks:
            windows[task.task_id] = (0, min(self.num_shifts - 1, self._due_shift(task) + LATENESS_SLACK_SHIFTS))
        for task in self.plan.sf_tasks:
            parent_hi = windows[task.parent_task_id][1]
            windows[task.task_id] = (0, max(0, min(parent_hi - 1, self.num_shifts - 1)))
        return windows

    def _build_model(self, utilization_mode: str, hard_due: bool, gap_cap=None):
        model = cp_model.CpModel()
        x: Dict[Tuple[str, int], object] = {}
        active: Dict[Tuple[str, int], object] = {}
        end_of: Dict[str, object] = {}
        # A changeover is paid per production run, not per shift, so the runs are only
        # counted in the models where they are priced: the hard tier and the
        # compaction pass, where the filling is already secured and cannot be bought
        # back down. Priced against soft gaps, a changeover simply buys gaps.
        price_changeovers = self.price_changeovers and (utilization_mode == "hard" or gap_cap is not None)
        runs: List[object] = []

        for task in self.tasks:
            lo, hi = self.windows[task.task_id]
            # One lot sets the floor of what a shift can hold; no task can
            # contribute more lots than that. Capping the domain here is
            # what lets CP-SAT propagate capacity instead of discovering it late.
            max_line_ms = self._get_line_max_shift_ms(task.line)
            per_shift = max(1, min(task.lots, max_line_ms // task.lot_millis))
            # ...and a shift that opens a task has to run its minimum batch: a whole
            # MPQ for semi-finished pieces, one MPQ batch for finished goods. Without
            # this the filling objective drips a model over dozens of shifts as 1-2
            # pieces at a time, which no line can actually set up for.
            min_lots = min(per_shift, task.mpq // task.lot_size) if self.enforce_mpq_batch else 1
            for s in range(lo, hi + 1):
                lots = model.NewIntVar(0, per_shift, f"x.{task.task_id}.{s}")
                used = model.NewBoolVar(f"a.{task.task_id}.{s}")
                model.Add(lots >= min_lots * used)
                model.Add(lots <= per_shift * used)
                x[task.task_id, s] = lots
                active[task.task_id, s] = used
            model.Add(sum(x[task.task_id, s] for s in range(lo, hi + 1)) == task.lots)

            if price_changeovers:
                # A run starts on any shift that produces the model while the shift
                # before it did not, so counting starts is counting changeovers.
                runs.append(active[task.task_id, lo])
                for s in range(lo + 1, hi + 1):
                    started = model.NewBoolVar(f"open.{task.task_id}.{s}")
                    model.Add(started >= active[task.task_id, s] - active[task.task_id, s - 1])
                    runs.append(started)

            end = model.NewIntVar(lo, hi, f"end.{task.task_id}")
            for s in range(lo, hi + 1):
                model.Add(end >= s).OnlyEnforceIf(active[task.task_id, s])
            end_of[task.task_id] = end

        if self.enforce_material_balance:
            self._add_material_balance(model, x)
        shortfall, violations, overloads = self._add_line_capacity(model, x, utilization_mode, gap_cap)

        # R-SF-02 & R-FG-05: 顺位紧邻前拉硬约束（同产线同型号按交期先后严格顺位排产，禁止跳序挑单）
        if self.enforce_sequential_pulling:
            by_line_part = defaultdict(list)
            for t in self.tasks:
                by_line_part[(t.line, t.part_no)].append(t)
            for group in by_line_part.values():
                if len(group) > 1:
                    sorted_group = sorted(group, key=lambda t: t.due_datetime or datetime.max)
                    for i in range(len(sorted_group) - 1):
                        ta, tb = sorted_group[i], sorted_group[i + 1]
                        if ta.due_datetime and tb.due_datetime and ta.due_datetime < tb.due_datetime:
                            model.Add(end_of[ta.task_id] <= end_of[tb.task_id])

        # R-TL-01: 工装模具物理独占硬约束（同班次跨产线互斥，禁止并发占用同一模具）
        if self.enforce_tooling_exclusivity:
            mold_groups = defaultdict(list)
            for t in self.tasks:
                mold_id = getattr(t, 'mold_id', None)
                if mold_id:
                    mold_groups[mold_id].append(t)
            for mold_id, group in mold_groups.items():
                lines_in_grp = {t.line for t in group}
                if len(lines_in_grp) > 1:
                    for s in range(self.num_shifts):
                        active_vars = [active[t.task_id, s] for t in group if (t.task_id, s) in active]
                        if len(active_vars) > 1:
                            model.Add(sum(active_vars) <= 1)

        # R-MT-01: 关键物料预计到货齐套开工硬约束（到料前禁止开工）
        if self.enforce_material_arrival:
            for t in self.tasks:
                arr_shift = getattr(t, 'material_arrival_shift', 0)
                if arr_shift > 0:
                    lo, hi = self.windows[t.task_id]
                    for s in range(lo, min(arr_shift, hi + 1)):
                        if (t.task_id, s) in x:
                            model.Add(x[t.task_id, s] == 0)

        # R-PM-01: 设备计划性预防维护（PM）避让硬约束（PM时段产能清零，任务避让）
        if self.enforce_pm_maintenance and self.pm_shifts:
            for line, pm_slots in self.pm_shifts.items():
                line_tasks = [t for t in self.tasks if t.line == line]
                for s in pm_slots:
                    if 0 <= s < self.num_shifts:
                        for t in line_tasks:
                            if (t.task_id, s) in x:
                                model.Add(x[t.task_id, s] == 0)

        lateness = []
        for task in self.plan.fg_tasks:
            due = self._due_shift(task)
            if hard_due:
                model.Add(end_of[task.task_id] <= due)
                continue
            late = model.NewIntVar(0, self.num_shifts, f"late.{task.task_id}")
            model.Add(late >= end_of[task.task_id] - due)
            # R-CR-01: 客户战略评级加权保供（核心客户优先交付）
            if self.enforce_customer_priority:
                cust_tier = getattr(task, 'customer_tier', 'A')
                mult = 10 if cust_tier == 'STRATEGIC' or 'WA' in task.part_no else (3 if cust_tier == 'A' else 1)
                lateness.append(late * mult)
            else:
                lateness.append(late)

        terms = [
            FG_COMPLETION_WEIGHT * sum(end_of[t.task_id] for t in self.plan.fg_tasks),
            SF_COMPLETION_WEIGHT * sum(end_of[t.task_id] for t in self.plan.sf_tasks),
        ]

        # R-FG-13: Penalize parallel models exceeding 3 per shift
        concurrency_penalties = []
        for line in self.lines:
            line_tasks = [t for t in self.tasks if t.line == line]
            part_numbers = sorted({t.part_no for t in line_tasks})
            for s in range(self.num_shifts):
                part_actives = []
                for p_no in part_numbers:
                    matching = [t for t in line_tasks if t.part_no == p_no and (t.task_id, s) in active]
                    if not matching:
                        continue
                    p_act = model.NewBoolVar(f"p_act.{line}.{p_no}.{s}")
                    for m in matching:
                        model.Add(p_act >= active[m.task_id, s])
                    part_actives.append(p_act)
                if len(part_actives) > 3:
                    excess = model.NewIntVar(0, len(part_actives), f"excess.{line}.{s}")
                    model.Add(excess >= sum(part_actives) - 3)
                    concurrency_penalties.append(excess)

        if self.enforce_concurrency_limit and concurrency_penalties:
            terms.append(CONCURRENCY_WEIGHT * sum(concurrency_penalties))

        # R-SF-07 & R-FG-09: 周末停机偏好软约束惩罚
        weekend_penalties = []
        if self.enforce_weekend_penalty:
            for s_idx, shift in enumerate(self.shifts):
                try:
                    dt = datetime.strptime(shift.date_str, "%Y-%m-%d")
                    if dt.weekday() in (5, 6):  # Saturday (5) or Sunday (6)
                        for line in self.lines:
                            line_tasks = [t for t in self.tasks if t.line == line]
                            line_active = [active[t.task_id, s_idx] for t in line_tasks if (t.task_id, s_idx) in active]
                            if line_active:
                                w_run = model.NewBoolVar(f"w_run.{line}.{s_idx}")
                                for a_var in line_active:
                                    model.Add(w_run >= a_var)
                                weekend_penalties.append(w_run)
                except Exception:
                    pass
        if weekend_penalties:
            terms.append(15_000 * sum(weekend_penalties))

        if price_changeovers:
            terms.append(CHANGEOVER_WEIGHT * sum(runs))
        elif violations:
            terms.append(self._violation_weight() * sum(violations))
            terms.append(SHORTFALL_WEIGHT * sum(shortfall))
        if overloads:
            # 弹性上浮负荷（100%~105%）加软惩罚，引导求解器优先保持在 100% 以内，谨慎动用上浮产能
            terms.append(200 * sum(overloads))
        if lateness:
            terms.append(self._lateness_weight() * sum(lateness))
        model.Minimize(sum(terms))
        return model, x

    def _violation_weight(self) -> int:
        """One slot below 95% costs more than every deficit that could pay for it."""
        return self.num_shifts * MIN_SHIFT_MS + 1

    def _lateness_weight(self) -> int:
        """One late shift must outweigh every utilisation gain still on the table.

        The soft tiers can at most clear one sub-95% slot per line per shift, so
        pricing lateness above that ceiling keeps WE34's ranking intact whatever
        the solver finds.
        """
        return len(self.lines) * self.num_shifts * self._violation_weight() + 1

    def _add_material_balance(self, model, x) -> None:
        """Per-shift material balance: bushings must exist before assembly consumes them.

        Output of shift s-1 is available to shift s, so the two stages overlap lot by
        lot while every assembly unit is still backed by real stock.
        """
        cumulative: Dict[str, Dict[int, object]] = {}
        for task in self.tasks:
            lo, hi = self.windows[task.task_id]
            running = 0
            slots = {}
            for s in range(lo, hi + 1):
                running += x[task.task_id, s]
                var = model.NewIntVar(0, task.lots, f"cum.{task.task_id}.{s}")
                model.Add(var == running)
                slots[s] = var
            cumulative[task.task_id] = slots

        for fg_task in self.plan.fg_tasks:
            components = [t for t in self.plan.sf_tasks if t.parent_task_id == fg_task.task_id]
            if not components:
                continue
            fg_lo, fg_hi = self.windows[fg_task.task_id]
            for s in range(fg_lo, fg_hi + 1):
                assembled = cumulative[fg_task.task_id][s] * fg_task.lot_size
                for component in components:
                    comp_lo, comp_hi = self.windows[component.task_id]
                    ratio = int(round(component.bom_ratio))
                    if s - 1 < comp_lo:
                        model.Add(cumulative[fg_task.task_id][s] == 0)
                    else:
                        model.Add(
                            cumulative[component.task_id][min(s - 1, comp_hi)] * component.lot_size
                            + component.stock_covered
                            >= assembled * ratio
                        )

    def _add_line_capacity(self, model, x, utilization_mode: str, gap_cap=None):
        """Per line-shift load, the 8h ceiling, and the 95% floor.

        'hard'  : every worked slot but the closing one must reach 95%
        'soft'  : the same, with the short slots and their deficits collected for the
                  objective
        'off'   : ceiling only, used for the seeding model

        `gap_cap` is the (total, worst, count) utilisation profile of a plan we
        already hold: the deficits stay soft, but may not add up to more, open a
        deeper single hole, or land on more slots than that plan. The compaction
        pass needs the 95% outcome frozen so it has nothing left to trade.
        """
        shortfall = []
        violations = []
        overloads = []
        for line in self.lines:
            line_tasks = [t for t in self.tasks if t.line == line]
            max_line_ms = self._get_line_max_shift_ms(line)
            load = []
            running = []
            for s in range(self.num_shifts):
                terms = [x[t.task_id, s] * t.lot_millis for t in line_tasks if (t.task_id, s) in x]
                var = model.NewIntVar(0, max_line_ms, f"load.{line}.{s}")
                model.Add(var == (sum(terms) if terms else 0))
                run = model.NewBoolVar(f"run.{line}.{s}")
                model.Add(var >= 1).OnlyEnforceIf(run)
                model.Add(var == 0).OnlyEnforceIf(run.Not())
                load.append(var)
                running.append(run)

                if max_line_ms > SHIFT_MS:
                    over = model.NewIntVar(0, max_line_ms - SHIFT_MS, f"over.{line}.{s}")
                    model.Add(over >= var - SHIFT_MS)
                    overloads.append(over)

            work = sum(t.lots * t.lot_millis for t in line_tasks)
            # A slot holds at most max_line_ms of work, so this floor on the number of
            # worked shifts is implied by the capacity constraint above.
            model.Add(sum(running) >= -(-work // max_line_ms))

            if utilization_mode == "off":
                continue

            last_worked = model.NewIntVar(0, self.num_shifts - 1, f"last.{line}")
            for s in range(self.num_shifts):
                model.Add(last_worked >= s).OnlyEnforceIf(running[s])

            for s in range(self.num_shifts):
                tail = model.NewBoolVar(f"tail.{line}.{s}")
                model.Add(last_worked <= s).OnlyEnforceIf(tail)
                model.Add(last_worked >= s + 1).OnlyEnforceIf(tail.Not())
                if utilization_mode == "hard":
                    model.Add(load[s] + MIN_SHIFT_MS * tail >= MIN_SHIFT_MS * running[s])
                    continue
                gap = model.NewIntVar(0, MIN_SHIFT_MS, f"gap.{line}.{s}")
                model.Add(gap >= MIN_SHIFT_MS * running[s] - MIN_SHIFT_MS * tail - load[s])
                shortfall.append(gap)
                # `below` is exactly "this slot is short of 95%": the tail exemption and
                # an empty slot both force it off, so counting it measures what the
                # report lists rather than how many milliseconds are behind.
                below = model.NewBoolVar(f"below.{line}.{s}")
                model.Add(load[s] >= MIN_SHIFT_MS * (running[s] - tail)).OnlyEnforceIf(below.Not())
                model.Add(load[s] <= MIN_SHIFT_MS * (running[s] - tail) - 1).OnlyEnforceIf(below)
                if gap_cap is not None:
                    model.Add(gap <= gap_cap[1])
                violations.append(below)

        if gap_cap is not None:
            model.Add(sum(shortfall) <= gap_cap[0])
            model.Add(sum(violations) <= gap_cap[2])
        return shortfall, violations, overloads

    def _extract(self, solver, x) -> List[ScheduledTask]:
        clock = self._clock_times(solver, x)
        scheduled = []

        for task in self.tasks:
            breakdown: Dict[ShiftKey, int] = {}
            seconds: Dict[ShiftKey, float] = {}
            used = []
            for s, shift in enumerate(self.shifts):
                if (task.task_id, s) not in x:
                    continue
                lots = solver.Value(x[task.task_id, s])
                if lots <= 0:
                    continue
                key: ShiftKey = (shift.date_str, shift.shift_name)
                breakdown[key] = int(lots * task.lot_size)
                seconds[key] = lots * task.lot_millis / TIME_SCALE
                used.append(s)

            scheduled.append(ScheduledTask(
                task_id=task.task_id,
                stage=task.stage,
                line=task.line,
                part_no=task.part_no,
                description=task.description,
                ct_seconds=task.ct_seconds,
                trp=task.trp,
                mpq=task.mpq,
                lot_size=task.lot_size,
                order_id=task.order_id,
                quantity=task.quantity,
                lots=task.lots,
                start_dt=clock[(task.task_id, used[0])][0],
                end_dt=clock[(task.task_id, used[-1])][1],
                duration_hours=sum(seconds.values()) / 3600.0,
                due_datetime=task.due_datetime,
                shift_breakdown=breakdown,
                shift_seconds=seconds,
                mold_id=getattr(task, 'mold_id', None),
                material_arrival_shift=getattr(task, 'material_arrival_shift', 0),
                customer_tier=getattr(task, 'customer_tier', 'A'),
                line_pm_slots=getattr(task, 'line_pm_slots', []),
            ))
        return scheduled

    def _clock_times(self, solver, x) -> Dict[Tuple[str, int], Tuple[datetime, datetime]]:
        """Walk each line shift by shift so 制造开始时间 reads as a real clock time."""
        clock: Dict[Tuple[str, int], Tuple[datetime, datetime]] = {}
        for line in self.lines:
            line_tasks = [t for t in self.tasks if t.line == line]
            order = sorted(line_tasks, key=lambda t: (self._first_shift(solver, x, t), t.task_id))
            for s, shift in enumerate(self.shifts):
                cursor = 0.0
                for task in order:
                    if (task.task_id, s) not in x:
                        continue
                    lots = solver.Value(x[task.task_id, s])
                    if lots <= 0:
                        continue
                    span = lots * task.lot_millis / TIME_SCALE
                    clock[(task.task_id, s)] = (
                        shift.start_dt + timedelta(seconds=cursor),
                        shift.start_dt + timedelta(seconds=cursor + span),
                    )
                    cursor += span
        return clock

    def _first_shift(self, solver, x, task) -> int:
        lo, hi = self.windows[task.task_id]
        for s in range(lo, hi + 1):
            if solver.Value(x[task.task_id, s]) > 0:
                return s
        return self.num_shifts
