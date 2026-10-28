"""Independent audit engine.

Design rules (see ``aps_implementation_log.md`` / ACT-09/10):

* no import of ``src.scheduler`` and no use of ``ScheduledTask`` / ``ScheduleResult``;
* the requirement plan is re-derived from master data (orders, stock, BOM, MPQ);
* every duration is re-computed from CT/TRP and the row quantity, never read from
  the solver's ``shift_seconds``;
* a missing task is a failure, not something to skip.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

TaskKey = Tuple[str, str, str]  # (stage, order_id, part_no)


@dataclass(frozen=True)
class PlanRow:
    """One (task, shift, quantity) row as a planner would read it from Excel."""

    task_id: str
    stage: str
    line: str
    part_no: str
    order_id: str
    shift_key: str
    quantity: float


@dataclass(frozen=True)
class ShiftInfo:
    key: str
    index: int
    date_str: str
    shift_name: str
    start_dt: datetime
    end_dt: datetime
    duration_hours: float

    @property
    def duration_seconds(self) -> float:
        return self.duration_hours * 3600.0


@dataclass
class AuditConfig:
    setup_minutes: float = 0.0          # 0: changeover already priced in TRP
    allow_105: bool = False
    max_blocks_per_task: int = 0        # 0: report only, do not fail
    delivery_tolerance_minutes: float = 0.0
    enforce_utilization: bool = True
    min_utilization: float = 0.95
    tolerance: float = 1e-6


@dataclass
class AuditCheck:
    name: str
    passed: bool
    violations: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        parts = [f"{k}={v}" for k, v in self.metrics.items()]
        return ", ".join(parts)


@dataclass
class AuditReport:
    checks: List[AuditCheck]
    expected_tasks: int
    actual_tasks: int
    warnings: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def failed_names(self) -> List[str]:
        return [c.name for c in self.checks if not c.passed]

    def format_text(self, width: int = 78) -> str:
        lines = ["-" * width, "独立审计（不复用求解器对象）".center(width), "-" * width]
        for check in self.checks:
            mark = "PASS" if check.passed else "FAIL"
            suffix = f"  ({check.summary()})" if check.metrics else ""
            lines.append(f"[{mark}] {check.name}{suffix}")
            for violation in check.violations[:6]:
                lines.append(f"        - {violation}")
            if len(check.violations) > 6:
                lines.append(f"        ... 另有 {len(check.violations) - 6} 条")
        lines.append("-" * width)
        failed = self.failed_names()
        lines.append(
            f"独立审计结论: {len(self.checks) - len(failed)}/{len(self.checks)} 通过"
            + (f"；未通过 -> {', '.join(failed)}" if failed else "")
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Requirement plan, re-derived from master data
# ---------------------------------------------------------------------------
def _expected_tasks(
    snapshot: Any,
    orders: Optional[Iterable[Any]] = None,
) -> Tuple[Dict[TaskKey, float], List[str]]:
    """Re-run the netting/explosion independently of ``build_requirement_plan``.

    The arithmetic mirrors the business definition: usable stock = opening stock
    above target stock; finished goods rounded up to whole MPQ batches; a
    semi-finished component only needs to clear its minimum batch, and the
    rounding surplus carries to the next order.
    """
    warnings: List[str] = []
    fg_avail: Dict[str, float] = {
        pn: float(snapshot.initial_stock.get(pn, 0.0)) for pn in snapshot.fg_params
    }
    sf_avail: Dict[str, float] = {
        pn: float(snapshot.initial_stock.get(pn, 0.0)) for pn in snapshot.sf_params
    }
    expected: Dict[TaskKey, float] = {}

    order_list = list(orders) if orders is not None else list(snapshot.orders)
    for order in sorted(order_list, key=lambda o: (o.due_datetime, o.part_no)):
        param = snapshot.fg_params.get(order.part_no)
        if param is None:
            warnings.append(f"{order.order_id}: 成品 {order.part_no} 无主数据，未生成需求")
            continue

        usable = max(0.0, fg_avail[order.part_no] - param.target_stock)
        net_need = order.demand_quantity - usable
        if net_need <= 0:
            fg_avail[order.part_no] -= order.demand_quantity
            continue

        lot = int(round(param.mpq)) or 1
        qty = int(math.ceil(net_need / lot)) * lot
        fg_avail[order.part_no] += qty - order.demand_quantity
        expected[("FG", order.order_id, order.part_no)] = float(qty)

        for sf_pn, ratio in snapshot.bom.get(order.part_no, []):
            sf_param = snapshot.sf_params.get(sf_pn)
            if sf_param is None:
                warnings.append(f"{order.order_id}: 半成品 {sf_pn} 无主数据，未展开")
                continue
            gross = qty * float(ratio)
            sf_usable = max(0.0, sf_avail[sf_pn] - sf_param.target_stock)
            sf_net = gross - sf_usable
            if sf_net <= 0:
                sf_avail[sf_pn] -= gross
                continue
            min_lot = int(round(sf_param.mpq)) or 1
            sf_qty = max(min_lot, int(math.ceil(sf_net)))
            sf_avail[sf_pn] += sf_qty - gross
            expected[("SF", order.order_id, sf_pn)] = float(sf_qty)

    return expected, warnings


def _group_actual(
    rows: Iterable[PlanRow], shift_by_key: Dict[str, ShiftInfo]
) -> Dict[TaskKey, Dict[str, float]]:
    actual: Dict[TaskKey, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        actual[(row.stage, row.order_id, row.part_no)][row.shift_key] += float(row.quantity)
    return actual


def _row_param(row: PlanRow, snapshot: Any):
    if row.stage == "FG":
        return snapshot.fg_params.get(row.part_no)
    return snapshot.sf_params.get(row.part_no)


def _line_loads(
    rows: Iterable[PlanRow],
    shift_by_key: Dict[str, ShiftInfo],
    snapshot: Any,
    cfg: AuditConfig,
) -> Tuple[Dict[Tuple[str, int], float], List[str]]:
    """Processing + setup seconds per (line, shift), recomputed from CT/TRP."""
    loads: Dict[Tuple[str, int], float] = defaultdict(float)
    errors: List[str] = []
    slots: Dict[TaskKey, set] = defaultdict(set)
    line_of_key: Dict[TaskKey, str] = {}

    for row in rows:
        shift = shift_by_key.get(row.shift_key)
        if shift is None:
            continue
        param = _row_param(row, snapshot)
        if param is None:
            errors.append(f"{row.part_no}: 无 CT/TRP 主数据，无法核验工时")
            continue
        if param.trp <= 0:
            errors.append(f"{row.part_no}: TRP={param.trp} 非正，无法核验工时")
            continue
        loads[(row.line, shift.index)] += float(row.quantity) * param.ct_seconds / param.trp
        key = (row.stage, row.order_id, row.part_no)
        slots[key].add(shift.index)
        line_of_key[key] = row.line

    if cfg.setup_minutes > 0:
        # Same convention as the solver: every contiguous run start pays setup.
        for key, indices in slots.items():
            for idx in indices:
                if idx - 1 not in indices:
                    loads[(line_of_key[key], idx)] += cfg.setup_minutes * 60.0
    return loads, errors


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def _check_demand_coverage(
    expected: Dict[TaskKey, float],
    actual: Dict[TaskKey, Dict[str, float]],
    warnings: List[str],
) -> AuditCheck:
    missing = [k for k in expected if k not in actual]
    deficits = [
        (k, expected[k] - sum(actual[k].values()))
        for k in expected
        if k in actual and sum(actual[k].values()) < expected[k] - 1e-6
    ]
    unexpected = [k for k in actual if k not in expected]

    violations = []
    for stage, order_id, part_no in sorted(missing):
        violations.append(f"{stage} {order_id} {part_no}: 需求存在但计划中完全没有该工单（漏排）")
    for (stage, order_id, part_no), gap in sorted(deficits):
        violations.append(f"{stage} {order_id} {part_no}: 排产 {sum(actual[(stage, order_id, part_no)].values()):.0f} 低于净需求 {expected[(stage, order_id, part_no)]:.0f}（欠产 {gap:.0f}）")
    for stage, order_id, part_no in sorted(unexpected):
        violations.append(f"{stage} {order_id} {part_no}: 计划中存在需求里没有的工单")

    return AuditCheck(
        "需求覆盖完整性（每条需求都必须有对应工单）",
        not violations,
        violations,
        {
            "expected": len(expected),
            "scheduled": len(actual),
            "missing": len(missing),
            "deficit": len(deficits),
            "unexpected": len(unexpected),
        },
    )


def _check_rows_valid(
    rows: Iterable[PlanRow],
    shift_by_key: Dict[str, ShiftInfo],
    snapshot: Any,
) -> AuditCheck:
    violations = []
    for row in rows:
        shift = shift_by_key.get(row.shift_key)
        if shift is None:
            violations.append(f"{row.task_id}: 班次 {row.shift_key} 不在日历中")
        if row.quantity <= 0:
            violations.append(f"{row.task_id} @ {row.shift_key}: 数量 {row.quantity} 非正")
        param = _row_param(row, snapshot)
        if param is None:
            violations.append(f"{row.part_no}: 无主数据（无法判断产线/CT/TRP）")
        elif param.line and param.line != row.line:
            violations.append(f"{row.part_no}: 主数据产线 {param.line} 与计划产线 {row.line} 不一致")
    return AuditCheck(
        "排程行有效性（班次存在、数量为正、产线归属正确）",
        not violations,
        violations,
        {"rows": len(list(rows))},
    )


def _check_conservation(
    expected: Dict[TaskKey, float],
    actual: Dict[TaskKey, Dict[str, float]],
    cfg: AuditConfig,
) -> AuditCheck:
    violations = []
    total_expected = 0.0
    total_actual = 0.0
    for key, want in expected.items():
        got = sum(actual.get(key, {}).values())
        total_expected += want
        total_actual += got
        if got < want - cfg.tolerance:
            violations.append(f"{key[0]} {key[1]} {key[2]}: 班次拆解合计 {got:.0f} ≠ 净需求 {want:.0f}")
        elif got > want + cfg.tolerance:
            violations.append(f"{key[0]} {key[1]} {key[2]}: 班次拆解合计 {got:.0f} 超过净需求 {want:.0f}")
    return AuditCheck(
        "班次拆解守恒（各工单分班产量之和 = 净需求）",
        not violations,
        violations,
        {"expected": round(total_expected), "scheduled": round(total_actual), "mismatch": len(violations)},
    )


def _check_contiguity(
    actual: Dict[TaskKey, Dict[str, float]],
    shift_by_key: Dict[str, ShiftInfo],
    cfg: AuditConfig,
) -> AuditCheck:
    violations = []
    max_blocks = 0
    total_blocks = 0
    for key, by_shift in actual.items():
        indices = sorted(
            shift_by_key[k].index for k, qty in by_shift.items() if qty > 0 and k in shift_by_key
        )
        if not indices:
            continue
        blocks = 1 + sum(1 for a, b in zip(indices, indices[1:]) if b != a + 1)
        max_blocks = max(max_blocks, blocks)
        total_blocks += blocks
        if cfg.max_blocks_per_task > 0 and blocks > cfg.max_blocks_per_task:
            violations.append(
                f"{key[0]} {key[1]} {key[2]}: 被拆成 {blocks} 段，超过上限 {cfg.max_blocks_per_task}"
            )
    return AuditCheck(
        "工单连续性（默认仅统计，可配置硬上限）",
        not violations,
        violations,
        {"max_blocks": max_blocks, "total_blocks": total_blocks, "hard_cap": cfg.max_blocks_per_task},
    )


def _check_capacity(
    rows: Iterable[PlanRow],
    shift_by_key: Dict[str, ShiftInfo],
    snapshot: Any,
    cfg: AuditConfig,
) -> AuditCheck:
    loads, errors = _line_loads(rows, shift_by_key, snapshot, cfg)
    violations = list(errors)
    ratio = 1.05 if cfg.allow_105 else 1.0
    for (line, idx), seconds in sorted(loads.items()):
        shift = next((s for s in shift_by_key.values() if s.index == idx), None)
        if shift is None:
            continue
        limit = shift.duration_seconds * ratio
        if seconds > limit + cfg.tolerance:
            violations.append(
                f"{line} {shift.date_str} {shift.shift_name}: 工时 {seconds / 3600:.2f}h 超过 {limit / 3600:.2f}h"
            )
    return AuditCheck(
        "单班物理容量（加工 + 换型 ≤ 班次工时）",
        not violations,
        violations,
        {"line_shifts": len(loads), "overload": len(violations) - len(errors)},
    )


def _check_due(
    rows: Iterable[PlanRow],
    shift_by_key: Dict[str, ShiftInfo],
    snapshot: Any,
    cfg: AuditConfig,
) -> AuditCheck:
    orders = {o.order_id: o for o in snapshot.orders}
    last_slot: Dict[str, int] = {}
    for row in rows:
        shift = shift_by_key.get(row.shift_key)
        if shift is None or row.stage != "FG":
            continue
        last_slot[row.order_id] = max(last_slot.get(row.order_id, -1), shift.index)

    violations = []
    checked = 0
    for order_id, idx in last_slot.items():
        order = orders.get(order_id)
        if order is None:
            continue
        shift = next((s for s in shift_by_key.values() if s.index == idx), None)
        if shift is None:
            continue
        checked += 1
        tolerance = cfg.delivery_tolerance_minutes * 60.0
        if shift.end_dt > order.due_datetime + timedelta(seconds=tolerance):
            delay = (shift.end_dt - order.due_datetime).total_seconds() / 3600.0
            violations.append(
                f"{order_id}: 末班 {shift.date_str} {shift.shift_name} 结束 {shift.end_dt:%m/%d %H:%M} "
                f"晚于交期 {order.due_datetime:%m/%d %H:%M}（+{delay:.1f}h）"
            )
    return AuditCheck(
        "交付达成（按班次结束时间独立重算）",
        not violations,
        violations,
        {"checked": checked, "late": len(violations)},
    )


def _check_material(
    snapshot: Any,
    actual: Dict[TaskKey, Dict[str, float]],
    shift_by_key: Dict[str, ShiftInfo],
    cfg: AuditConfig,
) -> AuditCheck:
    consumed: Dict[str, Dict[int, float]] = defaultdict(lambda: defaultdict(float))
    produced: Dict[str, Dict[int, float]] = defaultdict(lambda: defaultdict(float))

    for (stage, order_id, part_no), by_shift in actual.items():
        for shift_key, qty in by_shift.items():
            shift = shift_by_key.get(shift_key)
            if shift is None or qty <= 0:
                continue
            if stage == "SF":
                produced[part_no][shift.index] += qty
            else:
                for sf_pn, ratio in snapshot.bom.get(part_no, []):
                    consumed[sf_pn][shift.index] += qty * float(ratio)

    violations = []
    parts = sorted(set(consumed) | set(produced))
    min_margin = None
    for part_no in parts:
        stock = float(snapshot.initial_stock.get(part_no, 0.0))
        for idx in range(len(shift_by_key)):
            need = consumed[part_no].get(idx, 0.0)
            margin = stock - need
            if min_margin is None or margin < min_margin:
                min_margin = margin
            if margin < -cfg.tolerance:
                shift = next((s for s in shift_by_key.values() if s.index == idx), None)
                label = f"{shift.date_str} {shift.shift_name}" if shift else f"班次#{idx}"
                violations.append(
                    f"{part_no} @ {label}: 可用 {stock:.0f} < 消耗 {need:.0f}（缺口 {-margin:.0f}）"
                )
            stock += produced[part_no].get(idx, 0.0) - need

    return AuditCheck(
        "物料齐套（前道产出先于后道消耗，独立仿真）",
        not violations,
        violations,
        {"parts": len(parts), "min_margin": round(min_margin) if min_margin is not None else None},
    )


def _check_mpq(
    rows: Iterable[PlanRow],
    snapshot: Any,
    cfg: AuditConfig,
) -> AuditCheck:
    rows = list(rows)
    violations = []
    by_task: Dict[str, List[PlanRow]] = defaultdict(list)
    for row in rows:
        by_task[row.task_id].append(row)

    for task_id, group in by_task.items():
        first = group[0]
        param = _row_param(first, snapshot)
        if param is None:
            continue
        lot = int(round(param.mpq)) or 1
        total = sum(float(row.quantity) for row in group)
        if abs(total - round(total)) > cfg.tolerance:
            violations.append(f"{task_id}: 合计 {total} 不是整数件")
            continue
        # ACT-06: a single lot longer than one shift may be split into a
        # continuous run; the split parts are not separate MPQ batches.
        lot_hours = lot * float(param.ct_seconds) / max(float(param.trp), 1e-9) / 3600.0
        splittable = lot_hours > float(param.hours_per_shift)

        if first.stage == "FG":
            if int(round(total)) % lot != 0:
                violations.append(f"{task_id}: 合计 {total:.0f} 不是 MPQ({lot}) 的整数倍")
            elif not splittable:
                for row in group:
                    if int(round(row.quantity)) % lot != 0:
                        violations.append(
                            f"{task_id} @ {row.shift_key}: {row.quantity:.0f} 不是 MPQ({lot}) 的整数倍"
                        )
        else:
            # SF: only the order total must reach MPQ; a shift may run any
            # positive integer quantity (the plant explicitly allows 1-by-1).
            if total < lot:
                violations.append(f"{task_id}: 合计 {total:.0f} 低于 MPQ({lot})")
    return AuditCheck(
        "MPQ 与整数件（独立重算）",
        not violations,
        violations,
        {"rows": len(rows), "violations": len(violations)},
    )


def _check_utilization(
    rows: Iterable[PlanRow],
    shift_by_key: Dict[str, ShiftInfo],
    snapshot: Any,
    cfg: AuditConfig,
) -> AuditCheck:
    if not cfg.enforce_utilization:
        return AuditCheck("班次利用率（已关闭）", True, [], {})
    loads, _ = _line_loads(rows, shift_by_key, snapshot, cfg)
    by_line: Dict[str, List[Tuple[int, float]]] = defaultdict(list)
    for (line, idx), seconds in loads.items():
        by_line[line].append((idx, seconds))

    violations = []
    opened = 0
    below = 0
    for line, entries in sorted(by_line.items()):
        entries.sort()
        for position, (idx, seconds) in enumerate(entries):
            opened += 1
            if position == len(entries) - 1:
                continue
            shift = next((s for s in shift_by_key.values() if s.index == idx), None)
            if shift is None:
                continue
            if seconds < cfg.min_utilization * shift.duration_seconds - cfg.tolerance:
                below += 1
                violations.append(
                    f"{line} {shift.date_str} {shift.shift_name}: 利用率 {seconds / shift.duration_seconds * 100:.1f}% < {cfg.min_utilization * 100:.0f}%"
                )
    return AuditCheck(
        "班次利用率（尾班豁免，独立重算）",
        not violations,
        violations,
        {"opened": opened, "below": below},
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def audit_schedule(
    snapshot: Any,
    rows: Iterable[PlanRow],
    shifts: Iterable[ShiftInfo],
    config: Optional[AuditConfig] = None,
) -> AuditReport:
    cfg = config or AuditConfig()
    rows = list(rows)
    shift_by_key = {s.key: s for s in shifts}

    expected, warnings = _expected_tasks(snapshot)
    actual = _group_actual(rows, shift_by_key)

    checks = [
        _check_demand_coverage(expected, actual, warnings),
        _check_rows_valid(rows, shift_by_key, snapshot),
        _check_conservation(expected, actual, cfg),
        _check_contiguity(actual, shift_by_key, cfg),
        _check_capacity(rows, shift_by_key, snapshot, cfg),
        _check_due(rows, shift_by_key, snapshot, cfg),
        _check_material(snapshot, actual, shift_by_key, cfg),
        _check_mpq(rows, snapshot, cfg),
        _check_utilization(rows, shift_by_key, snapshot, cfg),
    ]
    return AuditReport(checks=checks, expected_tasks=len(expected), actual_tasks=len(actual), warnings=warnings)


# ---------------------------------------------------------------------------
# Part-level audit (for the manually uploaded PSI sheet, which has no order id)
# ---------------------------------------------------------------------------
def _expected_by_part(expected: Dict[TaskKey, float]) -> Dict[Tuple[str, str], float]:
    out: Dict[Tuple[str, str], float] = defaultdict(float)
    for (stage, _order_id, part_no), qty in expected.items():
        out[(stage, part_no)] += qty
    return out


def _actual_by_part(actual: Dict[TaskKey, Dict[str, float]]) -> Dict[Tuple[str, str], float]:
    out: Dict[Tuple[str, str], float] = defaultdict(float)
    for (stage, _order_id, part_no), by_shift in actual.items():
        out[(stage, part_no)] += sum(by_shift.values())
    return out


def _check_part_coverage(
    expected: Dict[Tuple[str, str], float],
    actual: Dict[Tuple[str, str], float],
) -> AuditCheck:
    missing = [k for k in expected if k not in actual]
    deficits = [(k, expected[k] - actual[k]) for k in expected if k in actual and actual[k] < expected[k] - 1e-6]
    unexpected = [k for k in actual if k not in expected]
    violations = []
    for stage, part_no in sorted(missing):
        violations.append(f"{stage} {part_no}: 净需求存在但手动计划中没有该料号（漏排）")
    for (stage, part_no), gap in sorted(deficits):
        violations.append(f"{stage} {part_no}: 排产 {actual[(stage, part_no)]:.0f} 低于净需求 {expected[(stage, part_no)]:.0f}（欠产 {gap:.0f}）")
    for stage, part_no in sorted(unexpected):
        violations.append(f"{stage} {part_no}: 手动计划中有净需求之外的数量")
    return AuditCheck(
        "需求覆盖完整性（按料号，手工表无订单号）",
        not violations,
        violations,
        {"expected_parts": len(expected), "scheduled_parts": len(actual), "missing": len(missing), "deficit": len(deficits)},
    )


def _check_part_conservation(
    expected: Dict[Tuple[str, str], float],
    actual: Dict[Tuple[str, str], float],
    cfg: AuditConfig,
) -> AuditCheck:
    violations = []
    for key, want in expected.items():
        got = actual.get(key, 0.0)
        if got < want - cfg.tolerance:
            violations.append(f"{key[0]} {key[1]}: 合计 {got:.0f} 低于净需求 {want:.0f}")
        elif got > want + cfg.tolerance:
            violations.append(f"{key[0]} {key[1]}: 合计 {got:.0f} 超过净需求 {want:.0f}")
    return AuditCheck(
        "料号级数量守恒（手工表分班合计 = 净需求）",
        not violations,
        violations,
        {"expected": round(sum(expected.values())), "scheduled": round(sum(actual.values())), "mismatch": len(violations)},
    )


def _check_due_part(
    rows: Iterable[PlanRow],
    shift_by_key: Dict[str, ShiftInfo],
    snapshot: Any,
    cfg: AuditConfig,
    orders: Optional[Iterable[Any]] = None,
) -> AuditCheck:
    """Check due dates at part level using strict FIFO chronological matching against orders (REG-06)."""
    orders_by_part: Dict[str, List[Any]] = defaultdict(list)
    order_list = list(orders) if orders is not None else list(snapshot.orders)
    for order in order_list:
        orders_by_part[order.part_no].append(order)
    for p in orders_by_part:
        orders_by_part[p].sort(key=lambda o: (o.due_datetime, o.order_id))

    rows_by_part: Dict[str, List[Tuple[ShiftInfo, PlanRow]]] = defaultdict(list)
    for row in rows:
        if row.stage != "FG" or row.quantity <= 0:
            continue
        shift = shift_by_key.get(row.shift_key)
        if shift is None:
            continue
        rows_by_part[row.part_no].append((shift, row))
    for p in rows_by_part:
        rows_by_part[p].sort(key=lambda item: item[0].end_dt)

    violations = []
    tolerance = cfg.delivery_tolerance_minutes * 60.0
    checked_orders = 0

    for part_no, p_orders in orders_by_part.items():
        p_rows = rows_by_part.get(part_no, [])
        opening_qty = float(snapshot.initial_stock.get(part_no, 0.0))

        # Build chronological supply events: [available_dt, qty]
        supply_events: List[List[Any]] = []
        if opening_qty > 0:
            earliest_dt = min((s.start_dt for s in shift_by_key.values()), default=p_orders[0].due_datetime)
            supply_events.append([earliest_dt, opening_qty])
        for shift, row in p_rows:
            supply_events.append([shift.end_dt, float(row.quantity)])

        supply_idx = 0
        for order in p_orders:
            checked_orders += 1
            rem_order_qty = float(order.demand_quantity)
            latest_fulfill_dt = None

            while rem_order_qty > 0 and supply_idx < len(supply_events):
                supp_dt, supp_avail = supply_events[supply_idx]
                if supp_avail <= 0:
                    supply_idx += 1
                    continue
                alloc = min(rem_order_qty, supp_avail)
                rem_order_qty -= alloc
                supply_events[supply_idx][1] -= alloc
                latest_fulfill_dt = supp_dt
                if supply_events[supply_idx][1] <= 1e-6:
                    supply_idx += 1

            if rem_order_qty > 1e-6:
                violations.append(
                    f"{part_no} (订单 {order.order_id}): 需求 {order.demand_quantity:.0f} 欠产 {rem_order_qty:.0f}"
                )
            elif latest_fulfill_dt and latest_fulfill_dt > order.due_datetime + timedelta(seconds=tolerance):
                delay = (latest_fulfill_dt - order.due_datetime).total_seconds() / 3600.0
                violations.append(
                    f"{part_no} (订单 {order.order_id}): 需求 {order.demand_quantity:.0f} 交付时点 "
                    f"{latest_fulfill_dt:%m/%d %H:%M} 晚于交期 {order.due_datetime:%m/%d %H:%M}（+{delay:.1f}h）"
                )

    return AuditCheck(
        "交付达成（按料号时序 FIFO 订单逐笔核算）",
        not violations,
        violations,
        {"checked_orders": checked_orders, "late": len(violations)},
    )


def audit_schedule_part_level(
    snapshot: Any,
    rows: Iterable[PlanRow],
    shifts: Iterable[ShiftInfo],
    config: Optional[AuditConfig] = None,
    demand_scope_orders: Optional[Iterable[Any]] = None,
    horizon_end: Optional[datetime] = None,
) -> AuditReport:
    """Audit a part-level plan (the manually edited PSI sheet has no order ids).

    Aligns demand scope with the scheduling horizon (REG-07) so orders beyond
    the schedule window do not produce false under-production (欠产) failures.
    """
    cfg = config or AuditConfig()
    rows = list(rows)
    shift_by_key = {s.key: s for s in shifts}

    if demand_scope_orders is None:
        if horizon_end is None and shifts:
            horizon_end = max((s.end_dt for s in shifts), default=None)
        if horizon_end is not None:
            demand_scope_orders = [
                o for o in snapshot.orders
                if o.due_datetime <= horizon_end + timedelta(hours=2)
            ]
        else:
            demand_scope_orders = list(snapshot.orders)

    expected_order, warnings = _expected_tasks(snapshot, orders=demand_scope_orders)
    expected = _expected_by_part(expected_order)
    actual_all = _group_actual(rows, shift_by_key)
    actual = _actual_by_part(actual_all)

    checks = [
        _check_part_coverage(expected, actual),
        _check_rows_valid(rows, shift_by_key, snapshot),
        _check_part_conservation(expected, actual, cfg),
        _check_contiguity(actual_all, shift_by_key, cfg),
        _check_capacity(rows, shift_by_key, snapshot, cfg),
        _check_due_part(rows, shift_by_key, snapshot, cfg, orders=demand_scope_orders),
        _check_material(snapshot, actual_all, shift_by_key, cfg),
        _check_mpq(rows, snapshot, cfg),
        _check_utilization(rows, shift_by_key, snapshot, cfg),
    ]
    return AuditReport(checks=checks, expected_tasks=len(expected), actual_tasks=len(actual), warnings=warnings)
