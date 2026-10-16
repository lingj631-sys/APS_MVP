"""
Independent re-verification of a solved schedule.

The validator recomputes every WE34 hard constraint straight from the
requirement plan and the exported shift breakdown, so a modelling mistake in
the solver cannot hide behind a solver-reported success.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from src.data_loader import RequirementPlan
from src.scheduler import ScheduledTask, ScheduleResult
from src.shift_manager import MIN_SHIFT_UTILIZATION, SHIFT_SECONDS


@dataclass
class Check:
    name: str
    passed: bool
    violations: List[str] = field(default_factory=list)


def validate_schedule(plan: RequirementPlan, result: ScheduleResult) -> List[Check]:
    index_of = {(s.date_str, s.shift_name): s.index for s in result.shifts}
    return [
        _check_capacity(result.tasks, index_of),
        _check_utilization(result.tasks, index_of),
        _check_conservation(plan, result.tasks),
        _check_fg_mpq(plan, result.tasks),
        _check_sf_minimum(plan, result.tasks),
        _check_material_balance(plan, result.tasks, index_of),
        _check_stock_balance(plan),
        _check_delivery(plan, result.tasks),
    ]


def _loads(tasks, index_of) -> Dict[Tuple[str, int], float]:
    loads: Dict[Tuple[str, int], float] = {}
    for task in tasks:
        for key, seconds in task.shift_seconds.items():
            loads[(task.line, index_of[key])] = loads.get((task.line, index_of[key]), 0.0) + seconds
    return loads


def _check_capacity(tasks, index_of) -> Check:
    violations = [
        f"{line} shift#{idx} 负荷 {seconds / 3600:.2f}h 超出 8h 班次上限"
        for (line, idx), seconds in sorted(_loads(tasks, index_of).items())
        if seconds > SHIFT_SECONDS + 1e-6
    ]
    return Check("班次产能不超 8 小时", not violations, violations)


def _check_utilization(tasks, index_of) -> Check:
    label_of = {idx: f"{date_str} {shift_name}" for (date_str, shift_name), idx in index_of.items()}
    loads = _loads(tasks, index_of)
    by_line: Dict[str, List[Tuple[int, float]]] = {}
    for (line, idx), seconds in loads.items():
        by_line.setdefault(line, []).append((idx, seconds))

    offenders = []
    for line, entries in by_line.items():
        entries.sort()
        for position, (idx, seconds) in enumerate(entries):
            utilization = seconds / SHIFT_SECONDS
            if utilization >= MIN_SHIFT_UTILIZATION - 1e-9:
                continue
            if position == len(entries) - 1:
                continue  # the closing partial shift is exempt by design
            offenders.append((utilization, line, idx, seconds))

    violations = [
        f"{line} {label_of[idx]} 利用率 {utilization * 100:.1f}% < 95%（投入 {seconds / 3600:.2f}h）"
        for utilization, line, idx, seconds in sorted(offenders)
    ]
    return Check("班次产能利用率 ≥ 95%（尾班豁免）", not violations, violations)


def _check_conservation(plan, tasks) -> Check:
    violations = []
    planned = {t.task_id: t for t in plan.tasks}
    for task in tasks:
        source = planned.get(task.task_id)
        if source is None:
            violations.append(f"{task.task_id}: 排产结果中存在需求计划里没有的任务")
            continue
        if abs(task.quantity - source.quantity) > 1e-6 or task.lots != source.lots:
            violations.append(
                f"{task.task_id}: 排产量 {task.quantity:.0f} 与净需求 {source.quantity:.0f} 不一致"
            )
        if sum(task.shift_breakdown.values()) != int(round(task.quantity)):
            violations.append(
                f"{task.task_id}: 班次合计 {sum(task.shift_breakdown.values())} 与总产量 "
                f"{task.quantity:.0f} 不一致"
            )
    return Check("排产量等于净需求且班次产出守恒", not violations, violations)


def _check_fg_mpq(plan, tasks) -> Check:
    """Whole MPQ batches, in total and in every individual shift."""
    violations = []
    for task in (t for t in tasks if t.stage == "FG"):
        if int(round(task.quantity)) % task.mpq:
            violations.append(f"{task.task_id}: 合计 {task.quantity:.0f} 不是 MPQ({task.mpq}) 的整数倍")
        for key, qty in task.shift_breakdown.items():
            if qty % task.mpq:
                violations.append(f"{task.task_id} @ {key}: 班次产量 {qty} 不是 MPQ({task.mpq}) 的整数倍")
    return Check("成品按 MPQ 整数倍排产", not violations, violations)


def _check_sf_minimum(plan, tasks) -> Check:
    """Semi-finished skips the MPQ rounding but every start runs a full MPQ."""
    violations = []
    for task in (t for t in tasks if t.stage == "SF"):
        if not float(task.quantity).is_integer():
            violations.append(f"{task.task_id}: 合计 {task.quantity} 不是整数件")
        if int(round(task.quantity)) < task.mpq:
            violations.append(
                f"{task.task_id}: 合计 {task.quantity:.0f} 低于最小投产批量 MPQ({task.mpq})"
            )
        for key, qty in task.shift_breakdown.items():
            if int(qty) != qty:
                violations.append(f"{task.task_id} @ {key}: 班次产量 {qty} 不是整数件")
            elif qty < task.mpq:
                violations.append(
                    f"{task.task_id} @ {key}: 班次投产 {qty} 件低于最小投产批量 MPQ({task.mpq})"
                )
    return Check("半成品为整数件且单班投产 ≥ MPQ", not violations, violations)


def _check_material_balance(plan, tasks, index_of) -> Check:
    """Cumulative bushings available must cover cumulative assembly consumption."""
    by_id = {t.task_id: t for t in tasks}
    violations = []
    for fg_task in plan.fg_tasks:
        assembled = by_id[fg_task.task_id]
        components = [c for c in plan.sf_tasks if c.parent_task_id == fg_task.task_id]
        for component in components:
            produced = by_id[component.task_id]
            for shift_index in range(max(index_of.values()) + 1):
                made_before = sum(
                    qty for key, qty in produced.shift_breakdown.items()
                    if index_of[key] < shift_index
                )
                consumed_upto = sum(
                    qty for key, qty in assembled.shift_breakdown.items()
                    if index_of[key] <= shift_index
                )
                required = consumed_upto * component.bom_ratio
                available = component.stock_covered + made_before
                if available < required:
                    violations.append(
                        f"{component.part_no} → {fg_task.part_no} 第 {shift_index} 班次"
                        f" 可用 {available:.0f} < 已装配耗用 {required:.0f}"
                    )
                    break
    return Check("上下工序按班次累计齐套", not violations, violations)


def _check_stock_balance(plan) -> Check:
    violations = []
    for movement in plan.stock_movements:
        expected = movement.opening + movement.produced - movement.consumed
        if abs(expected - movement.closing) > 1e-6:
            violations.append(
                f"{movement.part_no}: 期初 {movement.opening:.0f} + 产出 {movement.produced:.0f} "
                f"- 耗用 {movement.consumed:.0f} = {expected:.0f} 与结余 {movement.closing:.0f} 不符"
            )
        if movement.closing < movement.target_stock - 1e-6:
            violations.append(
                f"{movement.part_no}: 期末结余 {movement.closing:.0f} 低于安全库存 "
                f"{movement.target_stock:.0f}"
            )
    return Check("库存流水账平衡且不低于安全库存", not violations, violations)


def _check_delivery(plan, tasks) -> Check:
    by_id = {t.task_id: t for t in tasks}
    violations = []
    late_count = 0
    for fg_task in plan.fg_tasks:
        scheduled = by_id[fg_task.task_id]
        if scheduled.end_dt > fg_task.due_datetime:
            late_count += 1
            delay_hours = (scheduled.end_dt - fg_task.due_datetime).total_seconds() / 3600.0
            violations.append(
                f"{fg_task.order_id}: {fg_task.part_no} 完工 {scheduled.end_dt:%m/%d %H:%M} "
                f"晚于需求时点 {fg_task.due_datetime:%m/%d %H:%M}（+{delay_hours:.1f}h）"
            )
    total = len(plan.fg_tasks)
    on_time = total - late_count
    return Check(
        f"准时交付（{on_time}/{total}，准时率 {on_time / total * 100:.1f}%）" if total else "准时交付",
        not violations,
        violations,
    )


def format_report(checks: List[Check]) -> str:
    lines = ["-" * 70, "                     排产结果校验报告                     ", "-" * 70]
    for check in checks:
        mark = "PASS [OK]" if check.passed else "FAIL [!!]"
        lines.append(f"{mark:<11} {check.name}")
        for detail in check.violations[:12]:
            lines.append(f"            - {detail}")
        if len(check.violations) > 12:
            lines.append(f"            ... 另有 {len(check.violations) - 12} 条")
    failed = [c.name for c in checks if not c.passed]
    lines.append("-" * 70)
    lines.append(
        f"校验结论: {len(checks) - len(failed)}/{len(checks)} 项通过"
        + (f"；未通过 -> {', '.join(failed)}" if failed else "")
    )
    return "\n".join(lines)
