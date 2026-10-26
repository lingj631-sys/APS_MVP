"""Rule compiler: RuleInstance -> CP-SAT terms.

The compiler is deliberately independent of ``src/scheduler.py``. A scheduling
model exposes the small ``CompileContext`` surface below; the same operators can
then be used by the current scheduler, a future part-level model, or a test toy.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
import math
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple, runtime_checkable

from src.kernel.registry import IMPLEMENTED_TEMPLATES, get_template
from src.kernel.scope import resolve_for_tasks
from src.kernel.types import Nature, RuleInstance, RuleSet, Target


@runtime_checkable
class CompileContext(Protocol):
    model: Any
    num_shifts: int

    def lines(self) -> Iterable[str]: ...
    def tasks(self) -> Iterable[Any]: ...
    def window(self, task) -> Tuple[int, int]: ...
    def x(self, task, slot: int): ...
    def active(self, task, slot: int): ...
    def started(self, task, slot: int): ...
    def end_of(self, task): ...
    def load(self, line: str, slot: int): ...
    def open(self, line: str, slot: int): ...
    def tail(self, line: str, slot: int): ...
    def capacity_ms(self, line: str) -> int: ...
    def min_shift_ms(self, line: str) -> int: ...
    def load_upper_bound(self, line: str) -> int: ...
    def due_slot(self, task) -> int: ...
    def due_key(self, task) -> Any: ...
    def mold_id(self, task) -> Optional[str]: ...
    def pm_slots(self, line: str) -> Iterable[int]: ...
    def material_ready_slot(self, task) -> int: ...
    def is_splittable(self, task) -> bool: ...
    def children(self, task) -> Iterable[Any]: ...
    def bom_ratio(self, task) -> Any: ...
    def stock_covered(self, task) -> float: ...
    def cumulative(self, task, slot: int): ...
    def unit_pieces(self, task) -> int: ...
    def customer_tier(self, task) -> str: ...
    def crew_required(self, task) -> Optional[float]: ...
    def labor_available(self, line: str, slot: int) -> Optional[int]: ...
    def labor_pool(self, line: str) -> str: ...
    def shift_weekday(self, slot: int) -> int: ...
    def shift_name(self, slot: int) -> str: ...
    def shifts_per_day(self) -> int: ...
    def co_product_partner(self, task) -> Optional[Any]: ...


@dataclass(frozen=True)
class PenaltyTerm:
    rule_id: str
    group_id: str
    weight: float
    expr: Any


@dataclass
class PenaltyBook:
    terms: List[PenaltyTerm] = field(default_factory=list)

    def add(self, rule: RuleInstance, expr: Any) -> None:
        self.terms.append(PenaltyTerm(rule.rule_id, rule.group_id, float(rule.weight), expr))

    def terms_in(self, group_id: str) -> List[PenaltyTerm]:
        return [t for t in self.terms if t.group_id == group_id]

    def rule_weights(self, group_id: str) -> Dict[str, float]:
        """One weight per rule, not per variable.

        A rule such as changeover contributes one term per task/shift; normalising
        by the number of terms would silently divide its weight by the instance
        size, so the weight is attached to the rule and reused for every term.
        """
        weights: Dict[str, float] = {}
        for term in self.terms_in(group_id):
            weights.setdefault(term.rule_id, max(0.0, term.weight))
        return weights

    def scaled_weights(self, group_id: str, scale: int = 1000) -> Dict[str, int]:
        weights = self.rule_weights(group_id)
        total = sum(weights.values())
        if total <= 0:
            return {rule_id: 0 for rule_id in weights}
        return {rule_id: max(1, int(round(w / total * scale))) for rule_id, w in weights.items()}

    def group_objective(self, group_id: str, scale: int = 1000):
        terms = self.terms_in(group_id)
        if not terms:
            return 0
        coeffs = self.scaled_weights(group_id, scale)
        return sum(t.expr * coeffs[t.rule_id] for t in terms)

    def total_objective(self, scale: int = 1000):
        groups = sorted({t.group_id for t in self.terms})
        return sum(self.group_objective(group_id, scale) for group_id in groups)

    def groups(self) -> List[str]:
        return sorted({t.group_id for t in self.terms})


@dataclass
class CompiledRules:
    rule_set: RuleSet
    book: PenaltyBook
    hard_rules: List[str]
    params: Dict[str, Dict[str, Any]]
    warnings: List[str]

    def group_objective(self, group_id: str, scale: int = 1000):
        return self.book.group_objective(group_id, scale)

    def total_objective(self, scale: int = 1000):
        return self.book.total_objective(scale)

    def ordered_groups(self):
        return self.rule_set.ordered_groups()

    def applied_rule_ids(self) -> List[str]:
        return sorted(self.params)


def _target(site: str, task: Any) -> Target:
    return Target(
        site=site,
        line=str(getattr(task, "line", "*")),
        stage=str(getattr(task, "stage", "*")),
        part=str(getattr(task, "part_no", "*")),
    )


def _compile_capacity(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    warnings: List[str] = []
    min_util = float(params["min_util"])
    max_util = float(params["max_util"])
    tail_exempt = bool(params["tail_exempt"])
    # Capacity is line-level, but the rule may be stage-scoped. Apply it only to
    # lines that carry a matching task, so an FG rule cannot widen the SF line.
    applicable_lines = {
        str(getattr(task, "line", ""))
        for task in ctx.tasks()
        if rule.scope.matches(_target(site, task))
    }

    for line in ctx.lines():
        if str(line) not in applicable_lines:
            continue
        cap = ctx.capacity_ms(line)
        floor = int(round(min_util * cap))
        ceiling = int(round(max_util * cap))
        for slot in range(ctx.num_shifts):
            load = ctx.load(line, slot)
            # Only add the ceiling when the load variable's own domain is wider:
            # a redundant bound changes CP-SAT's search for no benefit.
            if ceiling < ctx.load_upper_bound(line):
                ctx.model.Add(load <= ceiling)
            tail = ctx.tail(line, slot) if tail_exempt else 0
            opened = ctx.open(line, slot)
            lhs = load + floor * tail if tail_exempt else load
            rhs = floor * opened
            if rule.nature is Nature.HARD:
                ctx.model.Add(lhs >= rhs)
            else:
                gap = ctx.model.NewIntVar(0, floor, f"gap.{rule.rule_id}.{line}.{slot}")
                ctx.model.Add(gap >= rhs - lhs)
                book.add(rule, gap)
    return warnings, rule.nature is Nature.HARD


def _compile_min_batch(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    warnings: List[str] = []
    unit = params["unit"]
    minimum = int(params["min_multiple"])
    for task in ctx.tasks():
        if not rule.scope.matches(_target(site, task)):
            continue
        lot_size = max(1, int(getattr(task, "lot_size", 1)))
        mpq = max(1, int(getattr(task, "mpq", 1)))
        splittable = hasattr(ctx, "is_splittable") and ctx.is_splittable(task)
        min_lots = 1 if splittable else (max(1, mpq // lot_size) if unit == "MPQ" else max(1, minimum))
        lo, hi = ctx.window(task)
        for slot in range(lo, hi + 1):
            ctx.model.Add(ctx.x(task, slot) >= min_lots * ctx.active(task, slot))
    return warnings, True


def _compile_due_date(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    warnings: List[str] = []
    hard = rule.nature is Nature.HARD and str(params["mode"]) == "hard"
    for task in ctx.tasks():
        if str(getattr(task, "stage", "")) != "FG":
            continue
        if not rule.scope.matches(_target(site, task)):
            continue
        due = None
        if hasattr(ctx, "due_slot"):
            due = ctx.due_slot(task)
        if due is None:
            due = getattr(task, "due_slot", None)
        if due is None:
            warnings.append(f"{rule.rule_id}: 任务 {getattr(task, 'task_id', '?')} 没有 due_slot，跳过")
            continue
        end = ctx.end_of(task)
        if hard:
            ctx.model.Add(end <= int(due))
        else:
            late = ctx.model.NewIntVar(0, ctx.num_shifts, f"late.{rule.rule_id}.{getattr(task, 'task_id', '?')}")
            ctx.model.Add(late >= end - int(due))
            book.add(rule, late)
    return warnings, hard


def _compile_changeover(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    warnings: List[str] = []
    if float(params["setup_minutes"]) > 0:
        warnings.append(f"{rule.rule_id}: setup_minutes 未计入容量（TRP 已含换型时保持 0）")
    if str(params["count_by"]) != "run_start":
        warnings.append(f"{rule.rule_id}: count_by={params['count_by']} 暂未实现，按 run_start 计价")
    for task in ctx.tasks():
        if not rule.scope.matches(_target(site, task)):
            continue
        lo, hi = ctx.window(task)
        for slot in range(lo, hi + 1):
            book.add(rule, ctx.started(task, slot))
    return warnings, False


def _compile_sequence(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    warnings: List[str] = []
    if str(params["order_by"]) != "due":
        warnings.append(f"{rule.rule_id}: order_by={params['order_by']} 暂按 due 处理")
    groups = defaultdict(list)
    for task in ctx.tasks():
        if not rule.scope.matches(_target(site, task)):
            continue
        groups[(getattr(task, "line", ""), getattr(task, "part_no", ""))].append(task)
    for group in groups.values():
        if len(group) < 2:
            continue
        by_due = defaultdict(list)
        for task in group:
            by_due[ctx.due_key(task)].append(task)
        dues = sorted(by_due)
        for earlier, later in zip(dues, dues[1:]):
            for task_a in by_due[earlier]:
                for task_b in by_due[later]:
                    ctx.model.Add(ctx.end_of(task_a) <= ctx.end_of(task_b))
    return warnings, True


def _compile_tooling(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    available = int(params["available_qty"])
    groups = defaultdict(list)
    for task in ctx.tasks():
        if not rule.scope.matches(_target(site, task)):
            continue
        mold = ctx.mold_id(task)
        if mold:
            groups[mold].append(task)
    for group in groups.values():
        if len({getattr(t, "line", "") for t in group}) <= 1:
            continue
        for slot in range(ctx.num_shifts):
            actives = [
                ctx.active(task, slot)
                for task in group
                if ctx.window(task)[0] <= slot <= ctx.window(task)[1]
            ]
            if len(actives) > available:
                ctx.model.Add(sum(actives) <= available)
    return [], True


def _compile_material_ready(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    for task in ctx.tasks():
        if not rule.scope.matches(_target(site, task)):
            continue
        ready = int(ctx.material_ready_slot(task) or 0)
        if ready <= 0:
            continue
        lo, hi = ctx.window(task)
        for slot in range(lo, min(ready, hi + 1)):
            ctx.model.Add(ctx.x(task, slot) == 0)
    return [], True


def _compile_material_balance(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    """FG assembly at shift s consumes component output available at s-1 (minus buffer)."""
    buffer_shifts = int(params["buffer_shifts"])
    include_stock = bool(params["include_stock"])
    for fg_task in ctx.tasks():
        if str(getattr(fg_task, "stage", "")) != "FG":
            continue
        if not rule.scope.matches(_target(site, fg_task)):
            continue
        components = list(ctx.children(fg_task))
        if not components:
            continue
        fg_lo, fg_hi = ctx.window(fg_task)
        for slot in range(fg_lo, fg_hi + 1):
            assembled = ctx.cumulative(fg_task, slot) * ctx.unit_pieces(fg_task)
            for component in components:
                comp_lo, comp_hi = ctx.window(component)
                frac = Fraction(str(ctx.bom_ratio(component))).limit_denominator(1000)
                num, denom = frac.numerator, frac.denominator
                lookback = slot - 1 - buffer_shifts
                stock = int(round(ctx.stock_covered(component))) if include_stock else 0
                if lookback < comp_lo:
                    # Nothing has been produced yet; opening stock may still cover it.
                    avail = stock
                else:
                    avail = ctx.cumulative(component, min(lookback, comp_hi)) * ctx.unit_pieces(component) + stock
                ctx.model.Add(denom * avail >= num * assembled)
    return [], True


def _compile_pm_block(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    for line in ctx.lines():
        slots = [int(s) for s in ctx.pm_slots(line)]
        if not slots:
            continue
        for task in ctx.tasks():
            if getattr(task, "line", None) != line:
                continue
            if not rule.scope.matches(_target(site, task)):
                continue
            lo, hi = ctx.window(task)
            for slot in slots:
                if lo <= slot <= hi:
                    ctx.model.Add(ctx.x(task, slot) == 0)
    return [], True


def _parse_tier_weights(text: Any) -> Dict[str, float]:
    weights: Dict[str, float] = {}
    for chunk in str(text or "").split(","):
        if ":" not in chunk:
            continue
        key, value = chunk.split(":", 1)
        try:
            weights[key.strip().upper()] = float(value)
        except ValueError:
            continue
    return weights


def _compile_customer_priority(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    """Soft lateness weighted by customer tier (STRATEGIC 10 / A 3 / B 1 by default)."""
    weights = _parse_tier_weights(params["tier_weights"])
    default = weights.get("A", 1.0)
    for task in ctx.tasks():
        if str(getattr(task, "stage", "")) != "FG":
            continue
        if not rule.scope.matches(_target(site, task)):
            continue
        due = ctx.due_slot(task)
        if due is None:
            continue
        late = ctx.model.NewIntVar(
            0, ctx.num_shifts, f"late.{rule.rule_id}.{getattr(task, 'task_id', '?')}"
        )
        ctx.model.Add(late >= ctx.end_of(task) - int(due))
        tier = str(ctx.customer_tier(task)).upper()
        multiplier = weights.get(tier, default)
        # CP-SAT constraints need integer coefficients; tier weights are ints.
        factor = max(1, int(round(multiplier)))
        book.add(rule, late * factor if factor != 1 else late)
    return [], False


def _compile_concurrency(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    """At most ``max_models`` distinct part numbers active on a line in one shift."""
    limit = int(params["max_models"])
    warnings: List[str] = []
    if str(params["window"]) != "shift":
        warnings.append(f"{rule.rule_id}: window={params['window']} 暂按 shift 处理")
    for line in ctx.lines():
        line_tasks = [
            task for task in ctx.tasks()
            if str(getattr(task, "line", "")) == str(line)
            and rule.scope.matches(_target(site, task))
        ]
        parts = sorted({str(getattr(task, "part_no", "")) for task in line_tasks})
        for slot in range(ctx.num_shifts):
            actives = []
            for part in parts:
                matching = [
                    task for task in line_tasks
                    if str(getattr(task, "part_no", "")) == part
                    and ctx.window(task)[0] <= slot <= ctx.window(task)[1]
                ]
                if not matching:
                    continue
                present = ctx.model.NewBoolVar(f"p_act.{rule.rule_id}.{line}.{part}.{slot}")
                for task in matching:
                    ctx.model.Add(present >= ctx.active(task, slot))
                # Exact OR: keep the indicator at 0 when the part is not running,
                # otherwise the extra free booleans slow the search down.
                ctx.model.Add(present <= sum(ctx.active(task, slot) for task in matching))
                actives.append(present)
            if not actives:
                continue
            if rule.nature is Nature.HARD:
                if len(actives) > limit:
                    ctx.model.Add(sum(actives) <= limit)
            else:
                excess = ctx.model.NewIntVar(0, len(actives), f"excess.{rule.rule_id}.{line}.{slot}")
                ctx.model.Add(excess >= sum(actives) - limit)
                book.add(rule, excess)
    return warnings, rule.nature is Nature.HARD


def _compile_labor_capacity(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    """Site per-shift headcount cap plus shared-crew-pool mutual exclusion.

    ``crew[l,s]`` is the max crew over the parts active on line l in shift s, which
    is the linear form of "a line is staffed for its most demanding active part".
    """
    warnings: List[str] = []
    fallback = max(0, int(params.get("available_headcount") or 0))
    crews: Dict[int, List[Tuple[str, str, Any, int]]] = defaultdict(list)

    for line in ctx.lines():
        pool = ctx.labor_pool(line)
        line_tasks = [
            task for task in ctx.tasks()
            if str(getattr(task, "line", "")) == str(line)
            and rule.scope.matches(_target(site, task))
        ]
        for slot in range(ctx.num_shifts):
            available = ctx.labor_available(line, slot)
            if available is None and fallback > 0:
                available = fallback
            if available is None:
                continue
            available = max(0, int(available))
            crew = ctx.model.NewIntVar(0, available, f"crew.{rule.rule_id}.{line}.{slot}")
            for task in line_tasks:
                if not (ctx.window(task)[0] <= slot <= ctx.window(task)[1]):
                    continue
                need = ctx.crew_required(task)
                if need is None or need <= 0:
                    continue
                ctx.model.Add(crew >= int(math.ceil(need)) * ctx.active(task, slot))
            crews[slot].append((line, pool, crew, available))

    if not crews:
        warnings.append(f"{rule.rule_id}: 无可用人力数据，labor_capacity 跳过")
        return warnings, False

    for slot, items in crews.items():
        available = min(item[3] for item in items)
        total = sum(item[2] for item in items)
        if rule.nature is Nature.HARD:
            ctx.model.Add(total <= available)
        else:
            excess = ctx.model.NewIntVar(
                0, max(0, sum(item[3] for item in items)),
                f"labor_excess.{rule.rule_id}.{slot}",
            )
            ctx.model.Add(excess >= total - available)
            book.add(rule, excess)

    pool_lines: Dict[str, set] = defaultdict(set)
    for line in ctx.lines():
        pool_lines[ctx.labor_pool(line)].add(str(line))
    for pool, lines in pool_lines.items():
        if len(lines) <= 1:
            continue
        for slot in range(ctx.num_shifts):
            indicators = []
            for line in lines:
                matching = [
                    task for task in ctx.tasks()
                    if str(getattr(task, "line", "")) == line
                    and rule.scope.matches(_target(site, task))
                    and ctx.window(task)[0] <= slot <= ctx.window(task)[1]
                ]
                if not matching:
                    continue
                present = ctx.model.NewBoolVar(f"pool_active.{rule.rule_id}.{pool}.{line}.{slot}")
                for task in matching:
                    ctx.model.Add(present >= ctx.active(task, slot))
                ctx.model.Add(present <= sum(ctx.active(task, slot) for task in matching))
                indicators.append(present)
            if len(indicators) <= 1:
                continue
            if rule.nature is Nature.HARD:
                ctx.model.Add(sum(indicators) <= 1)
            else:
                excess = ctx.model.NewIntVar(
                    0, len(indicators), f"pool_excess.{rule.rule_id}.{pool}.{slot}"
                )
                ctx.model.Add(excess >= sum(indicators) - 1)
                book.add(rule, excess)

    return warnings, rule.nature is Nature.HARD


def _compile_contiguity(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    max_blocks = int(params["max_blocks"])
    if max_blocks <= 0:
        return [], False
    soft = rule.nature is Nature.SOFT
    for task in ctx.tasks():
        if not rule.scope.matches(_target(site, task)):
            continue
        lo, hi = ctx.window(task)
        starts = [ctx.started(task, slot) for slot in range(lo, hi + 1)]
        total = sum(starts)
        if soft:
            excess = ctx.model.NewIntVar(
                0, len(starts), f"blocks_excess.{rule.rule_id}.{getattr(task, 'task_id', '?')}"
            )
            ctx.model.Add(excess >= total - max_blocks)
            book.add(rule, excess)
        else:
            ctx.model.Add(total <= max_blocks)
    return [], rule.nature is Nature.HARD


def _compile_calendar(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    mode = str(params["mode"])
    if mode == "FIVE_TWO" or bool(params["weekend_off"]):
        off_from = 5
    elif mode == "FOUR_THREE":
        off_from = 4
    else:
        return [], False
    for task in ctx.tasks():
        if not rule.scope.matches(_target(site, task)):
            continue
        lo, hi = ctx.window(task)
        for slot in range(lo, hi + 1):
            if ctx.shift_weekday(slot) < off_from:
                continue
            if rule.nature is Nature.HARD:
                ctx.model.Add(ctx.active(task, slot) == 0)
            else:
                book.add(rule, ctx.active(task, slot))
    return [], rule.nature is Nature.HARD


def _compile_time_window(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    warnings: List[str] = []
    allowed = {part.strip() for part in str(params["allowed_shifts"]).split(",") if part.strip()}
    if not allowed:
        return [], False
    if float(params["min_duration_hours"]) > 0:
        warnings.append(f"{rule.rule_id}: min_duration_hours 暂未实现（班次粒度）")
    for task in ctx.tasks():
        if not rule.scope.matches(_target(site, task)):
            continue
        lo, hi = ctx.window(task)
        for slot in range(lo, hi + 1):
            if ctx.shift_name(slot) in allowed:
                continue
            if rule.nature is Nature.HARD:
                ctx.model.Add(ctx.active(task, slot) == 0)
            else:
                book.add(rule, ctx.active(task, slot))
    return warnings, rule.nature is Nature.HARD


def _compile_inventory_bound(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    """v1 interpretation: do not build more than ``max_days`` before the due shift."""
    warnings: List[str] = []
    max_days = float(params["max_days"])
    if max_days <= 0:
        return [], False
    if float(params["min_days"]) > 0 or float(params["safety_stock"]) > 0:
        warnings.append(f"{rule.rule_id}: min_days/safety_stock 暂未实现，仅使用 max_days")
    horizon = int(round(max_days * ctx.shifts_per_day()))
    for task in ctx.tasks():
        if not rule.scope.matches(_target(site, task)):
            continue
        due = ctx.due_slot(task)
        if due is None:
            continue
        bound = int(due) - horizon
        if bound <= 0:
            continue
        if rule.nature is Nature.HARD:
            ctx.model.Add(ctx.end_of(task) >= bound)
        else:
            early = ctx.model.NewIntVar(
                0, ctx.num_shifts, f"early.{rule.rule_id}.{getattr(task, 'task_id', '?')}"
            )
            ctx.model.Add(early >= bound - ctx.end_of(task))
            book.add(rule, early)
    return warnings, rule.nature is Nature.HARD


def _compile_freeze(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    fence = int(params["fence_shifts"])
    if fence <= 0:
        return [], False
    for task in ctx.tasks():
        if not rule.scope.matches(_target(site, task)):
            continue
        lo, hi = ctx.window(task)
        for slot in range(lo, min(fence, hi + 1)):
            if rule.nature is Nature.HARD:
                ctx.model.Add(ctx.active(task, slot) == 0)
            else:
                book.add(rule, ctx.active(task, slot))
    return [], rule.nature is Nature.HARD


def _compile_co_product(ctx: CompileContext, rule: RuleInstance, params: Dict[str, Any], book: PenaltyBook, site: str):
    warnings: List[str] = []
    try:
        ratio = Fraction(str(params["ratio"] or "1:1").replace(":", "/"))
    except (ValueError, ZeroDivisionError):
        return [f"{rule.rule_id}: ratio={params['ratio']} 非法，跳过"], False
    for task in ctx.tasks():
        if not rule.scope.matches(_target(site, task)):
            continue
        partner = ctx.co_product_partner(task)
        if partner is None:
            warnings.append(f"{rule.rule_id}: {getattr(task, 'task_id', '?')} 没有 L/R 配对任务")
            continue
        if str(getattr(task, "task_id", "")) >= str(getattr(partner, "task_id", "")):
            continue
        upper = max(1, int(getattr(task, "lots", 1)), int(getattr(partner, "lots", 1))) * max(
            ratio.numerator, ratio.denominator
        )
        for slot in range(ctx.num_shifts):
            if not (ctx.window(task)[0] <= slot <= ctx.window(task)[1]):
                continue
            if not (ctx.window(partner)[0] <= slot <= ctx.window(partner)[1]):
                continue
            lhs = ctx.x(task, slot) * ratio.denominator
            rhs = ctx.x(partner, slot) * ratio.numerator
            if rule.nature is Nature.HARD:
                ctx.model.Add(lhs == rhs)
            else:
                diff = ctx.model.NewIntVar(0, upper, f"co_diff.{rule.rule_id}.{getattr(task, 'task_id', '?')}.{slot}")
                ctx.model.Add(diff >= lhs - rhs)
                ctx.model.Add(diff >= rhs - lhs)
                book.add(rule, diff)
    return warnings, rule.nature is Nature.HARD


_DISPATCH = {
    "capacity_utilization": _compile_capacity,
    "min_batch": _compile_min_batch,
    "due_date": _compile_due_date,
    "changeover": _compile_changeover,
    "sequence": _compile_sequence,
    "tooling_exclusivity": _compile_tooling,
    "material_ready": _compile_material_ready,
    "material_balance": _compile_material_balance,
    "pm_block": _compile_pm_block,
    "customer_priority": _compile_customer_priority,
    "concurrency": _compile_concurrency,
    "labor_capacity": _compile_labor_capacity,
    "contiguity": _compile_contiguity,
    "calendar": _compile_calendar,
    "time_window": _compile_time_window,
    "inventory_bound": _compile_inventory_bound,
    "freeze": _compile_freeze,
    "co_product": _compile_co_product,
}


def compile_rules(
    ctx: CompileContext,
    rule_set: RuleSet,
    on_date: Optional[date] = None,
) -> CompiledRules:
    rules, warnings = resolve_for_tasks(rule_set, ctx.tasks(), on_date)
    book = PenaltyBook()
    hard_rules: List[str] = []
    params_by_rule: Dict[str, Dict[str, Any]] = {}

    for rule in rules:
        spec = get_template(rule.template_id)
        params = spec.validate(rule.params)
        params_by_rule[rule.rule_id] = params
        compiler = _DISPATCH.get(rule.template_id)
        if compiler is None or rule.template_id not in IMPLEMENTED_TEMPLATES:
            warnings.append(f"{rule.rule_id}: 模板 {rule.template_id} 已登记但编译器未实现，跳过")
            continue
        rule_warnings, is_hard = compiler(ctx, rule, params, book, rule_set.site)
        warnings.extend(rule_warnings)
        if is_hard:
            hard_rules.append(rule.rule_id)

    return CompiledRules(
        rule_set=rule_set,
        book=book,
        hard_rules=hard_rules,
        params=params_by_rule,
        warnings=warnings,
    )
