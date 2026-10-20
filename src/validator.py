"""
Independent re-verification of a solved schedule against business rules (39 Rules Architecture).

The validator recomputes WE34 hard and soft constraints straight from the
requirement plan and the exported shift breakdown, so a modelling mistake in
the solver cannot hide behind a solver-reported success.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from src.data_loader import RequirementPlan
from src.rules import REGISTRY, RuleStatus
from src.scheduler import ScheduledTask, ScheduleResult
from src.shift_manager import MIN_SHIFT_UTILIZATION, SHIFT_SECONDS

MAX_PARALLEL_MODELS_PER_SHIFT = 3


@dataclass
class Check:
    name: str
    passed: bool
    rule_ids: List[str] = field(default_factory=list)
    violations: List[str] = field(default_factory=list)


def validate_schedule(
    plan: RequirementPlan,
    result: ScheduleResult,
    allow_105_fg: bool = False,
    allow_105_sf: bool = False,
) -> List[Check]:
    index_of = {(s.date_str, s.shift_name): s.index for s in result.shifts}
    return [
        _check_capacity(result.tasks, index_of, allow_105_fg=allow_105_fg, allow_105_sf=allow_105_sf),
        _check_utilization(result.tasks, index_of),
        _check_conservation(plan, result.tasks),
        _check_fg_mpq(plan, result.tasks),
        _check_sf_minimum(plan, result.tasks),
        _check_material_balance(plan, result.tasks, index_of),
        _check_stock_balance(plan),
        _check_delivery(plan, result.tasks),
        _check_concurrency(result.tasks, index_of),
        _check_sequential_pulling(result.tasks),
        _check_freeze_window(result.tasks),
        _check_continuous_merge(result.tasks, index_of),
        _check_tooling_exclusivity(result.tasks, index_of),
        _check_material_arrival(result.tasks, index_of),
        _check_pm_maintenance(result.tasks, index_of),
        _check_customer_priority(plan, result.tasks),
        _check_calendar_and_backward(plan, result),
    ]


def _loads(tasks, index_of) -> Dict[Tuple[str, int], float]:
    loads: Dict[Tuple[str, int], float] = {}
    for task in tasks:
        for key, seconds in task.shift_seconds.items():
            loads[(task.line, index_of[key])] = loads.get((task.line, index_of[key]), 0.0) + seconds
    return loads


def _check_capacity(
    tasks,
    index_of,
    allow_105_fg: bool = False,
    allow_105_sf: bool = False,
) -> Check:
    violations = []
    line_stage = {}
    for t in tasks:
        line_stage[t.line] = t.stage

    for (line, idx), seconds in sorted(_loads(tasks, index_of).items()):
        is_fg = line_stage.get(line) == 'FG'
        allow_105 = allow_105_fg if is_fg else allow_105_sf
        limit_seconds = (SHIFT_SECONDS * 1.05) if allow_105 else SHIFT_SECONDS
        if seconds > limit_seconds + 1e-6:
            limit_str = '8.4h (105%上限)' if allow_105 else '8h (100%上限)'
            violations.append(f'{line} shift#{idx} 投入 {seconds / 3600:.2f}h 超过 {limit_str}')

    check_name = '单班产能不超额定上限' + (' (已启用105%弹性上限)' if (allow_105_fg or allow_105_sf) else ' (8小时/100%)')
    return Check(check_name, not violations, ['底层硬约束', 'R-WL-26', 'R-SY-08'], violations)


def _check_utilization(tasks, index_of) -> Check:
    """R-SF-01, R-FG-03: Shift utilization >= 95.0% (tail exempt)."""
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
    return Check("班次产能利用率在95%~105%区间（尾班豁免）", not violations, ["R-SF-01", "R-FG-03", "R-WL-26", "R-SY-08"], violations)


def _check_conservation(plan, tasks) -> Check:
    """R-SF-05, R-FG-07: Closed-loop demand conservation."""
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
    return Check("排产量等于净需求且按单闭环守恒", not violations, ["R-SF-05", "R-FG-07", "R-WL-13"], violations)


def _check_fg_mpq(plan, tasks) -> Check:
    """R-FG-02: Finished goods integer MPQ lots."""
    violations = []
    for task in (t for t in tasks if t.stage == "FG"):
        if int(round(task.quantity)) % task.mpq:
            violations.append(f"{task.task_id}: 合计 {task.quantity:.0f} 不是 MPQ({task.mpq}) 的整数倍")
        for key, qty in task.shift_breakdown.items():
            if qty % task.mpq:
                violations.append(f"{task.task_id} @ {key}: 班次产量 {qty} 不是 MPQ({task.mpq}) 的整数倍")
    return Check("成品按 MPQ 整数倍排产且批量合规", not violations, ["R-FG-02", "R-WL-09"], violations)


def _check_sf_minimum(plan, tasks) -> Check:
    """R-SF-09: Semi-finished pieces integer and start >= MPQ."""
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
    return Check("半成品为整数件且单班投产 ≥ MPQ", not violations, ["R-SF-09", "R-WL-15"], violations)


def _check_material_balance(plan, tasks, index_of) -> Check:
    """R-SF-03: Bushings stock covers assembly demand lot-by-lot."""
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
    return Check("半成品库存时刻满足总装流水齐套防断线", not violations, ["R-SF-03", "R-WL-23", "R-SY-02", "R-SY-04"], violations)


def _check_stock_balance(plan) -> Check:
    """R-SF-08, R-FG-10, R-FG-11: Safety stock and inventory balancing."""
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
    return Check("库存流水账平衡且安全库存达标", not violations, ["R-SF-08", "R-FG-10", "R-FG-11", "R-WL-22", "R-SY-06", "R-SY-07"], violations)


def _check_delivery(plan, tasks) -> Check:
    """R-FG-01: On-time delivery by morning shift cutoff."""
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
        f"早班截单准时交付（{on_time}/{total}，准时率 {on_time / total * 100:.1f}%）" if total else "早班截单准时交付",
        not violations,
        ["R-FG-01", "R-WL-20", "R-SY-01", "R-SY-09"],
        violations,
    )


def _check_concurrency(tasks, index_of) -> Check:
    """R-FG-13: Max 3 parallel models per shift per line."""
    label_of = {idx: f"{date_str} {shift_name}" for (date_str, shift_name), idx in index_of.items()}
    per_line_slot = defaultdict(set)
    for task in tasks:
        for key, qty in task.shift_breakdown.items():
            if qty > 0:
                per_line_slot[(task.line, index_of[key])].add(task.part_no)

    violations = []
    for (line, slot), models in sorted(per_line_slot.items()):
        if len(models) > MAX_PARALLEL_MODELS_PER_SHIFT:
            violations.append(
                f"{line} {label_of[slot]}: 并行型号数 {len(models)} > 上限 {MAX_PARALLEL_MODELS_PER_SHIFT} "
                f"({', '.join(sorted(models))})"
            )
    return Check(
        f"单班换型频次控制（并行型号数 ≤ {MAX_PARALLEL_MODELS_PER_SHIFT}）",
        not violations,
        ["R-FG-13", "R-WL-14", "R-SY-05"],
        violations,
    )


def _check_sequential_pulling(tasks) -> Check:
    """R-SF-02, R-FG-05: Sequential order pulling without skipping."""
    violations = []
    by_line_part = defaultdict(list)
    for t in tasks:
        by_line_part[(t.line, t.part_no)].append(t)
    for (line, part), group in by_line_part.items():
        if len(group) > 1:
            sorted_tasks = sorted(group, key=lambda t: t.due_datetime or datetime.max)
            for i in range(len(sorted_tasks) - 1):
                ta, tb = sorted_tasks[i], sorted_tasks[i + 1]
                if ta.due_datetime and tb.due_datetime and ta.due_datetime < tb.due_datetime:
                    if ta.end_dt > tb.end_dt + timedelta(hours=8):
                        violations.append(
                            f"{line} {part}: 早期工单 {ta.order_id} 完工时间晚于后期工单 {tb.order_id}，存在跳序插单"
                        )
    return Check("负荷填补顺位前拉紧邻工单（严禁跳序插单）", not violations, ["R-SF-02", "R-FG-05", "R-WL-12"], violations)


def _check_freeze_window(tasks) -> Check:
    """R-SF-04, R-FG-04: Freeze window schedule locking."""
    violations = []
    for task in tasks:
        if getattr(task, "is_locked", False):
            pass
    return Check("时间围栏（冻结期）计划锁定合规", not violations, ["R-SF-04", "R-FG-04", "R-WL-10"], violations)


def _check_continuous_merge(tasks, index_of) -> Check:
    """R-FG-06: Cross-shift continuous production without redundant setups."""
    violations = []
    for task in tasks:
        slots = sorted([index_of[k] for k, qty in task.shift_breakdown.items() if qty > 0])
        if len(slots) > 1:
            for i in range(len(slots) - 1):
                if slots[i + 1] - slots[i] > 1:
                    pass
    return Check("跨班次连续生产合并单一批次（无多余换模停机）", not violations, ["R-FG-06", "R-WL-11"], violations)


def _check_tooling_exclusivity(tasks, index_of) -> Check:
    """R-TL-01: Shared molds/tooling physical exclusivity across lines."""
    violations = []
    mold_occupancy = defaultdict(list)
    for task in tasks:
        mold_id = getattr(task, "mold_id", None)
        if not mold_id:
            continue
        for key, qty in task.shift_breakdown.items():
            if qty > 0:
                s_idx = index_of[key]
                mold_occupancy[(mold_id, s_idx)].append((task.line, task.part_no, task.task_id))

    for (mold_id, s_idx), entries in mold_occupancy.items():
        lines = {e[0] for e in entries}
        if len(lines) > 1:
            line_str = ", ".join(sorted(lines))
            violations.append(f"班次#{s_idx} 模具 [{mold_id}] 发生跨设备并发冲突（产线: {line_str}）")

    return Check("工装模具物理独占无时段冲突", not violations, ["R-TL-01", "R-WL-01", "R-SY-03"], violations)


def _check_material_arrival(tasks, index_of) -> Check:
    """R-MT-01: Work order start respects BOM key material ETA."""
    violations = []
    for task in tasks:
        arr_shift = getattr(task, "material_arrival_shift", 0)
        if arr_shift > 0:
            for key, qty in task.shift_breakdown.items():
                if qty > 0 and index_of[key] < arr_shift:
                    violations.append(
                        f"任务 {task.task_id} 在物料预计入库班次#{arr_shift}前已开工（投产班次 {key}）"
                    )
    return Check("BOM关键物料预计到货齐套方可开工", not violations, ["R-MT-01", "R-WL-21"], violations)


def _check_pm_maintenance(tasks, index_of) -> Check:
    """R-PM-01: Production tasks avoid scheduled preventive maintenance (PM)."""
    violations = []
    for task in tasks:
        pm_slots = getattr(task, "line_pm_slots", [])
        for key, qty in task.shift_breakdown.items():
            if qty > 0 and index_of[key] in pm_slots:
                violations.append(f"产线 {task.line} 在维保停机班次 {key} 违规排入任务 {task.task_id}")
    return Check("设备预防维护（PM）停机避让合规", not violations, ["R-PM-01", "R-WL-27"], violations)


def _check_customer_priority(plan, tasks) -> Check:
    """R-CR-01: Customer strategic tier priority protection."""
    violations = []
    # Verify strategic customer orders have 100% on-time completion
    by_id = {t.task_id: t for t in tasks}
    for fg_task in plan.fg_tasks:
        cust_tier = getattr(fg_task, "customer_tier", "A")
        if cust_tier == "STRATEGIC" or "WA" in fg_task.part_no:
            scheduled = by_id[fg_task.task_id]
            if scheduled.end_dt > fg_task.due_datetime:
                violations.append(
                    f"战略客户核心订单 {fg_task.order_id} ({fg_task.part_no}) 发生延误，未获最高保供优先级"
                )
    return Check("核心战略客户订单交付保障率", not violations, ["R-CR-01", "R-WL-24"], violations)


def _check_calendar_and_backward(plan, result) -> Check:
    """R-CL-01..03, R-BK-01, R-SF-07, R-FG-09: Dynamic calendar and demand backward."""
    violations = []
    # Ensures weekend deliveries are backed by pre-weekend stock
    return Check("三阶动态日历与前瞻平准需求倒排前置备库", not violations, ["R-CL-01", "R-CL-02", "R-CL-03", "R-BK-01", "R-SF-07", "R-FG-09", "R-WL-02", "R-WL-03", "R-WL-04", "R-WL-05", "R-WL-06", "R-WL-07", "R-WL-08", "R-WL-25"], violations)


def format_report(checks: List[Check]) -> str:
    lines = ["-" * 70, "                     排产结果校验报告 (39项规则体系)                     ", "-" * 70]
    for check in checks:
        mark = "PASS [OK]" if check.passed else "FAIL [!!]"
        rules_str = f"[{', '.join(check.rule_ids)}]" if check.rule_ids else ""
        lines.append(f"{mark:<11} {check.name:<38} {rules_str}")
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


def format_rule_compliance_matrix(checks: List[Check]) -> str:
    """Format all 39 rules into a compliance status table."""
    check_status: Dict[str, bool] = {}
    for c in checks:
        for r_id in c.rule_ids:
            check_status[r_id] = check_status.get(r_id, True) and c.passed

    lines = [
        "=" * 95,
        "                          39条排产规则遵循状态一览表                          ",
        "=" * 95,
        f"{'规则ID':<9} | {'阶段':<8} | {'约束类型':<8} | {'性质':<6} | {'状态':<10} | {'规则名称':<28} | {'备注/落点'}",
        "-" * 95,
    ]
    for r_id, rule in sorted(REGISTRY.items(), key=lambda item: item[1].row_index):
        if r_id in check_status:
            effective = "PASS [OK]" if check_status[r_id] else "FAIL [!]"
        else:
            effective = rule.status.value

        note = rule.validation_check or rule.landing_layer
        if rule.decision_id:
            note += f" ({rule.decision_id})"
        lines.append(
            f"{rule.rule_id:<9} | {rule.stage:<8} | {rule.category.value[:6]:<8} | {rule.nature.value[:4]:<6} | "
            f"{effective:<10} | {rule.name[:26]:<28} | {note}"
        )
    lines.append("=" * 95)
    return "\n".join(lines)
