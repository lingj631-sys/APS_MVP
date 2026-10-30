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
import math
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ortools.sat.python import cp_model

from src.data_loader import TIME_SCALE, ProductionTask, RequirementPlan
from src.shift_manager import (
    CalendarMode,
    DEFAULT_CHANGEOVER_MINUTES,
    MIN_SHIFT_SECONDS,
    PlantTopology,
    SHIFT_SECONDS,
    ShiftBucket,
    generate_shift_calendar,
    get_topology,
    last_shift_on_or_before,
)

SHIFT_MS = SHIFT_SECONDS * TIME_SCALE
MIN_SHIFT_MS = MIN_SHIFT_SECONDS * TIME_SCALE

SHORTFALL_WEIGHT = 1
FG_COMPLETION_WEIGHT = 1
SF_COMPLETION_WEIGHT = 1
# A changeover is one model's run starting on a shift. Priced per run start, and
# now also charged against capacity in _add_line_capacity.
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
    crew_required: float = 0.0
    splittable: bool = False


@dataclass
class ScheduleResult:
    tasks: List[ScheduledTask]
    shifts: List[ShiftBucket]
    strict_utilization: bool
    status_name: str
    objective_value: float
    wall_time_seconds: float
    allow_105_fg: bool = False
    allow_105_sf: bool = False
    capacity_note: str = ""
    sequence_plan: Optional[List[Dict[str, Any]]] = None


class _SchedulerCompileContext:
    """Adapter from APSScheduler's local model variables to the kernel protocol."""

    def __init__(self, scheduler, model, x, active, started, end_of, opening, tails, loads, cumulative=None):
        self._scheduler = scheduler
        self.model = model
        self.num_shifts = scheduler.num_shifts
        self._x = x
        self._active = active
        self._started = started
        self._end_of = end_of
        self._opening = opening
        self._tails = tails
        self._loads = loads
        self._cumulative = cumulative or {}

    def lines(self):
        return self._scheduler.lines

    def tasks(self):
        return self._scheduler.tasks

    def window(self, task):
        return self._scheduler.windows[task.task_id]

    def x(self, task, slot):
        return self._x[task.task_id, slot]

    def active(self, task, slot):
        return self._active[task.task_id, slot]

    def started(self, task, slot):
        return self._started[task.task_id, slot]

    def end_of(self, task):
        return self._end_of[task.task_id]

    def load(self, line, slot):
        return self._loads[line, slot]

    def open(self, line, slot):
        return self._opening[line, slot]

    def tail(self, line, slot):
        return self._tails[line, slot]

    def capacity_ms(self, line):
        reserves = [
            float(value) for (reserved_line, _slot), value in
            getattr(self._scheduler, "setup_reserve", {}).items()
            if reserved_line == line
        ]
        reserve_ms = int(round(max(reserves, default=0.0) * 60 * TIME_SCALE))
        return max(1, self._scheduler.shift_ms - reserve_ms)

    def min_shift_ms(self, line):
        return self._scheduler.min_shift_ms

    def load_upper_bound(self, line):
        return self._scheduler._get_line_max_shift_ms(line)

    def due_slot(self, task):
        return self._scheduler._due_shift(task)

    def due_key(self, task):
        return task.due_datetime or datetime.max

    def mold_id(self, task):
        return getattr(task, "mold_id", None)

    def pm_slots(self, line):
        return self._scheduler.pm_shifts.get(line, [])

    def material_ready_slot(self, task):
        return getattr(task, "material_arrival_shift", 0)

    def is_splittable(self, task):
        return self._scheduler._is_splittable(task)

    def children(self, task):
        return [t for t in self._scheduler.plan.sf_tasks if t.parent_task_id == task.task_id]

    def bom_ratio(self, task):
        return getattr(task, "bom_ratio", 1)

    def stock_covered(self, task):
        return getattr(task, "stock_covered", 0.0)

    def cumulative(self, task, slot):
        return self._cumulative[task.task_id][slot]

    def unit_pieces(self, task):
        return self._scheduler._unit_pieces(task)

    def customer_tier(self, task):
        return getattr(task, "customer_tier", "A")

    def crew_required(self, task):
        value = float(getattr(task, "crew_required", 0) or 0)
        if value > 0:
            return value
        data = self._scheduler.labor_data
        if data is not None:
            return data.crew_required(
                self._scheduler.site, task.line, getattr(task, "part_no", "")
            )
        return None

    def labor_available(self, line, slot):
        data = self._scheduler.labor_data
        if data is None:
            return None
        shift = self._scheduler.shifts[slot]
        return data.available(self._scheduler.site, shift.date_str, shift.shift_name)

    def labor_pool(self, line):
        data = self._scheduler.labor_data
        if data is None:
            return f"__line__:{line}"
        return data.pool(self._scheduler.site, line)

    def shift_weekday(self, slot):
        return self._scheduler.shifts[slot].weekday

    def shift_name(self, slot):
        return self._scheduler.shifts[slot].shift_name

    def shift_date_str(self, slot):
        return self._scheduler.shifts[slot].date_str

    def baseline_units(self, task, slot):
        baseline = getattr(self._scheduler, "baseline_tasks", None) or []
        base = next((t for t in baseline if t.task_id == getattr(task, "task_id", None)), None)
        if base is None:
            return 0
        shift = self._scheduler.shifts[slot]
        key = (shift.date_str, shift.shift_name)
        qty = getattr(base, "shift_breakdown", {}).get(key, 0)
        return int(qty // max(1, self.unit_pieces(task)))

    def shifts_per_day(self):
        return int(getattr(self._scheduler.topology, "shifts_per_day", 3))

    def co_product_partner(self, task):
        part = str(getattr(task, "part_no", ""))
        if not (part.endswith("L") or part.endswith("R")):
            return None
        partner_part = part[:-1] + ("R" if part.endswith("L") else "L")
        for other in self._scheduler.tasks:
            if (
                other.task_id != task.task_id
                and other.part_no == partner_part
                and other.line == task.line
            ):
                return other
        return None


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
        enforce_tooling_exclusivity: bool = True,
        enforce_material_arrival: bool = True,
        enforce_pm_maintenance: bool = True,
        pm_shifts: Optional[Dict[str, List[int]]] = None,
        enforce_customer_priority: bool = True,
        enforce_weekend_penalty: bool = True,
        allow_105_utilization_fg: bool = False,
        allow_105_utilization_sf: bool = False,
        changeover_minutes: float = DEFAULT_CHANGEOVER_MINUTES,
        setup_reserve: Optional[Dict[Tuple[str, int], float]] = None,
        sequence_setup: bool = False,
        setup_technicians_available: int = 0,
        inventory_policies: Optional[Dict[Any, Any]] = None,
        inventory_hard: bool = False,
        operation_lead_times: Optional[Dict[Any, Any]] = None,
        campaign_families: Optional[Dict[Any, Any]] = None,
        enforce_campaign: bool = False,
        enforce_lead_time: bool = False,
        sequence_feedback: bool = False,
        setup_matrix: Optional[Dict[Any, Any]] = None,
        setup_matrix_path: Optional[str] = None,
        max_blocks_per_task: int = 0,
        max_blocks_per_task_sf: int = 0,
        rule_set: Optional[Any] = None,
        use_kernel_capacity: bool = False,
        kernel_templates: Optional[Iterable[str]] = None,
        labor_data: Optional[Any] = None,
        site: Optional[str] = None,
        baseline_tasks: Optional[List[Any]] = None,
        freeze_shifts: int = 3,
    ):
        self.plan = plan
        self.site = site or getattr(plan, "site", "WL")
        self.topology = get_topology(self.site)
        self.shift_hours = self.topology.shift_hours
        self.shift_seconds = self.topology.shift_seconds
        self.shift_ms = self.topology.shift_ms
        self.min_shift_ms = self.topology.min_shift_seconds * TIME_SCALE

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
        self.baseline_tasks = baseline_tasks
        self.freeze_shifts = max(0, int(freeze_shifts))
        self.enforce_tooling_exclusivity = enforce_tooling_exclusivity
        self.enforce_material_arrival = enforce_material_arrival
        self.enforce_pm_maintenance = enforce_pm_maintenance
        if pm_shifts is not None:
            self.pm_shifts = pm_shifts
        else:
            self.pm_shifts = defaultdict(list)
            for t in getattr(self.plan, "tasks", []):
                if t.line_pm_slots:
                    for s in t.line_pm_slots:
                        if s not in self.pm_shifts[t.line]:
                            self.pm_shifts[t.line].append(s)
            self.pm_shifts = dict(self.pm_shifts)
        self.enforce_customer_priority = enforce_customer_priority
        self.enforce_weekend_penalty = enforce_weekend_penalty
        self.allow_105_utilization_fg = allow_105_utilization_fg
        self.allow_105_utilization_sf = allow_105_utilization_sf
        self.changeover_minutes = changeover_minutes
        self.setup_ms = max(0, int(round(changeover_minutes * 60 * TIME_SCALE)))
        self.setup_reserve = dict(setup_reserve or {})
        self.sequence_feedback = bool(sequence_feedback)
        self.setup_matrix = dict(setup_matrix or {})
        self.setup_matrix_path = setup_matrix_path
        self.sequence_setup = bool(sequence_setup)
        self.setup_technicians_available = max(0, int(setup_technicians_available or 0))
        self.inventory_policies = dict(inventory_policies or {})
        self.inventory_hard = bool(inventory_hard)
        self.operation_lead_times = dict(operation_lead_times or {})
        self.campaign_families = dict(campaign_families or {})
        self.enforce_campaign = bool(enforce_campaign)
        self.enforce_lead_time = bool(enforce_lead_time)
        self._sequence_vars = {}
        if self.sequence_setup:
            try:
                from src.sequence import load_setup_matrix
                if not self.setup_matrix:
                    path = self.setup_matrix_path or os.path.join(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "master", "Setup-Matrix.csv",
                    )
                    self.setup_matrix = load_setup_matrix(path)
                if not self.setup_matrix:
                    print("  [sequence] setup matrix empty; sequence_setup disabled")
                    self.sequence_setup = False
            except Exception as exc:
                print(f"  [sequence] setup matrix load failed: {exc}")
                self.sequence_setup = False
        self.max_blocks_per_task = max(0, int(max_blocks_per_task))
        self.max_blocks_per_task_sf = max(0, int(max_blocks_per_task_sf))
        self.rule_set = rule_set
        templates = set(kernel_templates or [])
        if use_kernel_capacity:
            templates.add("capacity_utilization")
        self.kernel_templates = templates
        self.use_kernel_capacity = rule_set is not None and "capacity_utilization" in templates
        self.sequence_setup_hard = True
        if self.rule_set is not None and self.use_kernel_capacity:
            # The rule set is the source of truth for the load ceiling. Keeping the
            # legacy 105% flag here would widen the load domains/hint while the
            # compiled rule still caps at 100%, which makes the hint invalid and
            # pushes the hard tier into the soft fallback.
            capacity_rules = [
                rule for rule in self.rule_set.rules
                if rule.enabled and rule.template_id == "capacity_utilization"
            ]
            if capacity_rules:
                self.sequence_setup_hard = all(
                    str(getattr(rule.nature, "value", rule.nature)) == "hard"
                    for rule in capacity_rules
                )

                def _allow_for(stage: str) -> bool:
                    values = [
                        float(rule.params.get("max_util", 1.0))
                        for rule in capacity_rules
                        if rule.scope.stage in ("*", stage)
                    ]
                    return bool(values) and max(values) > 1.0 + 1e-9

                fg_allow = _allow_for("FG")
                sf_allow = _allow_for("SF")
                if fg_allow != self.allow_105_utilization_fg or sf_allow != self.allow_105_utilization_sf:
                    print(
                        f"  [rules] capacity 105%: FG={'ON' if fg_allow else 'OFF'}, "
                        f"SF={'ON' if sf_allow else 'OFF'} (rule set wins over legacy option)"
                    )
                self.allow_105_utilization_fg = fg_allow
                self.allow_105_utilization_sf = sf_allow
        self.labor_data = labor_data
        self._hint_infeasible = False
        self.capacity_note = ""

        self.shifts = generate_shift_calendar(
            start_datetime,
            planning_days,
            calendar_mode=calendar_mode,
            day_shift_overrides=day_shift_overrides,
            site=self.site,
        )
        self.num_shifts = len(self.shifts)
        self.tasks = plan.tasks
        self.task_by_id = {t.task_id: t for t in self.tasks}
        self.lines = sorted({t.line for t in self.tasks})
        self.windows = self._build_windows()
        # ACT-06: a single lot that cannot fit in one shift is scheduled in pieces.
        # The order total is still >= MPQ (netting guarantees it); only the split
        # parts are exempt from the per-shift MPQ floor.
        self._splittable = {
            task.task_id
            for task in self.tasks
            if task.lot_size > 0 and task.lot_millis > self.shift_ms
        }

    def _is_splittable(self, task) -> bool:
        return task.task_id in self._splittable

    def _unit_pieces(self, task) -> int:
        return 1 if self._is_splittable(task) else task.lot_size

    def _unit_millis(self, task) -> int:
        if self._is_splittable(task):
            return max(1, int(round(task.lot_millis / task.lot_size)))
        return task.lot_millis

    def _units_total(self, task) -> int:
        if self._is_splittable(task):
            return max(1, int(round(task.quantity)))
        return task.lots

    def _per_shift_units(self, task) -> int:
        capacity = self._get_line_max_shift_ms(task.line)
        return max(1, min(self._units_total(task), capacity // self._unit_millis(task)))

    def _get_line_max_shift_ms(self, line: str) -> int:
        is_fg = any(t.stage == "FG" for t in self.tasks if t.line == line)
        allow_105 = self.allow_105_utilization_fg if is_fg else self.allow_105_utilization_sf
        return int(round(self.shift_ms * 1.05)) if allow_105 else self.shift_ms

    def _tiers(self):
        if not self.enforce_utilization_95:
            return (
                ("关闭 95% 利用率约束 (" + ("硬约束交付" if self.enforce_on_time_delivery else "交付损失最小化") + ")", "off", self.enforce_on_time_delivery, 1.0),
            )
        # Try the fully compliant packing first: when it exists it is both the best
        # answer and the easiest to explain. The soft tiers are the fallback for
        # datasets where MPQ granularity and the 95% band genuinely do not add up.
        tiers = [
            (
                "硬约束 95% 利用率 + " + ("硬约束准时交付" if self.enforce_on_time_delivery else "交付损失最小化"),
                "hard",
                self.enforce_on_time_delivery,
                0.70,
            )
        ]
        if self.enforce_on_time_delivery:
            tiers.append(("软约束 95% 利用率 + 硬约束准时交付", "soft", True, 0.25))
        tiers.append(("软约束 95% 利用率 + 交付损失最小化", "soft", False, 1.0))
        return tuple(tiers)

    def _run_tiers(self, tiers, deadline: float, hint) -> Optional[ScheduleResult]:
        for index, (label, mode, hard_due, share) in enumerate(tiers):
            if index:
                print(f"  [warn]  上一口径无解，降级为: {label}")
            remaining = max(1.0, deadline - time.perf_counter())
            result = self._solve_tier(mode, hard_due, remaining * share, hint)
            if result is not None:
                print(f"  [solver] 排产口径: {label}")
                left = deadline - time.perf_counter()
                min_left = 15.0 if mode == "hard" else 5.0
                if self.enforce_utilization_95 and left > min_left:
                    result = self._compact(result, hard_due, left)
                return result
        return None

    def solve(self, time_limit_seconds: float = 90.0) -> ScheduleResult:
        # WE34 ranks the constraints: on-time delivery first, 95% shift utilisation
        # second, whole models per line third.
        start = time.perf_counter()
        deadline = start + time_limit_seconds
        initial_deadline = start + time_limit_seconds * (0.70 if self.sequence_feedback else 1.0)
        # A due-date-feasible packing is hard for CP-SAT to *find* and cheap to
        # improve, so one is solved for up front and used to seed every tier;
        # without it the budget goes on search rather than on filling.
        hint = self._build_hint(min(10.0, time_limit_seconds * 0.08))

        # The 95%~100% vs 95%~105% choice belongs to the plant (rule option), so the
        # engine never escalates the ceiling on its own: it either finds a plan inside
        # the selected band or reports infeasibility.
        result = self._run_tiers(self._tiers(), initial_deadline, hint)
        if result is None:
            raise RuntimeError("Solver found no feasible schedule in any tier.")

        if self.sequence_feedback and time.perf_counter() < deadline - 10.0:
            reserves = self._sequence_reserves(result)
            if reserves:
                previous = self.setup_reserve
                self.setup_reserve = reserves
                print(f"  [sequence] setup reserve feedback: {len(reserves)} line-shifts")
                initial_gaps = self._gap_metrics(result)
                initial_blocks, initial_late = self._plan_metrics(result)
                try:
                    feedback = self._run_tiers(self._tiers(), deadline, hint)
                    if feedback is not None:
                        feedback_gaps = self._gap_metrics(feedback)
                        feedback_blocks, feedback_late = self._plan_metrics(feedback)
                        if (
                            feedback_gaps[2] <= initial_gaps[2]
                            and feedback_gaps[0] <= initial_gaps[0] + 60_000
                            and feedback_late <= initial_late
                        ):
                            result = feedback
                            print(
                                f"  [sequence] feedback accepted: gaps {feedback_gaps[2]}, "
                                f"blocks {feedback_blocks}, late {feedback_late}"
                            )
                        else:
                            print(
                                f"  [sequence] feedback rejected: gaps {feedback_gaps[2]} "
                                f"vs {initial_gaps[2]}, late {feedback_late} vs {initial_late}"
                            )
                except Exception as exc:  # keep the first feasible result
                    print(f"  [sequence] feedback skipped: {exc}")
                finally:
                    self.setup_reserve = previous
        return result

    def _sequence_reserves(self, result: ScheduleResult) -> Dict[Tuple[str, int], float]:
        """C1-b feedback: turn C1-a setup minutes into per-shift capacity reserves."""
        try:
            from src.sequence import load_setup_matrix, segments_from_schedule, sequence_segments
            matrix = self.setup_matrix
            if not matrix:
                path = self.setup_matrix_path or os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "master", "Setup-Matrix.csv",
                )
                matrix = load_setup_matrix(path)
            if not matrix:
                return {}
            index_of = {(s.date_str, s.shift_name): s.index for s in self.shifts}
            key_index = {f"{date_str} {shift_name}": idx for (date_str, shift_name), idx in index_of.items()}
            by_line = segments_from_schedule(result, index_of)
            reserves: Dict[Tuple[str, int], float] = {}
            for line, by_shift in by_line.items():
                capacity = {
                    key: float(self.shifts[idx].duration_hours * 60.0)
                    for key, idx in key_index.items()
                }
                plan = sequence_segments(
                    by_shift, matrix, site=self.site, line=line,
                    capacity_minutes_by_shift=capacity,
                )
                for key, minutes in plan.setup_minutes_by_shift.items():
                    if minutes > 0 and key in key_index:
                        reserves[(line, key_index[key])] = minutes
            return reserves
        except Exception as exc:
            print(f"  [sequence] reserve calculation skipped: {exc}")
            return {}

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
            model, x, _aux = self._build_model(mode, hard_due, gap_cap=cap)
            plan, status_name, _ = self._run(
                model, x, mode, left * share, self._lot_values(result),
                group_terms=_aux.get("group_terms"),
            )
            if plan is None:
                print(f"  [solver] 换型集中: {label} 未求得（{status_name}）")
                continue
            grouped, on_time = self._plan_metrics(plan)
            if on_time > late:
                print(f"  [solver] 换型集中: {label} 会多延误 {on_time - late} 个成品订单")
                continue
            if grouped >= blocks:
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
                gap = max(0, self.min_shift_ms - int(round(seconds * TIME_SCALE)))
                total += gap
                worst = max(worst, gap)
                count += gap > 0
        return total, worst, count

    def _lot_values(self, result: ScheduleResult) -> Dict[Tuple[str, int], int]:
        """Flip an extracted plan back into per-(task, shift) lot counts for hinting."""
        index_of = {(s.date_str, s.shift_name): s.index for s in self.shifts}
        return {
            (task.task_id, index_of[key]): qty // self._unit_pieces(task)
            for task in result.tasks
            for key, qty in task.shift_breakdown.items()
        }

    def _solve_tier(self, mode: str, hard_due: bool, budget: float, hint):
        label = "硬约束" if mode == "hard" else "软约束"
        model, x, aux = self._build_model(mode, hard_due)
        # 'soft' is the tier that has to *find* a violation-free packing rather
        # than prove one optimal, so it stops the moment it holds one.
        stopper = None if mode == "hard" else _SolutionStopper(self._clean_filling(aux))
        result, status_name, wall = self._run(
            model, x, mode, budget, hint, stopper, group_terms=aux.get("group_terms")
        )
        if result is None:
            print(f"  [solver] {label}: {status_name}")
            return None

        gaps = self._gap_metrics(result)
        print(f"\n--- Solver Finished (利用率约束: {label}) ---")
        print(f"Status: {result.status_name} | Objective: {result.objective_value:.0f} | Wall Time: {wall:.2f}s")
        print(f"  [solver] 利用率缺口: {gaps[2]} 个班次，合计 {gaps[0] / 1000:.0f}s")
        return result

    def _clean_filling(self, aux):
        """Test used to cut the filling tier short: every opened slot except the
        line's closing shift clears the 95% floor, with setup time included."""

        def is_clean(callback) -> bool:
            for (line, slot), opened in aux["open"].items():
                if not callback.Value(opened):
                    continue
                if callback.Value(aux["tail"][line, slot]):
                    continue
                if callback.Value(aux["load"][line, slot]) < self.min_shift_ms:
                    return False
            return True

        return is_clean

    def _build_hint(self, time_limit_seconds: float) -> Optional[Dict[Tuple[str, int], int]]:
        """Capacity, kitting and due dates only: the quickest way to a valid plan."""
        # The hint is a relaxation: build it with the legacy path even when the main
        # model uses the kernel, so the seed is available in seconds rather than
        # being lost to a different constraint ordering.
        model, x, _aux = self._build_model("off", hard_due=True, use_kernel=False)
        # Any due-feasible packing is a usable seed, so ask for feasibility rather
        # than optimality; the harder tiers do the improving from here.
        model.Minimize(0)
        solver = self._solver(time_limit_seconds)
        status = solver.Solve(model)
        self._hint_infeasible = (status == cp_model.INFEASIBLE)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            print(f"  [solver] 初始可行提示: {solver.StatusName(status)}")
            return None
        print(f"  [solver] 初始可行提示: {solver.StatusName(status)} ({solver.WallTime():.1f}s)")
        return {key: solver.Value(var) for key, var in x.items()}

    def _solver(self, time_limit_seconds: float) -> cp_model.CpSolver:
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max(1.0, time_limit_seconds)
        # One worker + a fixed seed makes a plan reproducible; a planning system
        # cannot answer "why did yesterday work and today not" any other way.
        solver.parameters.num_workers = 1
        solver.parameters.random_seed = 1
        solver.parameters.log_search_progress = False
        return solver

    def _run(self, model, x, mode: str, time_limit_seconds: float, hint, stopper=None, group_terms=None):
        if (
            self.rule_set is not None
            and group_terms
            and mode == "soft"
            and len([gid for gid, terms in group_terms.items() if terms]) > 1
        ):
            return self._run_lexicographic(model, x, mode, time_limit_seconds, hint, group_terms)

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
            allow_105_fg=self.allow_105_utilization_fg,
            allow_105_sf=self.allow_105_utilization_sf,
            capacity_note=self.capacity_note,
            sequence_plan=self._extract_sequence(solver) if self.sequence_setup else None,
        )
        return result, result.status_name, solver.WallTime()

    def _run_lexicographic(self, model, x, mode, time_limit_seconds, hint, group_terms):
        """Solve soft groups in priority order, freezing each group's best value.

        This is the production wiring of the kernel's group priority: delivery is
        frozen before capacity, capacity before changeover/inventory. It only runs
        in the soft tiers; the hard tier already enforces its groups as constraints.
        """
        for key, value in (hint or {}).items():
            model.AddHint(x[key], value)

        order = [group.group_id for group in self.rule_set.ordered_groups()]
        order += [group_id for group_id in group_terms if group_id not in order]
        groups = [group_id for group_id in order if group_terms.get(group_id)]
        per_group = max(1.0, time_limit_seconds / max(1, len(groups)))
        bests: Dict[str, float] = {}
        total_wall = 0.0
        last_solver = None
        last_status = "UNKNOWN"

        for group_id in groups:
            expr = sum(group_terms[group_id])
            model.Minimize(expr)
            solver = self._solver(per_group)
            status = solver.Solve(model)
            total_wall += solver.WallTime()
            last_status = solver.StatusName(status)
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                return None, last_status, total_wall
            best = float(solver.ObjectiveValue())
            bests[group_id] = best
            model.Add(expr <= int(math.ceil(best)))
            last_solver = solver

        if last_solver is None:
            return None, last_status, total_wall

        result = ScheduleResult(
            tasks=self._extract(last_solver, x),
            shifts=self.shifts,
            strict_utilization=False,
            status_name=last_status,
            objective_value=sum(bests.values()),
            wall_time_seconds=total_wall,
            allow_105_fg=self.allow_105_utilization_fg,
            allow_105_sf=self.allow_105_utilization_sf,
            capacity_note=self.capacity_note,
            sequence_plan=self._extract_sequence(last_solver) if self.sequence_setup else None,
        )
        return result, result.status_name, total_wall

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

    def _build_model(self, utilization_mode: str, hard_due: bool, gap_cap=None, use_kernel: bool = True):
        model = cp_model.CpModel()
        x: Dict[Tuple[str, int], object] = {}
        active: Dict[Tuple[str, int], object] = {}
        started: Dict[Tuple[str, int], object] = {}
        end_of: Dict[str, object] = {}
        # A changeover is paid per production run, not per shift. Run starts are
        # always materialised because they also consume capacity (setup time) and
        # because they are the only correct denominator for "how many changeovers".
        price_changeovers = self.price_changeovers and (utilization_mode == "hard" or gap_cap is not None)
        runs: List[object] = []
        group_terms: Dict[str, List[Any]] = defaultdict(list)
        kernel_min_batch = self.enforce_mpq_batch and use_kernel and self._use_kernel("min_batch")

        for task in self.tasks:
            lo, hi = self.windows[task.task_id]
            # One lot sets the floor of what a shift can hold; no task can
            # contribute more lots than that. Capping the domain here is
            # what lets CP-SAT propagate capacity instead of discovering it late.
            max_line_ms = self._get_line_max_shift_ms(task.line)
            per_shift = self._per_shift_units(task)
            units_total = self._units_total(task)
            splittable = self._is_splittable(task)
            # ...and a shift that opens a task has to run its minimum batch: a whole
            # MPQ for semi-finished pieces, one MPQ batch for finished goods. Without
            # this the filling objective drips a model over dozens of shifts as 1-2
            # pieces at a time, which no line can actually set up for.
            # ACT-06 exception: a split part of an oversized lot is a continuation,
            # not a new batch, so it is exempt from the per-shift MPQ floor.
            min_lots = 1 if splittable else (
                min(per_shift, task.mpq // task.lot_size) if self.enforce_mpq_batch else 1
            )
            for s in range(lo, hi + 1):
                units = model.NewIntVar(0, per_shift, f"x.{task.task_id}.{s}")
                used = model.NewBoolVar(f"a.{task.task_id}.{s}")
                if not kernel_min_batch:
                    model.Add(units >= min_lots * used)
                model.Add(units <= per_shift * used)
                x[task.task_id, s] = units
                active[task.task_id, s] = used
            model.Add(sum(x[task.task_id, s] for s in range(lo, hi + 1)) == units_total)

            # Exact run-start indicator: 1 iff the task begins on shift s. Paid as a
            # changeover and charged as setup time on that shift.
            started[task.task_id, lo] = active[task.task_id, lo]
            runs.append(active[task.task_id, lo])
            for s in range(lo + 1, hi + 1):
                st = model.NewBoolVar(f"start.{task.task_id}.{s}")
                model.Add(st >= active[task.task_id, s] - active[task.task_id, s - 1])
                model.Add(st <= active[task.task_id, s])
                model.Add(st <= 1 - active[task.task_id, s - 1])
                started[task.task_id, s] = st
                runs.append(st)

            if splittable:
                # ACT-06: the split parts must form one continuous run (no gap and
                # no second start), and use only the minimum number of shifts needed.
                model.Add(sum(started[task.task_id, s] for s in range(lo, hi + 1)) == 1)
                max_shifts = -(-units_total // per_shift)
                model.Add(
                    sum(active[task.task_id, s] for s in range(lo, hi + 1)) <= max_shifts
                )

            # R-WL-09 continuity is a preference, not a hard rule: each extra run
            # start is already priced as a changeover, so the model consolidates
            # unless 95% filling makes a block impossible. A plant can opt into a
            # hard block cap per stage with max_blocks_per_task(_sf) > 0.
            block_cap = self.max_blocks_per_task if task.stage == "FG" else self.max_blocks_per_task_sf
            if block_cap > 0:
                model.Add(
                    active[task.task_id, lo]
                    + sum(started[task.task_id, s] for s in range(lo + 1, hi + 1))
                    <= block_cap
                )

            # Completion is exactly the last shift the task is active on. The old
            # `end >= s` form left `end` a free lower bound, which silently voided
            # the FIFO ordering below and under-counted lateness.
            end = model.NewIntVar(lo, hi, f"end.{task.task_id}")
            model.AddMaxEquality(end, [s * active[task.task_id, s] for s in range(lo, hi + 1)])
            end_of[task.task_id] = end

        # R-WL-10: Freeze window baseline equality constraints (REG-05)
        if self.enforce_freeze_window and self.baseline_tasks:
            baseline_map = {t.task_id: t for t in self.baseline_tasks}
            for task in self.tasks:
                lo, hi = self.windows[task.task_id]
                base_t = baseline_map.get(task.task_id)
                for s in range(lo, min(hi + 1, self.freeze_shifts)):
                    if (task.task_id, s) not in x:
                        continue
                    if base_t is not None:
                        shift = self.shifts[s]
                        key = (shift.date_str, shift.shift_name)
                        base_qty = getattr(base_t, "shift_breakdown", {}).get(key, 0)
                        base_units = int(base_qty // self._unit_pieces(task))
                        model.Add(x[task.task_id, s] == base_units)
                    else:
                        model.Add(x[task.task_id, s] == 0)

        # min_batch is compiled before the line resources, in the same position as
        # the legacy constraint: CP-SAT's search is sensitive to constraint order.
        if kernel_min_batch:
            self._compile_rules_for(
                "min_batch", model, x, active, started, end_of, {}, {}, {}, force_hard=True
            )
        if use_kernel and self._use_kernel("labor_capacity"):
            self._compile_rules_for(
                "labor_capacity", model, x, active, started, end_of, {}, {}, {}, force_hard=False
            )
        kernel_material = use_kernel and self.enforce_material_balance and self._use_kernel("material_balance")
        cumulative = None
        if self.enforce_material_balance or self.inventory_policies:
            cumulative = self._create_cumulative(model, x)
            if not kernel_material:
                self._legacy_material_balance(model, cumulative)
        if kernel_material:
            self._compile_rules_for(
                "material_balance", model, x, active, started, end_of, {}, {}, {}, cumulative=cumulative
            )
        inventory_penalty = self._add_inventory_constraints(model, cumulative) if self.inventory_policies else None
        lead_time_penalty = self._add_lead_time_penalty(model, end_of) if self.enforce_lead_time else None
        shortfall, violations, overloads, opening, tails, loads, kernel_objective = self._add_line_capacity(
            model, x, active, started, end_of, utilization_mode, gap_cap, use_kernel=use_kernel
        )
        sequence_setup_penalty = None
        if self.sequence_setup:
            sequence_setup_penalty = self._add_sequence_constraints(model, x, active, loads)
        kernel_due = hard_due and use_kernel and self._use_kernel("due_date")

        # R-SF-02 & R-FG-05: 顺位紧邻前拉硬约束（同产线同型号按交期先后严格顺位排产，禁止跳序挑单）
        kernel_sequence = use_kernel and self.enforce_sequential_pulling and self._use_kernel("sequence")
        if self.enforce_sequential_pulling and not kernel_sequence:
            by_line_part = defaultdict(list)
            for t in self.tasks:
                by_line_part[(t.line, t.part_no)].append(t)
            for group in by_line_part.values():
                if len(group) > 1:
                    by_due = defaultdict(list)
                    for t in group:
                        by_due[t.due_datetime or datetime.max].append(t)
                    sorted_dues = sorted(by_due.keys())
                    for i in range(len(sorted_dues) - 1):
                        d_early, d_late = sorted_dues[i], sorted_dues[i + 1]
                        for ta in by_due[d_early]:
                            for tb in by_due[d_late]:
                                model.Add(end_of[ta.task_id] <= end_of[tb.task_id])
        if kernel_sequence:
            self._compile_rules_for(
                "sequence", model, x, active, started, end_of, opening, tails, loads, force_hard=True
            )

        # R-TL-01: 工装模具物理独占硬约束（同班次跨产线互斥，禁止并发占用同一模具）
        kernel_tooling = use_kernel and self.enforce_tooling_exclusivity and self._use_kernel("tooling_exclusivity")
        if self.enforce_tooling_exclusivity and not kernel_tooling:
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
        if kernel_tooling:
            self._compile_rules_for(
                "tooling_exclusivity", model, x, active, started, end_of, opening, tails, loads, force_hard=True
            )

        # R-MT-01: 关键物料预计到货齐套开工硬约束（到料前禁止开工）
        kernel_ready = use_kernel and self.enforce_material_arrival and self._use_kernel("material_ready")
        if self.enforce_material_arrival and not kernel_ready:
            for t in self.tasks:
                arr_shift = getattr(t, 'material_arrival_shift', 0)
                if arr_shift > 0:
                    lo, hi = self.windows[t.task_id]
                    for s in range(lo, min(arr_shift, hi + 1)):
                        if (t.task_id, s) in x:
                            model.Add(x[t.task_id, s] == 0)
        if kernel_ready:
            self._compile_rules_for(
                "material_ready", model, x, active, started, end_of, opening, tails, loads, force_hard=True
            )

        # R-PM-01: 设备计划性预防维护（PM）避让硬约束（PM时段产能清零，任务避让）
        kernel_pm = use_kernel and self.enforce_pm_maintenance and self._use_kernel("pm_block")
        if self.enforce_pm_maintenance and self.pm_shifts and not kernel_pm:
            for line, pm_slots in self.pm_shifts.items():
                line_tasks = [t for t in self.tasks if t.line == line]
                for s in pm_slots:
                    if 0 <= s < self.num_shifts:
                        for t in line_tasks:
                            if (t.task_id, s) in x:
                                model.Add(x[t.task_id, s] == 0)
        if kernel_pm:
            self._compile_rules_for(
                "pm_block", model, x, active, started, end_of, opening, tails, loads, force_hard=True
            )

        # due is compiled after the physical constraints, where the legacy hard-due
        # constraint lived, so the search order stays comparable.
        if kernel_due:
            self._compile_rules_for(
                "due_date", model, x, active, started, end_of, opening, tails, loads, force_hard=True
            )

        kernel_changeover = price_changeovers and use_kernel and self._use_kernel("changeover")
        changeover_objective = None
        if kernel_changeover:
            compiled_changeover = self._compile_rules_for(
                "changeover", model, x, active, started, end_of, opening, tails, loads, force_hard=False
            )
            if compiled_changeover.book.terms:
                changeover_objective = compiled_changeover.total_objective(scale=CHANGEOVER_WEIGHT)

        kernel_customer = (
            not hard_due
            and use_kernel
            and self.enforce_customer_priority
            and self._use_kernel("customer_priority")
        )
        customer_objective = None
        if kernel_customer:
            compiled_customer = self._compile_rules_for(
                "customer_priority", model, x, active, started, end_of, opening, tails, loads,
                force_hard=False,
            )
            if compiled_customer.book.terms:
                customer_objective = compiled_customer.total_objective(scale=1)

        lateness = []
        for task in self.plan.fg_tasks:
            if kernel_customer:
                break
            due = self._due_shift(task)
            if hard_due:
                if not kernel_due:
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

        group_terms["G1_DELIVERY"].append(
            FG_COMPLETION_WEIGHT * sum(end_of[t.task_id] for t in self.plan.fg_tasks)
        )
        group_terms["G1_DELIVERY"].append(
            SF_COMPLETION_WEIGHT * sum(end_of[t.task_id] for t in self.plan.sf_tasks)
        )

        # R-FG-13: Penalize parallel models exceeding 3 per shift
        kernel_concurrency = use_kernel and self._use_kernel("concurrency")
        concurrency_objective = None
        if kernel_concurrency:
            compiled_concurrency = self._compile_rules_for(
                "concurrency", model, x, active, started, end_of, opening, tails, loads,
                force_hard=self.enforce_concurrency_limit,
            )
            if compiled_concurrency.book.terms:
                concurrency_objective = compiled_concurrency.total_objective(scale=CONCURRENCY_WEIGHT)
        else:
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
                        if self.enforce_concurrency_limit:
                            model.Add(sum(part_actives) <= 3)
                        excess = model.NewIntVar(0, len(part_actives), f"excess.{line}.{s}")
                        model.Add(excess >= sum(part_actives) - 3)
                        concurrency_penalties.append(excess)

            if self.enforce_concurrency_limit and concurrency_penalties:
                group_terms["G3_CHANGEOVER"].append(CONCURRENCY_WEIGHT * sum(concurrency_penalties))
        if concurrency_objective is not None:
            group_terms["G3_CHANGEOVER"].append(concurrency_objective)
        if self.enforce_campaign and self.campaign_families:
            campaign_penalty = self._add_campaign_penalty(model, active)
            if campaign_penalty is not None:
                group_terms["G3_CHANGEOVER"].append(campaign_penalty)

        # R-SF-07 & R-FG-09: 周末停机偏好软约束惩罚
        weekend_penalties = []
        if self.enforce_weekend_penalty:
            for s_idx, shift in enumerate(self.shifts):
                if shift.weekday in (5, 6):  # Saturday (5) or Sunday (6)
                    for line in self.lines:
                        line_tasks = [t for t in self.tasks if t.line == line]
                        line_active = [active[t.task_id, s_idx] for t in line_tasks if (t.task_id, s_idx) in active]
                        if line_active:
                            w_run = model.NewBoolVar(f"w_run.{line}.{s_idx}")
                            for a_var in line_active:
                                model.Add(w_run >= a_var)
                            weekend_penalties.append(w_run)
        if weekend_penalties:
            group_terms["G3_CHANGEOVER"].append(15_000 * sum(weekend_penalties))

        if price_changeovers:
            if changeover_objective is None:
                group_terms["G3_CHANGEOVER"].append(CHANGEOVER_WEIGHT * sum(runs))
            else:
                group_terms["G3_CHANGEOVER"].append(changeover_objective)
        elif violations:
            group_terms["G2_CAPACITY"].append(self._violation_weight() * sum(violations))
            group_terms["G2_CAPACITY"].append(SHORTFALL_WEIGHT * sum(shortfall))
        if overloads:
            # 弹性上浮负荷（100%~105%）加软惩罚，引导求解器优先保持在 100% 以内，谨慎动用上浮产能
            group_terms["G2_CAPACITY"].append(200 * sum(overloads))
        if lateness:
            group_terms["G1_DELIVERY"].append(self._lateness_weight() * sum(lateness))
        if customer_objective is not None:
            group_terms["G1_DELIVERY"].append(self._lateness_weight() * customer_objective)
        if kernel_objective is not None:
            group_terms["G2_CAPACITY"].append(kernel_objective)
        if sequence_setup_penalty is not None:
            group_terms["G2_CAPACITY"].append(sequence_setup_penalty)
        if inventory_penalty is not None:
            group_terms["G4_INVENTORY"].append(inventory_penalty)
        if lead_time_penalty is not None:
            group_terms["G1_DELIVERY"].append(lead_time_penalty)
        all_terms = [term for group in group_terms.values() for term in group]
        model.Minimize(sum(all_terms))
        return model, x, {"open": opening, "tail": tails, "load": loads, "group_terms": group_terms}

    def _violation_weight(self) -> int:
        """One slot below 95% costs more than every deficit that could pay for it."""
        return self.num_shifts * self.min_shift_ms + 1

    def _lateness_weight(self) -> int:
        """One late shift must outweigh every utilisation gain still on the table.

        The soft tiers can at most clear one sub-95% slot per line per shift, so
        pricing lateness above that ceiling keeps WE34's ranking intact whatever
        the solver finds.
        """
        return len(self.lines) * self.num_shifts * self._violation_weight() + 1

    def _create_cumulative(self, model, x) -> Dict[str, Dict[int, object]]:
        """Cumulative production by task and shift, shared by both balance paths."""
        cumulative: Dict[str, Dict[int, object]] = {}
        for task in self.tasks:
            lo, hi = self.windows[task.task_id]
            running = 0
            slots = {}
            for s in range(lo, hi + 1):
                running += x[task.task_id, s]
                var = model.NewIntVar(0, self._units_total(task), f"cum.{task.task_id}.{s}")
                model.Add(var == running)
                slots[s] = var
            cumulative[task.task_id] = slots
        return cumulative

    def _legacy_material_balance(self, model, cumulative) -> None:
        """Per-shift time-phased material ledger (REG-01 / ALG-01):
        For each semi-finished part_no, cumulative supply released by shift s-1 (plus initial stock)
        must cover cumulative consumption across all finished goods assembly tasks up to shift s.
        """
        bom = getattr(self.plan, "bom", {})
        initial_stock = getattr(self.plan, "initial_stock", {})

        # If BOM is not available in plan, fall back to pairwise parent_task_id matching
        if not bom:
            for fg_task in self.plan.fg_tasks:
                components = [t for t in self.plan.sf_tasks if t.parent_task_id == fg_task.task_id]
                if not components:
                    continue
                fg_lo, fg_hi = self.windows[fg_task.task_id]
                for s in range(fg_lo, fg_hi + 1):
                    assembled = cumulative[fg_task.task_id][s] * self._unit_pieces(fg_task)
                    for component in components:
                        comp_lo, comp_hi = self.windows[component.task_id]
                        frac = Fraction(str(component.bom_ratio)).limit_denominator(1000)
                        num = frac.numerator
                        denom = frac.denominator
                        if s - 1 < comp_lo:
                            avail = int(round(component.stock_covered))
                        else:
                            avail = (
                                cumulative[component.task_id][min(s - 1, comp_hi)] * self._unit_pieces(component)
                                + int(round(component.stock_covered))
                            )
                        model.Add(denom * avail >= num * assembled)
            return

        # True Time-phased Material Balance Ledger (ALG-01)
        sf_tasks_by_part: Dict[str, List[ProductionTask]] = defaultdict(list)
        for t in self.plan.sf_tasks:
            sf_tasks_by_part[t.part_no].append(t)

        fg_consumers_by_part: Dict[str, List[Tuple[ProductionTask, float]]] = defaultdict(list)
        for fg_task in self.plan.fg_tasks:
            for sf_pn, ratio in bom.get(fg_task.part_no, []):
                fg_consumers_by_part[sf_pn].append((fg_task, float(ratio)))

        components = set(sf_tasks_by_part.keys()) | set(fg_consumers_by_part.keys())

        for sf_pn in sorted(components):
            producers = sf_tasks_by_part.get(sf_pn, [])
            consumers = fg_consumers_by_part.get(sf_pn, [])
            if not consumers:
                continue

            opening_stock = int(round(float(initial_stock.get(sf_pn, 0.0))))

            all_slots = []
            for p_task in producers:
                lo, hi = self.windows[p_task.task_id]
                all_slots.extend([lo, hi])
            for c_task, _ in consumers:
                lo, hi = self.windows[c_task.task_id]
                all_slots.extend([lo, hi])

            if not all_slots:
                continue
            min_s, max_s = min(all_slots), max(all_slots)

            for s in range(min_s, max_s + 1):
                supply_terms = [opening_stock]
                for p_task in producers:
                    p_lo, p_hi = self.windows[p_task.task_id]
                    if s - 1 >= p_lo:
                        cum_slot = min(s - 1, p_hi)
                        unit_pcs = self._unit_pieces(p_task)
                        supply_terms.append(cumulative[p_task.task_id][cum_slot] * unit_pcs)

                consumption_terms = []
                for c_task, ratio in consumers:
                    c_lo, c_hi = self.windows[c_task.task_id]
                    if s >= c_lo:
                        cum_slot = min(s, c_hi)
                        unit_pcs = self._unit_pieces(c_task)
                        frac = Fraction(str(ratio)).limit_denominator(1000)
                        consumption_terms.append((c_task.task_id, cum_slot, unit_pcs, frac))

                if not consumption_terms:
                    continue

                lcm_denom = 1
                for _, _, _, frac in consumption_terms:
                    lcm_denom = (lcm_denom * frac.denominator) // math.gcd(lcm_denom, frac.denominator)

                scaled_supply = sum(supply_terms) * lcm_denom
                scaled_consumed = []
                for t_id, cum_slot, unit_pcs, frac in consumption_terms:
                    coeff = unit_pcs * frac.numerator * (lcm_denom // frac.denominator)
                    scaled_consumed.append(cumulative[t_id][cum_slot] * coeff)

                model.Add(scaled_supply >= sum(scaled_consumed))

    # ------------------------------------------------------------------
    # Capacity: resources are always created here; the 95% floor is either
    # compiled from the fixed kernel (ACT-22) or applied by the legacy body.
    # ------------------------------------------------------------------
    def _rules_for(self, template_id: str):
        if self.rule_set is None:
            return []
        return [
            rule for rule in self.rule_set.rules
            if rule.enabled and rule.template_id == template_id
        ]

    def _use_kernel(self, template_id: str) -> bool:
        return (
            self.rule_set is not None
            and template_id in self.kernel_templates
            and bool(self._rules_for(template_id))
        )

    def _compile_rules_for(
        self,
        template_id: str,
        model,
        x,
        active,
        started,
        end_of,
        opening,
        tails,
        loads,
        cumulative=None,
        force_hard: bool = True,
    ):
        """Compile one template's rules against the current scheduler model."""
        from dataclasses import replace

        from src.kernel import Nature, RuleSet, compile_rules

        rules = self._rules_for(template_id)
        if force_hard:
            rules = [replace(rule, nature=Nature.HARD) for rule in rules]
        derived = RuleSet(
            rule_set_id=f"{self.rule_set.rule_set_id}#{template_id}",
            site=self.rule_set.site,
            version=self.rule_set.version,
            groups=self.rule_set.groups,
            rules=rules,
        )
        context = _SchedulerCompileContext(
            self, model, x, active, started, end_of, opening, tails, loads, cumulative
        )
        compiled = compile_rules(context, derived)
        for warning in compiled.warnings:
            print(f"  [kernel] {warning}")
        return compiled

    def _add_campaign_penalty(self, model, active):
        """C5-a: soft penalty for mixing campaign groups inside one line/shift."""
        penalties = []
        for line in self.lines:
            line_tasks = [t for t in self.tasks if t.line == line]
            for s in range(self.num_shifts):
                groups = {}
                for task in line_tasks:
                    family = self.campaign_families.get(str(task.part_no))
                    if family is None or not family.campaign_group:
                        continue
                    if (task.task_id, s) not in active:
                        continue
                    groups.setdefault(family.campaign_group, []).append(task)
                if len(groups) <= 1:
                    continue
                present = []
                for group, members in groups.items():
                    p = model.NewBoolVar(f"campaign.{line}.{s}.{group}")
                    for task in members:
                        model.Add(p >= active[task.task_id, s])
                    model.Add(p <= sum(active[task.task_id, s] for task in members))
                    present.append(p)
                excess = model.NewIntVar(0, len(present), f"campaign_excess.{line}.{s}")
                model.Add(excess >= sum(present) - 1)
                penalties.append(excess)
        return 20 * sum(penalties) if penalties else None

    def _add_lead_time_penalty(self, model, end_of):
        """C4-a: soft SF operation lead-time gap before parent FG completion."""
        shifts_per_day = max(1, int(getattr(self.topology, "shifts_per_day", 3)))
        by_id = {t.task_id: t for t in self.plan.tasks}
        penalties = []
        for sf_task in self.plan.sf_tasks:
            parent = by_id.get(getattr(sf_task, "parent_task_id", None))
            if parent is None or parent.task_id not in end_of or sf_task.task_id not in end_of:
                continue
            lead_days = float(getattr(sf_task, "lead_time_days", 0.0) or 0.0)
            if lead_days <= 0 and not self.operation_lead_times:
                continue
            from src.leadtime import lead_shifts as _lead_shifts
            entry = self.operation_lead_times.get((self.site, sf_task.part_no, 10))
            lead_shift_count = _lead_shifts(entry, lead_time_days=lead_days, shift_hours=self.shift_hours, quantity=float(getattr(sf_task, "quantity", 0.0) or 0.0))
            if lead_shift_count <= 0:
                continue
            lead_shifts = lead_shift_count
            lag = model.NewIntVar(0, lead_shifts, f"lead_lag.{sf_task.task_id}")
            model.Add(lag >= lead_shifts - (end_of[parent.task_id] - end_of[sf_task.task_id]))
            penalties.append(lag)
        return 10 * sum(penalties) if penalties else None

    def _add_inventory_constraints(self, model, cumulative):
        """B4-b: soft time-phased FG inventory cap from Inventory-Policy.

        This is intentionally FG-only and soft: SF inventory is still governed by
        the two-stage material-balance ledger. The policy quantity is expressed in
        days of average demand, matching the B4-a policy table.
        """
        from src.inventory_policy import policy_for_day
        if not cumulative:
            return None
        shifts_per_day = max(1, int(getattr(self.topology, "shifts_per_day", 3)))
        horizon_days = max(1.0, self.num_shifts / shifts_per_day)
        penalties = []
        seen_parts = set()
        for task in self.plan.fg_tasks:
            part = str(task.part_no)
            if part in seen_parts:
                continue
            seen_parts.add(part)
            peers = [t for t in self.plan.fg_tasks if str(t.part_no) == part]
            if not peers:
                continue
            total_demand = sum(int(round(float(t.quantity))) for t in peers)
            if total_demand <= 0:
                continue
            daily = total_demand / horizon_days
            opening = int(round(float(getattr(self.plan, "initial_stock", {}).get(part, 0.0))))
            due_qty = {}
            for peer in peers:
                due_qty[self._due_shift(peer)] = due_qty.get(self._due_shift(peer), 0) + int(round(float(peer.quantity)))
            for s in range(self.num_shifts):
                policy = policy_for_day(self.inventory_policies, self.site, part, s // shifts_per_day + 1)
                if policy is None:
                    continue
                cumulative_demand = sum(int(qty) for due, qty in due_qty.items() if due <= s)
                prod_terms = []
                for peer in peers:
                    slots = cumulative.get(peer.task_id, {})
                    if not slots:
                        continue
                    eligible = [idx for idx in slots if idx <= s]
                    if not eligible:
                        continue
                    prod_terms.append(slots[max(eligible)] * self._unit_pieces(peer))
                if not prod_terms:
                    continue
                inventory = opening + sum(prod_terms) - cumulative_demand
                ceiling = int(round(policy.max_days * daily + max(0.0, policy.max_stock)))
                if self.inventory_hard:
                    model.Add(inventory <= ceiling)
                else:
                    over = model.NewIntVar(
                        0, max(1, int(total_demand + opening + 1)), f"inv_over.{part}.{s}"
                    )
                    model.Add(over >= inventory - ceiling)
                    penalties.append(over)
        sf_penalty = self._add_sf_inventory_penalties(model, cumulative)
        if not penalties and sf_penalty is None:
            return None
        return 50 * sum(penalties) + (sf_penalty if sf_penalty is not None else 0)

    def _add_sf_inventory_penalties(self, model, cumulative):
        """B4-b extension: soft time-phased SF inventory cap.

        SF demand is the variable FG production multiplied by the BOM ratio;
        supply is cumulative SF production plus opening stock.
        """
        from src.inventory_policy import policy_for_day
        if not cumulative:
            return None
        shifts_per_day = max(1, int(getattr(self.topology, "shifts_per_day", 3)))
        horizon_days = max(1.0, self.num_shifts / shifts_per_day)
        fg_by_part = {}
        for task in self.plan.fg_tasks:
            fg_by_part.setdefault(str(task.part_no), []).append(task)
        consumers = {}
        for fg_pn, entries in getattr(self.plan, "bom", {}).items():
            for sf_pn, ratio in entries:
                consumers.setdefault(str(sf_pn), []).extend(
                    (task, ratio) for task in fg_by_part.get(str(fg_pn), [])
                )
        sf_parts = {str(t.part_no) for t in self.plan.sf_tasks} | set(consumers)
        penalties = []
        for part in sorted(sf_parts):
            supply_tasks = [t for t in self.plan.sf_tasks if str(t.part_no) == part]
            if not supply_tasks:
                continue
            total = sum(int(round(float(t.quantity))) for t in supply_tasks)
            if total <= 0:
                continue
            daily = total / horizon_days
            opening = int(round(float(getattr(self.plan, "initial_stock", {}).get(part, 0.0))))
            for s in range(self.num_shifts):
                policy = policy_for_day(self.inventory_policies, self.site, part, s // shifts_per_day + 1)
                if policy is None:
                    continue
                supply = []
                for task in supply_tasks:
                    slots = cumulative.get(task.task_id, {})
                    eligible = [idx for idx in slots if idx <= s]
                    if eligible:
                        supply.append(slots[max(eligible)] * self._unit_pieces(task) * 1000)
                if not supply:
                    continue
                demand = []
                for fg_task, ratio in consumers.get(part, []):
                    slots = cumulative.get(fg_task.task_id, {})
                    eligible = [idx for idx in slots if idx <= s]
                    if eligible:
                        coeff = int(round(float(ratio) * 1000))
                        demand.append(slots[max(eligible)] * self._unit_pieces(fg_task) * coeff)
                ceiling = int(round((policy.max_days * daily + max(0.0, policy.max_stock)) * 1000))
                inventory = opening * 1000 + sum(supply) - sum(demand)
                if self.inventory_hard:
                    model.Add(inventory <= ceiling)
                else:
                    over = model.NewIntVar(0, max(1, total * 2000 + 1), f"sf_inv_over.{part}.{s}")
                    model.Add(over >= inventory - ceiling)
                    penalties.append(over)
        return 50 * sum(penalties) if penalties else None

    def _add_sequence_constraints(self, model, x, active, loads):
        """C1-b-3: optional intra-shift sequence, SDST, start/end and carry-over.

        For each line/shift, active tasks form one path (first -> ... -> last).
        ``trans[a,b]`` orders tasks inside the shift; ``carry[a,b]`` inherits the
        last mold of shift s-1 into the first task of shift s. Each task gets
        ``start/end/setup`` variables so the result can expose numeric sequence
        and setup minutes, not just an aggregate capacity penalty.
        """
        if not self.sequence_setup or not self.setup_matrix:
            return
        self._sequence_vars = {}

        def setup_ms_for(from_task, to_task):
            from_mold = str(getattr(from_task, "mold_id", "") or getattr(from_task, "part_no", ""))
            to_mold = str(getattr(to_task, "mold_id", "") or getattr(to_task, "part_no", ""))
            if from_mold == to_mold:
                return 0
            edge = self.setup_matrix.get((self.site, line, from_mold, to_mold))
            if edge is None:
                edge = self.setup_matrix.get((self.site, line, to_mold, from_mold))
            minutes = float(edge.minutes) if edge is not None else 60.0
            return int(round(minutes * 60 * TIME_SCALE))

        def setup_tech_for(from_task, to_task):
            from_mold = str(getattr(from_task, "mold_id", "") or getattr(from_task, "part_no", ""))
            to_mold = str(getattr(to_task, "mold_id", "") or getattr(to_task, "part_no", ""))
            if from_mold == to_mold:
                return 0
            edge = self.setup_matrix.get((self.site, line, from_mold, to_mold))
            if edge is None:
                edge = self.setup_matrix.get((self.site, line, to_mold, from_mold))
            return max(0, int(getattr(edge, "technicians", 1) or 1)) if edge is not None else 1

        seq_index = 0
        setup_overflow_terms = []
        setup_intervals_by_shift = defaultdict(list)
        for line in self.lines:
            line_tasks = [t for t in self.tasks if t.line == line]
            base_max = self._get_line_max_shift_ms(line)
            big_m = 4 * base_max
            tasks_by_slot = {}
            first_by_slot = {}
            last_by_slot = {}
            starts_by_slot = {}
            ends_by_slot = {}
            incoming_by_slot = {}
            incoming_tech_by_slot = {}

            for s in range(self.num_shifts):
                tasks = [
                    t for t in line_tasks
                    if (t.task_id, s) in active and (t.task_id, s) in x
                    and self.windows[t.task_id][0] <= s <= self.windows[t.task_id][1]
                ]
                if not tasks:
                    continue
                n = len(tasks)
                first = {t.task_id: model.NewBoolVar(f"seq_first.{seq_index}.{t.task_id}") for t in tasks}
                last = {t.task_id: model.NewBoolVar(f"seq_last.{seq_index}.{t.task_id}") for t in tasks}
                trans = {
                    (a.task_id, b.task_id): model.NewBoolVar(f"seq_tr.{seq_index}.{a.task_id}.{b.task_id}")
                    for a in tasks for b in tasks if a.task_id != b.task_id
                }
                order = {t.task_id: model.NewIntVar(0, max(0, n - 1), f"seq_u.{seq_index}.{t.task_id}") for t in tasks}
                for a in tasks:
                    aid = a.task_id
                    model.Add(sum(trans[aid, b.task_id] for b in tasks if b.task_id != aid) + last[aid] == active[aid, s])
                    model.Add(sum(trans[b.task_id, aid] for b in tasks if b.task_id != aid) + first[aid] == active[aid, s])
                model.Add(sum(first.values()) == sum(last.values()))
                model.Add(
                    sum(trans.values()) + sum(first.values())
                    == sum(active[t.task_id, s] for t in tasks)
                )
                for a in tasks:
                    for b in tasks:
                        if a.task_id == b.task_id:
                            continue
                        model.Add(
                            order[a.task_id] - order[b.task_id] + n * trans[a.task_id, b.task_id] <= n - 1
                        )

                reserve_ms = int(round(float(self.setup_reserve.get((line, s), 0.0)) * 60 * TIME_SCALE))
                slot_max = max(1, base_max - reserve_ms)
                time_max = slot_max if self.sequence_setup_hard else slot_max + 2 * base_max
                incoming = {t.task_id: [] for t in tasks}
                incoming_tech = {t.task_id: [] for t in tasks}
                starts = {}
                ends = {}
                for t in tasks:
                    st = model.NewIntVar(0, time_max, f"seq_start.{seq_index}.{t.task_id}")
                    en = model.NewIntVar(0, time_max, f"seq_end.{seq_index}.{t.task_id}")
                    proc = x[t.task_id, s] * self._unit_millis(t)
                    model.Add(en == st + proc)
                    model.Add(st <= time_max * active[t.task_id, s])
                    model.Add(en <= time_max * active[t.task_id, s])
                    starts[t.task_id] = st
                    ends[t.task_id] = en

                for a in tasks:
                    for b in tasks:
                        if a.task_id == b.task_id:
                            continue
                        term = setup_ms_for(a, b)
                        if term:
                            incoming[b.task_id].append(trans[a.task_id, b.task_id] * term)
                            incoming_tech[b.task_id].append(trans[a.task_id, b.task_id] * setup_tech_for(a, b))
                        model.Add(
                            starts[b.task_id]
                            >= ends[a.task_id] + term
                            - big_m * (1 - trans[a.task_id, b.task_id])
                        )

                tasks_by_slot[s] = tasks
                first_by_slot[s] = first
                last_by_slot[s] = last
                starts_by_slot[s] = starts
                ends_by_slot[s] = ends
                incoming_by_slot[s] = incoming
                incoming_tech_by_slot[s] = incoming_tech
                seq_index += 1

            # Cross-shift carry-over: last mold of s-1 -> first mold of s.
            for s in range(1, self.num_shifts):
                previous = tasks_by_slot.get(s - 1, [])
                current = tasks_by_slot.get(s, [])
                if not previous or not current:
                    continue
                carry = {
                    (a.task_id, b.task_id): model.NewBoolVar(f"seq_carry.{line}.{s}.{a.task_id}.{b.task_id}")
                    for a in previous for b in current
                }
                prev_any = sum(last_by_slot[s - 1][a.task_id] for a in previous)
                curr_any = sum(first_by_slot[s][b.task_id] for b in current)
                both = model.NewBoolVar(f"seq_both.{line}.{s}")
                model.Add(both <= prev_any)
                model.Add(both <= curr_any)
                model.Add(both >= prev_any + curr_any - 1)
                model.Add(sum(carry.values()) == both)
                for a in previous:
                    model.Add(
                        sum(carry[a.task_id, b.task_id] for b in current)
                        <= last_by_slot[s - 1][a.task_id]
                    )
                for b in current:
                    model.Add(
                        sum(carry[a.task_id, b.task_id] for a in previous)
                        <= first_by_slot[s][b.task_id]
                    )
                for a in previous:
                    for b in current:
                        term = setup_ms_for(a, b)
                        if term:
                            incoming_by_slot[s][b.task_id].append(carry[a.task_id, b.task_id] * term)
                            incoming_tech_by_slot[s][b.task_id].append(carry[a.task_id, b.task_id] * setup_tech_for(a, b))
                            model.Add(starts_by_slot[s][b.task_id] >= carry[a.task_id, b.task_id] * term)

            for s, tasks in tasks_by_slot.items():
                reserve_ms = int(round(float(self.setup_reserve.get((line, s), 0.0)) * 60 * TIME_SCALE))
                slot_max = max(1, base_max - reserve_ms)
                setup_vars = []
                for b in tasks:
                    setup_var = model.NewIntVar(0, big_m, f"seq_setup.{line}.{s}.{b.task_id}")
                    terms = incoming_by_slot[s][b.task_id]
                    model.Add(setup_var == (sum(terms) if terms else 0))
                    tech_terms = incoming_tech_by_slot[s][b.task_id]
                    tech_var = model.NewIntVar(0, 10, f"seq_tech.{line}.{s}.{b.task_id}")
                    model.Add(tech_var == (sum(tech_terms) if tech_terms else 0))
                    model.Add(starts_by_slot[s][b.task_id] <= setup_var + big_m * (1 - first_by_slot[s][b.task_id]))
                    setup_vars.append(setup_var)
                    setup_start = model.NewIntVar(0, big_m, f"seq_setup_start.{line}.{s}.{b.task_id}")
                    model.Add(setup_start == starts_by_slot[s][b.task_id] - setup_var)
                    setup_intervals_by_shift[s].append((
                        model.NewOptionalIntervalVar(
                            setup_start, setup_var, starts_by_slot[s][b.task_id],
                            active[b.task_id, s], f"seq_setup_interval.{line}.{s}.{b.task_id}",
                        ),
                        tech_var,
                    ))
                    self._sequence_vars[(b.task_id, s)] = {
                        "active": active[b.task_id, s],
                        "start": starts_by_slot[s][b.task_id],
                        "end": ends_by_slot[s][b.task_id],
                        "setup": setup_var,
                        "first": first_by_slot[s][b.task_id],
                        "last": last_by_slot[s][b.task_id],
                        "line": line,
                        "shift": self.shifts[s],
                    }
                if self.sequence_setup_hard:
                    model.Add(loads[line, s] + sum(setup_vars) <= slot_max)
                else:
                    over = model.NewIntVar(0, base_max, f"seq_setup_over.{line}.{s}")
                    model.Add(loads[line, s] + sum(setup_vars) <= slot_max + over)
                    setup_overflow_terms.append(over)

        if self.setup_technicians_available > 0:
            for s, pairs in setup_intervals_by_shift.items():
                if pairs:
                    intervals = [pair[0] for pair in pairs]
                    demands = [pair[1] for pair in pairs]
                    model.AddCumulative(intervals, demands, self.setup_technicians_available)

        if setup_overflow_terms:
            return 200 * sum(setup_overflow_terms)
        return None

    def _extract_sequence(self, solver):
        records = []
        for (task_id, slot), rec in getattr(self, "_sequence_vars", {}).items():
            try:
                if solver.Value(rec["active"]) <= 0:
                    continue
                shift = rec["shift"]
                records.append({
                    "task_id": task_id,
                    "line": rec["line"],
                    "shift_key": f"{shift.date_str} {shift.shift_name}",
                    "shift_index": slot,
                    "start_min": round(solver.Value(rec["start"]) / 60000.0, 3),
                    "end_min": round(solver.Value(rec["end"]) / 60000.0, 3),
                    "setup_min": round(solver.Value(rec["setup"]) / 60000.0, 3),
                    "first": bool(solver.Value(rec["first"])),
                    "last": bool(solver.Value(rec["last"])),
                })
            except Exception:
                continue
        records.sort(key=lambda r: (r["line"], r["shift_index"], r["start_min"], r["task_id"]))
        seq_by_key = {}
        for rec in records:
            key = (rec["line"], rec["shift_key"])
            seq_by_key[key] = seq_by_key.get(key, 0) + 1
            rec["sequence"] = seq_by_key[key]
        return records

    def _compile_capacity_kernel(self, model, x, active, started, end_of, opening, tails, loads):
        """Compile capacity rules, respecting each rule's hard/soft nature.

        A soft capacity rule (SC-02) becomes a bilateral penalty, not a hard
        ceiling; a hard rule keeps the 95%/ceiling constraints.
        """
        compiled = self._compile_rules_for(
            "capacity_utilization", model, x, active, started, end_of, opening, tails, loads,
            force_hard=False,
        )
        if not compiled.book.terms:
            return None
        return compiled.total_objective()

    def _add_line_capacity(self, model, x, active, started, end_of, utilization_mode: str, gap_cap=None, use_kernel: bool = True):
        """Create the line resources, then apply either the kernel or legacy floor."""
        kernel_capacity = (
            use_kernel
            and utilization_mode == "hard"
            and self._use_kernel("capacity_utilization")
        )
        shortfall, violations, overloads, opening, tails, loads = self._legacy_capacity_body(
            model, x, started, "off" if kernel_capacity else utilization_mode, gap_cap
        )
        kernel_objective = None
        if kernel_capacity:
            kernel_objective = self._compile_capacity_kernel(
                model, x, active, started, end_of, opening, tails, loads
            )
        return shortfall, violations, overloads, opening, tails, loads, kernel_objective

    def _legacy_capacity_body(self, model, x, started, utilization_mode: str, gap_cap=None):
        """Per line-shift load (processing + setup), opening decision, rated ceiling
        and the 95% floor applied to every opened shift except the closing one.

        Opening a shift is a decision, not something inferred from load: the business
        rule is "if you open a shift you must fill it", and an idle shift is a separate
        question (calendar coverage) rather than a utilisation violation.
        """
        shortfall = []
        violations = []
        overloads = []
        opening: Dict[Tuple[str, int], object] = {}
        tails: Dict[Tuple[str, int], object] = {}
        loads: Dict[Tuple[str, int], object] = {}

        setup_ms = 0 if self.sequence_setup else self.setup_ms
        for line in self.lines:
            line_tasks = [t for t in self.tasks if t.line == line]
            max_line_ms = self._get_line_max_shift_ms(line)
            line_open = []
            line_load = []
            for s in range(self.num_shifts):
                proc = [x[t.task_id, s] * self._unit_millis(t) for t in line_tasks if (t.task_id, s) in x]
                setup = [
                    started[t.task_id, s] * setup_ms
                    for t in line_tasks
                    if (t.task_id, s) in started
                ]
                reserve_ms = int(round(float(self.setup_reserve.get((line, s), 0.0)) * 60 * TIME_SCALE))
                slot_max = max(1, max_line_ms - reserve_ms)
                var = model.NewIntVar(0, slot_max, f"load.{line}.{s}")
                model.Add(var == (sum(proc) + sum(setup) if proc or setup else 0))
                o = model.NewBoolVar(f"open.{line}.{s}")
                # o == 1 exactly when the shift carries load.
                model.Add(var >= 1).OnlyEnforceIf(o)
                model.Add(var <= slot_max * o)
                opening[line, s] = o
                loads[line, s] = var
                line_open.append(o)
                line_load.append(var)

                if max_line_ms > self.shift_ms:
                    over = model.NewIntVar(0, max_line_ms - self.shift_ms, f"over.{line}.{s}")
                    model.Add(over >= var - self.shift_ms)
                    overloads.append(over)

            work = sum(self._units_total(t) * self._unit_millis(t) for t in line_tasks)
            # A slot holds at most max_line_ms of work, so this floor on the number of
            # opened shifts is implied by the capacity constraint above.
            model.Add(sum(line_open) >= -(-work // max_line_ms))

            # At most one closing shift per line, and it must be the last opened one.
            line_tails = []
            for s in range(self.num_shifts):
                tail = model.NewBoolVar(f"tail.{line}.{s}")
                model.Add(tail <= line_open[s])
                for later in range(s + 1, self.num_shifts):
                    model.Add(tail <= 1 - line_open[later])
                tails[line, s] = tail
                line_tails.append(tail)
            model.Add(sum(line_tails) <= 1)

            if utilization_mode == "off":
                continue

            for s in range(self.num_shifts):
                o = line_open[s]
                tail = tails[line, s]
                var = line_load[s]
                if utilization_mode == "hard":
                    model.Add(var + self.min_shift_ms * tail >= self.min_shift_ms * o)
                    continue
                gap = model.NewIntVar(0, self.min_shift_ms, f"gap.{line}.{s}")
                model.Add(gap >= self.min_shift_ms * o - self.min_shift_ms * tail - var)
                shortfall.append(gap)
                # `below` is exactly "this opened slot is short of 95%": a closed slot
                # and the closing shift both force it off.
                below = model.NewBoolVar(f"below.{line}.{s}")
                model.Add(var >= self.min_shift_ms * (o - tail)).OnlyEnforceIf(below.Not())
                model.Add(var <= self.min_shift_ms * (o - tail) - 1).OnlyEnforceIf(below)
                if gap_cap is not None:
                    model.Add(gap <= gap_cap[1])
                violations.append(below)

        if gap_cap is not None:
            model.Add(sum(shortfall) <= gap_cap[0])
            model.Add(sum(violations) <= gap_cap[2])
        return shortfall, violations, overloads, opening, tails, loads

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
                units = solver.Value(x[task.task_id, s])
                if units <= 0:
                    continue
                key: ShiftKey = (shift.date_str, shift.shift_name)
                breakdown[key] = int(units * self._unit_pieces(task))
                seconds[key] = units * self._unit_millis(task) / TIME_SCALE
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
                crew_required=getattr(task, 'crew_required', 0.0),
                splittable=self._is_splittable(task),
            ))
        return scheduled

    def _clock_times(self, solver, x) -> Dict[Tuple[str, int], Tuple[datetime, datetime]]:
        """Walk each line shift by shift so 制造开始时间 reads as a real clock time."""
        clock: Dict[Tuple[str, int], Tuple[datetime, datetime]] = {}
        for line in self.lines:
            line_tasks = [t for t in self.tasks if t.line == line]
            # Earliest-due first inside a shift: a shipment due at the shift end must
            # not be pushed behind work that is not due yet.
            order = sorted(
                line_tasks,
                key=lambda t: (t.due_datetime or datetime.max, self._first_shift(solver, x, t), t.task_id),
            )
            for s, shift in enumerate(self.shifts):
                cursor = 0.0
                for task in order:
                    if (task.task_id, s) not in x:
                        continue
                    units = solver.Value(x[task.task_id, s])
                    if units <= 0:
                        continue
                    span = units * self._unit_millis(task) / TIME_SCALE
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
