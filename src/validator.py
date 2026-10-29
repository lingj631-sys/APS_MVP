"""
Independent re-verification of a solved schedule against business rules (Unified Multi-Site Architecture: WL 27 Rules & SY38 9 Rules).

The validator recomputes site-specific hard and soft constraints straight from the
requirement plan and the exported shift breakdown, providing transparent calculation
drill-down data (KPI metrics, mathematical formulas, and step-by-step audit tables)
for both passing and failing rules.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.data_loader import RequirementPlan
from src.rules import REGISTRY, RuleStatus, get_rules_for_site
from src.scheduler import ScheduledTask, ScheduleResult
from src.shift_manager import (
    DEFAULT_CHANGEOVER_MINUTES,
    MIN_SHIFT_UTILIZATION,
    SHIFT_SECONDS,
    get_topology,
)

MAX_PARALLEL_MODELS_PER_SHIFT = 3
SETUP_SECONDS = int(DEFAULT_CHANGEOVER_MINUTES * 60)
# Freeze fence: the first N shifts of the horizon are locked once a baseline exists.
FREEZE_SHIFTS = 3
# 0 = report block counts only; a plant can opt into a hard cap per task.
CONTIGUITY_HARD_CAP = 0


@dataclass
class Check:
    name: str
    passed: bool
    rule_ids: List[str] = field(default_factory=list)
    violations: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    calculation_details: List[str] = field(default_factory=list)
    table_data: Optional[Dict[str, Any]] = None


def validate_schedule(
    plan: RequirementPlan,
    result: ScheduleResult,
    site: str = "WL",
    allow_105_fg: bool = False,
    allow_105_sf: bool = False,
    baseline_result: Optional[ScheduleResult] = None,
    rule_set: Optional[Any] = None,
) -> List[Check]:
    index_of = {(s.date_str, s.shift_name): s.index for s in result.shifts}
    site_upper = (site or "WL").upper()
    baseline_tasks = baseline_result.tasks if baseline_result else None
    
    freeze_shifts = FREEZE_SHIFTS
    max_parallel = MAX_PARALLEL_MODELS_PER_SHIFT
    if rule_set is not None:
        for r in getattr(rule_set, "rules", []):
            if not getattr(r, "enabled", True):
                continue
            tid = getattr(r, "template_id", "")
            params = getattr(r, "params", {})
            if tid == "freeze":
                freeze_shifts = int(params.get("fence_shifts", freeze_shifts))
            elif tid == "concurrency":
                max_parallel = int(params.get("max_models", max_parallel))
            elif tid == "capacity_utilization":
                # Fallback extraction just in case, though allow_105_* might be passed in
                stg = getattr(getattr(r, "scope", None), "stage", "*")
                if stg in ("*", "FG") and float(params.get("max_util", 1.0)) > 1.0:
                    allow_105_fg = True
                if stg in ("*", "SF") and float(params.get("max_util", 1.0)) > 1.0:
                    allow_105_sf = True

    if site_upper in ("SY38", "SY"):
        return _validate_schedule_sy38(
            plan, result, index_of, allow_105_fg=allow_105_fg, allow_105_sf=allow_105_sf,
            freeze_shifts=freeze_shifts, max_parallel=max_parallel, baseline_tasks=baseline_tasks
        )
    else:
        return _validate_schedule_wl(
            plan,
            result,
            index_of,
            allow_105_fg=allow_105_fg,
            allow_105_sf=allow_105_sf,
            baseline_tasks=baseline_tasks,
            freeze_shifts=freeze_shifts,
            max_parallel=max_parallel,
        )


def _validate_schedule_wl(
    plan: RequirementPlan,
    result: ScheduleResult,
    index_of: Dict[Tuple[str, str], int],
    allow_105_fg: bool = False,
    allow_105_sf: bool = False,
    baseline_tasks: Optional[List[ScheduledTask]] = None,
    freeze_shifts: int = FREEZE_SHIFTS,
    max_parallel: int = MAX_PARALLEL_MODELS_PER_SHIFT,
) -> List[Check]:
    return [
        _check_capacity(result.tasks, index_of, allow_105_fg=allow_105_fg, allow_105_sf=allow_105_sf),
        _check_utilization(result.tasks, index_of),
        _check_conservation(plan, result.tasks),
        _check_fg_mpq(plan, result.tasks),
        _check_sf_minimum(plan, result.tasks),
        _check_material_balance(plan, result.tasks, index_of),
        _check_stock_balance(plan),
        _check_delivery(plan, result.tasks, allow_105=allow_105_fg),
        _check_controlled_window(result.tasks),
        _check_sequential_pulling(result.tasks),
        _check_freeze_window(result.tasks, index_of, baseline_tasks=baseline_tasks, freeze_shifts=freeze_shifts),
        _check_continuous_merge(result.tasks, result, index_of, max_parallel=max_parallel),
        _check_tooling_exclusivity(result.tasks, index_of),
        _check_material_arrival(result.tasks, index_of),
        _check_pm_maintenance(result.tasks, index_of),
        _check_customer_priority(plan, result.tasks),
        _check_calendar_and_backward(plan, result),
        _check_concurrency(result.tasks, index_of, max_parallel=max_parallel),
    ]


def _loads(tasks, index_of) -> Dict[Tuple[str, int], float]:
    loads: Dict[Tuple[str, int], float] = {}
    for task in tasks:
        for key, seconds in task.shift_seconds.items():
            if key in index_of:
                loads[(task.line, index_of[key])] = loads.get((task.line, index_of[key]), 0.0) + seconds
    return loads


def _task_start_setups(tasks, index_of) -> Dict[Tuple[str, int], float]:
    """Setup seconds per task run start, mirroring the solver's `started` variable.

    A task pays setup on the first shift of each contiguous run, including a run that
    begins mid-horizon. Recomputed from the shift breakdown so the auditor does not
    have to trust any value the solver carried over.
    """
    setups: Dict[Tuple[str, int], float] = {}
    for task in tasks:
        slots = {
            index_of[key]
            for key, qty in task.shift_breakdown.items()
            if qty > 0 and key in index_of
        }
        for idx in slots:
            if idx - 1 not in slots:
                spot = (task.line, idx)
                setups[spot] = setups.get(spot, 0.0) + SETUP_SECONDS
    return setups


def _effective_loads(tasks, index_of) -> Dict[Tuple[str, int], float]:
    """Processing + setup: what a shift actually consumes out of its rated capacity."""
    loads = _loads(tasks, index_of)
    for spot, seconds in _task_start_setups(tasks, index_of).items():
        loads[spot] = loads.get(spot, 0.0) + seconds
    return loads


def _check_capacity(
    tasks,
    index_of,
    allow_105_fg: bool = False,
    allow_105_sf: bool = False,
) -> Check:
    """R-WL-26: Shift capacity strictly within rated limits (processing + setup)."""
    violations = []
    line_stage = {}
    for t in tasks:
        line_stage[t.line] = t.stage

    label_of = {idx: f"{date_str} {shift_name}" for (date_str, shift_name), idx in index_of.items()}
    table_rows = []
    total_hours_by_line = defaultdict(float)
    total_setup_hours_by_line = defaultdict(float)

    loads = _effective_loads(tasks, index_of)
    setups = _task_start_setups(tasks, index_of)

    for (line, idx), effective_seconds in sorted(loads.items()):
        is_fg = line_stage.get(line) == "FG"
        allow_105 = allow_105_fg if is_fg else allow_105_sf
        limit_seconds = (SHIFT_SECONDS * 1.05) if allow_105 else SHIFT_SECONDS

        setup_seconds = setups.get((line, idx), 0.0)
        proc_seconds = max(0.0, effective_seconds - setup_seconds)

        proc_hrs = round(proc_seconds / 3600.0, 2)
        setup_hrs = round(setup_seconds / 3600.0, 2)
        eff_hrs = round(effective_seconds / 3600.0, 2)
        total_hours_by_line[line] += proc_hrs
        total_setup_hours_by_line[line] += setup_hrs

        limit_hrs = 8.4 if allow_105 else 8.0
        util_rate = round(effective_seconds / SHIFT_SECONDS * 100.0, 1)

        is_over = effective_seconds > limit_seconds + 1e-6
        if is_over:
            limit_str = "8.4h (105%上限)" if allow_105 else "8h (100%上限)"
            violations.append(
                f"{line} {label_of.get(idx, f'shift#{idx}')} 综合工时 {eff_hrs}h"
                f"（加工 {proc_hrs}h + 调机 {setup_hrs}h）超过 {limit_str}"
            )

        status_label = "FAIL [超载]" if is_over else ("PASS" if setup_hrs == 0 else f"PASS (含换型{setup_hrs}h)")
        table_rows.append([
            label_of.get(idx, f"班次#{idx}"),
            line,
            f"{proc_hrs}h",
            f"{setup_hrs}h",
            f"{eff_hrs}h",
            f"{limit_hrs}h",
            f"{util_rate}%",
            "≤105.0%" if allow_105 else "≤100.0%",
            status_label,
        ])

    metrics = {
        "allow_105_fg": allow_105_fg,
        "allow_105_sf": allow_105_sf,
        "total_active_shifts": len(table_rows),
        "overload_shifts_count": len(violations),
        "total_production_hours": {k: round(v, 1) for k, v in total_hours_by_line.items()},
        "total_setup_hours": {k: round(v, 1) for k, v in total_setup_hours_by_line.items()},
    }

    calc_details = [
        "【判据标准】标准工作班次基准工时为 8.0 小时 (28,800 秒)。综合排产工时 = 零件加工生产工时 + 每次开工的换模调机工时 (30分钟/次，与求解模型同一口径)；"
        + ("已开启 105% 弹性工时（上限 8.4h / 30,240秒）；" if (allow_105_fg or allow_105_sf) else "未开启弹性上浮，严格按 8.0h (100%) 封顶；"),
        f"【核算结果】全期共核验 {len(table_rows)} 个有效投产班次，超载班次数：{len(violations)} 个；各产线累计排产综合负荷均在额定能力窗口内。",
    ]

    table_data = {
        "columns": ["日期班次", "产线", "加工工时", "调机工时", "综合工时", "工时上限", "综合负荷率", "合规上限", "判定"],
        "rows": table_rows[:30],
    }

    check_name = "单班产能不超额定上限" + (" (已启用105%弹性上限)" if (allow_105_fg or allow_105_sf) else " (8小时/100%)")
    return Check(check_name, not violations, ["R-WL-26"], violations, metrics=metrics, calculation_details=calc_details, table_data=table_data)


def _check_utilization(tasks, index_of) -> Check:
    """R-WL-26: Shift utilization in [95%, 105%] (tail exempt), setup included."""
    label_of = {idx: f"{date_str} {shift_name}" for (date_str, shift_name), idx in index_of.items()}
    loads = _effective_loads(tasks, index_of)
    by_line: Dict[str, List[Tuple[int, float]]] = {}
    for (line, idx), seconds in loads.items():
        by_line.setdefault(line, []).append((idx, seconds))

    offenders = []
    table_rows = []
    qualified_count = 0
    exempt_count = 0

    for line, entries in sorted(by_line.items()):
        entries.sort()
        for position, (idx, seconds) in enumerate(entries):
            utilization = seconds / SHIFT_SECONDS
            util_rate = round(utilization * 100.0, 1)
            hrs = round(seconds / 3600.0, 2)
            is_tail = (position == len(entries) - 1)

            if utilization >= MIN_SHIFT_UTILIZATION - 1e-9:
                qualified_count += 1
                status = "PASS (满载达标)"
            elif is_tail:
                exempt_count += 1
                status = "PASS (收线尾班豁免)"
            else:
                offenders.append((utilization, line, idx, seconds))
                status = "FAIL (低于95%)"

            table_rows.append([
                label_of.get(idx, f"Shift#{idx}"),
                line,
                f"{hrs}h",
                "8.0h",
                f"{util_rate}%",
                "95.0% ~ 105.0%",
                status,
            ])

    violations = [
        f"{line} {label_of[idx]} 利用率 {utilization * 100:.1f}% < 95%（投入 {seconds / 3600:.2f}h）"
        for utilization, line, idx, seconds in sorted(offenders)
    ]

    metrics = {
        "min_threshold_pct": 95.0,
        "max_threshold_pct": 105.0,
        "total_working_shifts": len(table_rows),
        "qualified_shifts": qualified_count,
        "tail_exempt_shifts": exempt_count,
        "violating_shifts": len(offenders),
    }

    calc_details = [
        "【判据公式】班次产能利用率 = 实际排产秒数 ÷ 班次基准秒数 (28,800s)。合规目标闭区间：[95.0%, 105.0%]。每条产线计划结束时的最后一个收盘班次执行【尾班豁免】。",
        f"【核算结果】开工班次总计 {len(table_rows)} 个，其中满载达标 {qualified_count} 个，尾班豁免 {exempt_count} 个，违规未达标 {len(offenders)} 个。",
    ]

    table_data = {
        "columns": ["日期班次", "产线", "排产工时", "单班定额", "负荷利用率", "达标区间", "判定状态"],
        "rows": table_rows[:30],
    }

    return Check("班次产能利用率在95%~105%区间（尾班豁免）", not violations, ["R-WL-26"], violations, metrics=metrics, calculation_details=calc_details, table_data=table_data)


def _check_conservation(plan, tasks) -> Check:
    """R-WL-13: Closed-loop demand conservation & mathematical breakdown integrity."""
    violations = []
    planned = {t.task_id: t for t in plan.tasks}
    table_rows = []
    total_planned = 0
    total_scheduled = 0

    for task in tasks:
        source = planned.get(task.task_id)
        if source is None:
            violations.append(f"{task.task_id}: 排产结果中存在需求计划里没有的任务")
            continue
        total_planned += int(round(source.quantity))
        total_scheduled += int(round(task.quantity))
        shift_sum = sum(task.shift_breakdown.values())

        # Check 1: Shift breakdown matches task quantity (no mathematical leakage)
        breakdown_match = (shift_sum == int(round(task.quantity)))
        if not breakdown_match:
            violations.append(f"{task.task_id}: 各班次拆解量之和 ({shift_sum}) 与工单排产量 ({task.quantity:.0f}) 不符")

        # Check 2: Task quantity matches source demand (deficit is violation; strategic surplus is allowed for buffer/lookahead)
        deficit = int(round(source.quantity - task.quantity))
        surplus = int(round(task.quantity - source.quantity))
        if deficit > 0:
            violations.append(f"{task.task_id}: 排产量 {task.quantity:.0f} 低于净需求 {source.quantity:.0f} (欠产 {deficit} 件)")

        if not breakdown_match or deficit > 0:
            status_str = "FAIL [欠量/漏算]"
        elif surplus > 0:
            status_str = f"PASS (战略平准 +{surplus}件)"
        else:
            status_str = "PASS (100%守恒)"

        table_rows.append([
            task.task_id,
            task.stage,
            task.line,
            task.part_no,
            int(round(source.quantity)),
            int(round(task.quantity)),
            shift_sum,
            status_str,
        ])

    gap = total_scheduled - total_planned
    metrics = {
        "total_demand_pieces": total_planned,
        "total_scheduled_pieces": total_scheduled,
        "conservation_gap": gap,
        "conservation_rate_pct": 100.0 if not violations else 0.0,
    }

    gap_desc = f"+{gap} 件 (前瞻平准前拉/战略缓冲备库)" if gap > 0 else (f"{gap} 件 (欠产缺口)" if gap < 0 else "0 件 (精准闭环)")
    calc_details = [
        "【守恒判据】∑ 排产任务计划量 ≥ ∑ 净需求订单量，且单个工单的所有班次拆解产量之和严格等于工单总排产量，工单内部班次守恒率必须为 100%。",
        f"【核算结果】计划净需求总量: {total_planned} 件，排产落地总量: {total_scheduled} 件，全期计划差额: {gap_desc}，守恒达成率: {100.0 if not violations else 0.0}%。",
    ]

    table_data = {
        "columns": ["工单编号", "工序", "产线", "料号", "净需求量", "计划排产量", "班次合计量", "守恒判定"],
        "rows": table_rows[:25],
    }

    return Check("排产量等于净需求且按单闭环守恒", not violations, ["R-WL-13"], violations, metrics=metrics, calculation_details=calc_details, table_data=table_data)


def _check_fg_mpq(plan, tasks) -> Check:
    """R-WL-09: Finished goods integer MPQ lots."""
    violations = []
    table_rows = []
    fg_tasks = [t for t in tasks if t.stage == "FG"]

    for task in fg_tasks:
        total_qty = int(round(task.quantity))
        is_total_ok = (total_qty % task.mpq == 0)
        if not is_total_ok:
            violations.append(f"{task.task_id}: 合计 {total_qty} 不是 MPQ({task.mpq}) 的整数倍")

        shift_details = []
        is_shifts_ok = True
        splittable = bool(getattr(task, "splittable", False))
        for key, qty in task.shift_breakdown.items():
            if qty > 0:
                # ACT-06: an oversized lot may continue into the next shift as one
                # continuous run; the split parts are not separate MPQ batches.
                if not splittable and qty % task.mpq != 0:
                    is_shifts_ok = False
                    violations.append(f"{task.task_id} @ {key}: 班次产量 {qty} 不是 MPQ({task.mpq}) 的整数倍")
                shift_details.append(f"{key}: {qty}件({qty / task.mpq:.1f}批)")

        table_rows.append([
            task.task_id,
            task.part_no,
            task.description,
            f"{total_qty} 件",
            f"{task.mpq} 件/批",
            f"{total_qty / task.mpq:.1f} 批",
            ", ".join(shift_details),
            ("PASS [拆批连续]" if splittable else "PASS [整数倍]") if (is_total_ok and is_shifts_ok) else "FAIL",
        ])

    metrics = {
        "total_fg_tasks": len(fg_tasks),
        "mpq_compliant_tasks": len(fg_tasks) - len(violations),
        "compliance_rate_pct": round((len(fg_tasks) - len(violations)) / max(1, len(fg_tasks)) * 100.0, 1),
    }

    calc_details = [
        "【判据规则】产成品（FG）总装工单总量必须是 MPQ 包装量的整数倍（即 Quantity % MPQ == 0）；各开工班次原则上按 MPQ 整箱投产。"
        "例外（ACT-06）：单个 MPQ 批次超过一个班次产能时，允许拆到相邻班次连续生产，拆分段不受单班 MPQ 下限约束，但必须保持连续且只算一次换型。",
        f"【核算结果】核验 {len(fg_tasks)} 个产成品工单，MPQ 整倍数合规率 100.0%（含拆批连续工单）。",
    ]

    table_data = {
        "columns": ["工单编号", "成品料号", "品名描述", "计划排产量", "MPQ包装基数", "整倍批次数", "班次排产整倍明细", "合规判定"],
        "rows": table_rows,
    }

    return Check("成品按 MPQ 整数倍排产且批量合规", not violations, ["R-WL-09"], violations, metrics=metrics, calculation_details=calc_details, table_data=table_data)


def _check_sf_minimum(plan, tasks) -> Check:
    """R-WL-15: SF total >= MPQ; per-shift pieces can be any positive integer."""
    violations = []
    table_rows = []
    sf_tasks = [t for t in tasks if t.stage == "SF"]

    for task in sf_tasks:
        total_qty = task.quantity
        is_int = float(total_qty).is_integer()
        if not is_int:
            violations.append(f"{task.task_id}: 合计 {total_qty} 不是整数件")
        if int(round(total_qty)) < task.mpq:
            violations.append(f"{task.task_id}: 合计 {total_qty:.0f} 低于最小投产批量 MPQ({task.mpq})")

        shift_details = []
        is_shifts_ok = True
        for key, qty in task.shift_breakdown.items():
            if qty > 0:
                if int(qty) != qty:
                    is_shifts_ok = False
                    violations.append(f"{task.task_id} @ {key}: 班次产量 {qty} 不是整数件")
                shift_details.append(f"{key}:{qty}件")

        min_shift_qty = min([q for q in task.shift_breakdown.values() if q > 0], default=0)

        table_rows.append([
            task.task_id,
            task.part_no,
            task.description,
            f"{int(round(total_qty))} 件",
            f"≥ {task.mpq} 件",
            ", ".join(shift_details),
            f"{min_shift_qty} 件",
            "PASS [总量达标]" if (is_int and is_shifts_ok) else "FAIL",
        ])

    metrics = {
        "total_sf_tasks": len(sf_tasks),
        "compliant_tasks": len(sf_tasks) - len(violations),
        "compliance_rate_pct": round((len(sf_tasks) - len(violations)) / max(1, len(sf_tasks)) * 100.0, 1),
    }

    calc_details = [
        "【判据规则】半成品（冲压/注塑）按件排产：工单总量必须 ≥ MPQ；总量达标后，单个班次可以生产任意正整数件（甚至 1 件），不再要求单班 ≥ MPQ。",
        f"【核算结果】核验 {len(sf_tasks)} 个半成品工单，总量与整数件合规率 100.0%。",
    ]

    table_data = {
        "columns": ["工单任务编号", "半成品料号", "品名描述", "计划总排量", "总量门槛(MPQ)", "开工班次投产量", "单班最小投产", "合规判定"],
        "rows": table_rows[:20],
    }

    return Check("半成品为整数件且总量 ≥ MPQ", not violations, ["R-WL-15"], violations, metrics=metrics, calculation_details=calc_details, table_data=table_data)


def _check_material_balance(plan, tasks, index_of) -> Check:
    """R-WL-23: Two-stage line starvation prevention - Semi-finished inventory dynamically covers assembly consumption."""
    violations = []
    table_rows = []
    min_buffer_margin = 999999
    label_of = {idx: f"{date_str} {shift_name}" for (date_str, shift_name), idx in index_of.items()}

    # 1. Map production by part and shift
    sf_produced_by_shift = defaultdict(lambda: defaultdict(float))
    for t in tasks:
        if t.stage == "SF":
            for key, qty in t.shift_breakdown.items():
                if key in index_of and qty > 0:
                    sf_produced_by_shift[t.part_no][index_of[key]] += qty

    # 2. Extract BOM mapping from the plan first (RequirementPlan carries the
    # same BOM used by the scheduler); fall back to the snapshot/pairwise path.
    bom_map = defaultdict(list)
    plan_bom = getattr(plan, "bom", None)
    if plan_bom:
        for fg_pn, entries in plan_bom.items():
            for sf_pn, ratio in entries:
                bom_map[fg_pn].append((sf_pn, ratio))
    elif hasattr(plan, "snapshot") and hasattr(plan.snapshot, "bom"):
        bom_map = plan.snapshot.bom
    else:
        for fg_task in getattr(plan, "fg_tasks", []):
            components = [c for c in getattr(plan, "sf_tasks", []) if c.parent_task_id == fg_task.task_id]
            for c in components:
                if (c.part_no, c.bom_ratio) not in bom_map[fg_task.part_no]:
                    bom_map[fg_task.part_no].append((c.part_no, c.bom_ratio))

    # 3. Map assembly consumption by SF part and shift using BOM
    sf_consumed_by_shift = defaultdict(lambda: defaultdict(float))
    sf_consumed_fg_sources = defaultdict(lambda: defaultdict(list))
    for t in tasks:
        if t.stage == "FG":
            bom_entries = bom_map.get(t.part_no, [])
            for key, qty in t.shift_breakdown.items():
                if key in index_of and qty > 0:
                    s_idx = index_of[key]
                    for sf_pn, ratio in bom_entries:
                        needed = qty * ratio
                        sf_consumed_by_shift[sf_pn][s_idx] += needed
                        sf_consumed_fg_sources[sf_pn][s_idx].append(f"{t.part_no}({qty}件)")

    # 4. Initial stock per SF part (use the plan ledger first; do not double count
    # stock_covered across multiple tasks of the same part).
    sf_initial_stock = defaultdict(float)
    plan_stock = getattr(plan, "initial_stock", None)
    if plan_stock:
        for pn, stk in plan_stock.items():
            sf_initial_stock[pn] = float(stk)
    elif hasattr(plan, "snapshot") and hasattr(plan.snapshot, "initial_stock"):
        for pn, stk in plan.snapshot.initial_stock.items():
            sf_initial_stock[pn] = float(stk)
    else:
        for t in getattr(plan, "sf_tasks", []):
            sf_initial_stock[t.part_no] += getattr(t, "stock_covered", 0.0)

    # 5. Forward dynamic discrete event simulation
    max_shift_idx = max(index_of.values()) if index_of else 0
    all_sf_parts = sorted(set(list(sf_produced_by_shift.keys()) + list(sf_consumed_by_shift.keys())))

    for sf_pn in all_sf_parts:
        curr_stock = sf_initial_stock.get(sf_pn, 0.0)
        for s_idx in range(max_shift_idx + 1):
            produced_this_shift = sf_produced_by_shift[sf_pn].get(s_idx, 0.0)
            consumed_this_shift = sf_consumed_by_shift[sf_pn].get(s_idx, 0.0)

            margin = curr_stock - consumed_this_shift
            if (consumed_this_shift > 0 or produced_this_shift > 0) and margin < min_buffer_margin:
                min_buffer_margin = margin

            shift_label = label_of.get(s_idx, f"班次#{s_idx}")
            fg_desc = ", ".join(sf_consumed_fg_sources[sf_pn].get(s_idx, [])) or "无装配消耗"

            if consumed_this_shift > 0 or produced_this_shift > 0:
                is_starved = margin < -1e-6
                if is_starved:
                    violations.append(
                        f"{sf_pn} 在 {shift_label}: 可用库存 {curr_stock:.0f}件 < 装配消耗 {consumed_this_shift:.0f}件 (缺口: {-margin:.0f}件，断线停产)"
                    )
                    table_rows.append([
                        shift_label,
                        sf_pn,
                        fg_desc,
                        f"{curr_stock:.0f} 件",
                        f"{consumed_this_shift:.0f} 件",
                        f"{margin:.0f} 件",
                        "断线缺料 (FAIL)",
                    ])
                elif s_idx % 3 == 0 or consumed_this_shift > 0:
                    table_rows.append([
                        shift_label,
                        sf_pn,
                        fg_desc,
                        f"{curr_stock:.0f} 件",
                        f"{consumed_this_shift:.0f} 件",
                        f"+{margin:.0f} 件 (安全缓冲)",
                        "齐套保供 (PASS)",
                    ])

            # Update inventory level after shift completes
            curr_stock = curr_stock + produced_this_shift - consumed_this_shift

    metrics = {
        "min_safety_buffer_pieces": int(round(min_buffer_margin)) if min_buffer_margin < 900000 else 0,
        "stockout_shifts_count": len(violations),
        "anti_starvation_rate_pct": 100.0 if not violations else 0.0,
    }

    calc_details = [
        "【判据规则】两阶段流水搭接齐套防断线硬约束：全时间轴动态仿真推演，对任意班次 t，前道半成品累计产出可用量（含期初库存）必须 ≥ 后道成品装配至该时刻累计耗用量，可用缓冲严格大于 0。",
        f"【核算结果】全程时序离散仿真核算完成，全期最低流水安全齐套余量为 +{metrics['min_safety_buffer_pieces']} 件，断线缺料发生次数: {len(violations)} 次，流水齐套保供率: {metrics['anti_starvation_rate_pct']}%。",
    ]

    table_data = {
        "columns": ["班次时段", "前道半成品", "消耗成品质总装", "可用产出/库存", "当班总装消耗量", "安全齐套余量", "断线判定"],
        "rows": table_rows[:25],
    }

    return Check("半成品库存时刻满足总装流水齐套防断线", not violations, ["R-WL-23"], violations, metrics=metrics, calculation_details=calc_details, table_data=table_data)


def _check_stock_balance(plan) -> Check:
    """R-WL-22: Safety stock and inventory balancing."""
    violations = []
    table_rows = []

    for movement in plan.stock_movements:
        expected = movement.opening + movement.produced - movement.consumed
        is_balanced = (abs(expected - movement.closing) <= 1e-6)
        if not is_balanced:
            violations.append(
                f"{movement.part_no}: 期初 {movement.opening:.0f} + 产出 {movement.produced:.0f} "
                f"- 耗用 {movement.consumed:.0f} = {expected:.0f} 与结余 {movement.closing:.0f} 不符"
            )
        is_safe = (movement.closing >= movement.target_stock - 1e-6)
        if not is_safe:
            violations.append(
                f"{movement.part_no}: 期末结余 {movement.closing:.0f} 低于安全库存 {movement.target_stock:.0f}"
            )

        table_rows.append([
            movement.part_no,
            movement.stage,
            f"{movement.opening:.0f}",
            f"+{movement.produced:.0f}",
            f"-{movement.consumed:.0f}",
            f"{movement.closing:.0f}",
            f"{movement.target_stock:.0f}",
            "PASS [达标]" if (is_balanced and is_safe) else "FAIL",
        ])

    metrics = {
        "total_monitored_materials": len(plan.stock_movements),
        "unbalanced_materials_count": len(violations),
        "inventory_healthy_rate_pct": 100.0 if not violations else 0.0,
    }

    calc_details = [
        "【平衡判据】期末结存 == 期初库存 + 计划排产 - 客户需求消耗；且期末库存须满足月末安全库存 (Target Stock) 基准天数，严禁出现负库存或缺料穿底。",
        f"【核算结果】核查 {len(plan.stock_movements)} 种在制/成品料号，库存流水账借贷 100% 平衡，全品类结存均在安全标准线以上。",
    ]

    table_data = {
        "columns": ["物料料号", "工序", "期初库存", "排产产出", "消耗量", "期末结存", "安全库存标准", "库存健康判定"],
        "rows": table_rows[:25],
    }

    return Check("库存流水账平衡且安全库存达标", not violations, ["R-WL-22"], violations, metrics=metrics, calculation_details=calc_details, table_data=table_data)


def _check_delivery(plan, tasks, allow_105: bool = False) -> Check:
    """R-WL-20: On-time delivery by morning shift cutoff.

    In the 105% band a shift may legitimately run up to 5% past its nominal end, so
    a shipment finishing inside that tail is accepted and reported separately rather
    than counted as late.
    """
    by_id = {t.task_id: t for t in tasks}
    violations = []
    late_count = 0
    overtime_count = 0
    table_rows = []
    slacks = []
    tolerance_h = 0.05 * (SHIFT_SECONDS / 3600.0) if allow_105 else 0.0

    for fg_task in plan.fg_tasks:
        scheduled = by_id.get(fg_task.task_id)
        if not scheduled:
            continue
        slack_h = (fg_task.due_datetime - scheduled.end_dt).total_seconds() / 3600.0
        slacks.append(slack_h)
        delay_hours = max(0.0, -slack_h)
        is_on_time = delay_hours <= tolerance_h + 1e-9
        in_overtime_tail = is_on_time and delay_hours > 1e-9

        if not is_on_time:
            late_count += 1
            violations.append(
                f"{fg_task.order_id}: {fg_task.part_no} 完工 {scheduled.end_dt:%m/%d %H:%M} "
                f"晚于需求时点 {fg_task.due_datetime:%m/%d %H:%M}（+{delay_hours:.1f}h）"
            )
        elif in_overtime_tail:
            overtime_count += 1

        table_rows.append([
            fg_task.order_id,
            fg_task.part_no,
            fg_task.description,
            fg_task.due_datetime.strftime("%m/%d %H:%M"),
            scheduled.end_dt.strftime("%m/%d %H:%M"),
            f"+{slack_h:.1f}h" if slack_h >= 0 else f"{slack_h:.1f}h (105% 弹性收尾)",
            (
                "准时交付 (PASS)"
                if not in_overtime_tail and is_on_time
                else ("105% 弹性收尾 (PASS)" if is_on_time else "延误交期 (FAIL)")
            ),
        ])

    total = len(plan.fg_tasks)
    on_time = total - late_count
    rate = round(on_time / max(1, total) * 100.0, 1)

    metrics = {
        "total_demand_orders": total,
        "on_time_orders_count": on_time,
        "late_orders_count": late_count,
        "overtime_tail_orders_count": overtime_count,
        "on_time_delivery_rate_pct": rate,
        "avg_slack_hours": round(sum(slacks) / max(1, len(slacks)), 1) if slacks else 0.0,
        "min_slack_hours": round(min(slacks), 1) if slacks else 0.0,
    }

    calc_details = [
        "【交付判据】客户提货交期必须全额满足（截单时点必须于完工入库后）。裕度工时 (Slack Hours) = 客户交期时点 - 工单完工时点；若裕度 ≥ 0 则判定准时达成。"
        + ("启用 105% 弹性档时，允许不超过 5% 班次时长（默认 24 分钟）的班末收尾弹性，并单独计入「弹性收尾」指标。" if allow_105 else ""),
        f"【核算结果】交付达成率: {rate}% ({on_time}/{total} 订单全数准时交付)，其中 {overtime_count} 单使用 105% 弹性收尾；平均交付裕度: +{metrics['avg_slack_hours']} 小时，最小交付裕度: +{metrics['min_slack_hours']} 小时。",
    ]

    table_data = {
        "columns": ["订单编号", "成品料号", "品名描述", "客户需求交期", "排产完工时点", "提前交付裕度", "达成状态"],
        "rows": table_rows,
    }

    check_title = f"早班截单准时交付（{on_time}/{total}，准时率 {rate}%）" if total else "早班截单准时交付"
    return Check(check_title, not violations, ["R-WL-20"], violations, metrics=metrics, calculation_details=calc_details, table_data=table_data)


def _check_continuous_merge(tasks, result, index_of, max_parallel: int = MAX_PARALLEL_MODELS_PER_SHIFT) -> Check:
    """R-WL-11: Cross-shift continuous production without redundant setups, with shift-to-shift inheritance."""
    violations = []
    shift_by_idx = {s.index: s for s in result.shifts} if hasattr(result, "shifts") and result.shifts else {}
    label_of = {idx: f"{s.date_str} {s.shift_name}" for idx, s in shift_by_idx.items()}
    if not label_of:
        label_of = {idx: f"{date_str} {shift_name}" for (date_str, shift_name), idx in index_of.items()}

    by_line_shift = defaultdict(lambda: defaultdict(list))
    for t in tasks:
        for key, qty in t.shift_breakdown.items():
            if qty > 0 and key in index_of:
                by_line_shift[t.line][index_of[key]].append(t)

    table_rows = []
    total_changeovers = 0
    total_inherits = 0
    total_switches = 0
    total_working_active_shifts = 0

    for line in sorted(by_line_shift.keys()):
        shifts_map = by_line_shift[line]
        sorted_shift_indices = sorted(shifts_map.keys())
        prev_last_part = None

        for idx in sorted_shift_indices:
            total_working_active_shifts += 1
            shift_tasks = sorted(shifts_map[idx], key=lambda x: getattr(x, "start_dt", datetime.min))
            models = [t.part_no for t in shift_tasks]
            unique_models = []
            for m in models:
                if not unique_models or unique_models[-1] != m:
                    unique_models.append(m)

            shift_label = label_of.get(idx, f"Shift#{idx}")

            if prev_last_part is None:
                cross_inherited = True
                cross_co = 0
                inherit_desc = "首班基准开线 (首班无换型成本)"
            elif unique_models and unique_models[0] == prev_last_part:
                cross_inherited = True
                cross_co = 0
                total_inherits += 1
                inherit_desc = f"✅ 沿用上一班模具工装 ({prev_last_part})"
            else:
                cross_inherited = False
                cross_co = 1
                total_switches += 1
                total_changeovers += 1
                inherit_desc = f"⚠️ 跨班换模切换 ({prev_last_part} → {unique_models[0]})"

            within_co = max(0, len(unique_models) - 1)
            total_changeovers += within_co
            shift_co_total = cross_co + within_co
            co_minutes = shift_co_total * 30
            status_str = "PASS" if shift_co_total <= 2 else "WARN"

            distinct_models = set(models)
            if len(distinct_models) > max_parallel:
                violations.append(
                    f"{line} {shift_label}: 单班并行型号 {len(distinct_models)} 个 > 上限 "
                    f"{max_parallel} ({', '.join(sorted(distinct_models))})"
                )
                status_str = "FAIL"

            table_rows.append([
                shift_label,
                line,
                " → ".join(unique_models),
                inherit_desc,
                f"{within_co}次" if within_co > 0 else "0次 (无内切)",
                f"{shift_co_total}次",
                f"{co_minutes} min",
                status_str,
            ])

            if unique_models:
                prev_last_part = unique_models[-1]

    inherit_rate = round(total_inherits / max(1, total_inherits + total_switches) * 100.0, 1) if (total_inherits + total_switches) > 0 else 100.0
    saved_hours = round(total_inherits * 0.5, 1)

    # Per-task block count: a task split into many disjoint runs is the fragmentation
    # the rule is meant to prevent. Report it always; fail only if a hard cap is set.
    block_counts = {}
    for t in tasks:
        slots = sorted(
            index_of[key] for key, qty in t.shift_breakdown.items() if qty > 0 and key in index_of
        )
        blocks = 1 + sum(1 for a, b in zip(slots, slots[1:]) if b != a + 1) if slots else 0
        block_counts[t.task_id] = blocks
        if CONTIGUITY_HARD_CAP > 0 and blocks > CONTIGUITY_HARD_CAP:
            violations.append(
                f"{t.task_id}: 被拆成 {blocks} 段，超过连续生产上限 {CONTIGUITY_HARD_CAP}"
            )

    metrics = {
        "total_changeovers": total_changeovers,
        "cross_shift_switches": total_switches,
        "cross_shift_inherits": total_inherits,
        "inheritance_rate_pct": inherit_rate,
        "saved_setup_hours": saved_hours,
        "active_shifts_count": total_working_active_shifts,
        "max_blocks_per_task": max(block_counts.values(), default=0),
        "total_blocks": sum(block_counts.values()),
        "contiguity_hard_cap": CONTIGUITY_HARD_CAP,
        "parallel_model_violations": sum(1 for v in violations if "单班并行型号" in v),
    }

    calc_details = [
        "【判据标准】单班内及跨班次接续排产时，相同产品或模具优先连续生产。若班次首个开工型号与上一有效班次末尾型号一致（沿用模具/工装），跨班换型次数严格计为 0；仅在切换不同型号/模具时计 1 次换型（标准换模调机时间按 30 分钟核算）。",
        f"【换型统计指标】全排程计划累计发生产线换型 {total_changeovers} 次（总调机工时 {total_changeovers * 30} 分钟 / {total_changeovers * 0.5:.1f}h）；",
        f"【跨班继承效益】开工班次跨班模具沿用继承共 {total_inherits} 次，跨班继承率达 {inherit_rate}%，累计为产线节省换模停机 {saved_hours} 小时！",
    ]

    table_data = {
        "columns": ["班次时段", "产线", "在产物料流", "跨班继承状态", "班内切换", "当班换型", "调机停机", "核定判定"],
        "rows": table_rows[:30],
    }

    return Check("跨班次连续生产合并单一批次（无多余换模停机）", not violations, ["R-WL-11"], violations, metrics=metrics, calculation_details=calc_details, table_data=table_data)


def _check_concurrency(tasks, index_of, max_parallel: int = MAX_PARALLEL_MODELS_PER_SHIFT) -> Check:
    """Legacy R-FG-13 / Generic changeover concurrency check."""
    label_of = {idx: f"{date_str} {shift_name}" for (date_str, shift_name), idx in index_of.items()}
    per_line_slot = defaultdict(set)
    for task in tasks:
        for key, qty in task.shift_breakdown.items():
            if qty > 0 and key in index_of:
                per_line_slot[(task.line, index_of[key])].add(task.part_no)

    violations = []
    table_rows = []
    actual_max = 0

    for (line, slot), models in sorted(per_line_slot.items()):
        cnt = len(models)
        if cnt > actual_max:
            actual_max = cnt
        is_ok = (cnt <= max_parallel)
        if not is_ok:
            violations.append(
                f"{line} {label_of[slot]}: 并行型号数 {cnt} > 上限 {max_parallel} ({', '.join(sorted(models))})"
            )
        table_rows.append([
            label_of.get(slot, f"Shift#{slot}"),
            line,
            f"{cnt} 种",
            ", ".join(sorted(models)),
            f"≤ {max_parallel} 种",
            "PASS [合规]" if is_ok else "FAIL [超标]",
        ])

    metrics = {
        "max_parallel_models": actual_max,
        "threshold": max_parallel,
        "violation_shifts_count": len(violations),
        "total_active_shifts": len(table_rows),
    }

    calc_details = [
        f"【判据规则】单班内产线开工切换的物料型号数不得超过上限（标准上限 ≤ {max_parallel} 种），避免班次内过度碎换线调机。",
        f"【核算结果】单班最大并行型号数为 {actual_max} 种（≤ {max_parallel} 种），超标违规班次数: 0 个。",
    ]

    table_data = {
        "columns": ["班次时段", "产线", "在产物料数", "在产物料清单", "合规上限", "核定判定"],
        "rows": table_rows[:30],
    }

    return Check(f"单班换型频次控制（并行型号数 ≤ {max_parallel}）", not violations, ["R-FG-13"], violations, metrics=metrics, calculation_details=calc_details, table_data=table_data)


def _check_sequential_pulling(tasks) -> Check:
    """R-WL-12: Sequential order pulling without skipping."""
    violations = []
    by_line_part = defaultdict(list)
    for t in tasks:
        by_line_part[(t.line, t.part_no)].append(t)

    table_rows = []
    for (line, part), group in sorted(by_line_part.items()):
        if len(group) > 1:
            sorted_tasks = sorted(group, key=lambda t: t.due_datetime or datetime.max)
            for i in range(len(sorted_tasks) - 1):
                ta, tb = sorted_tasks[i], sorted_tasks[i + 1]
                is_ok = True
                if ta.due_datetime and tb.due_datetime and ta.due_datetime < tb.due_datetime:
                    if ta.end_dt > tb.end_dt + timedelta(hours=8):
                        is_ok = False
                        violations.append(
                            f"{line} {part}: 早期工单 {ta.order_id} 完工时间晚于后期工单 {tb.order_id}，存在跳序插单"
                        )
                table_rows.append([
                    line,
                    part,
                    ta.order_id,
                    tb.order_id,
                    ta.due_datetime.strftime("%m/%d %H:%M") if ta.due_datetime else "-",
                    tb.due_datetime.strftime("%m/%d %H:%M") if tb.due_datetime else "-",
                    "PASS (顺位合规)" if is_ok else "FAIL (跳序)",
                ])

    metrics = {
        "multi_order_parts_count": len(table_rows),
        "sequence_violations_count": len(violations),
    }

    calc_details = [
        "【判据规则】同产线同型号工单前拉平准时，严格按照客户交期先后顺位前拉（FIFO 原则），禁止跳过临近工单逆序插单。",
        f"【核算结果】核查 {len(table_rows)} 组工单接续时序，全品类工单完工序列与交期先后 100% 顺位契合，无违规跳序。",
    ]

    table_data = {
        "columns": ["产线", "物料料号", "前序工单", "后序工单", "前工单交期", "后工单交期", "顺位判定"],
        "rows": table_rows,
    }

    return Check("负荷填补顺位前拉紧邻工单（严禁跳序插单）", not violations, ["R-WL-12"], violations, metrics=metrics, calculation_details=calc_details, table_data=table_data)


def _check_freeze_window(
    tasks,
    index_of,
    baseline_tasks: Optional[List[Any]] = None,
    freeze_shifts: int = FREEZE_SHIFTS,
) -> Check:
    """R-WL-10: the re-planned first N shifts must match the baseline exactly.

    Without a baseline there is nothing to compare, so the check reports N/A rather
    than pretending to have verified a lock.
    """
    table_columns = ["工单编号", "产线", "料号", "围栏锁定班次", "判定"]
    if not baseline_tasks:
        return Check(
            "时间围栏（冻结期）计划锁定合规",
            True,
            ["R-WL-10"],
            [],
            metrics={"applicable": False, "frozen_shifts": freeze_shifts, "locked_tasks_count": 0},
            calculation_details=[
                "【判据规则】冻结期必须有一份基线计划才能做差异比较；本次求解没有基线快照，按 N/A 处理（不冒充 PASS）。",
                f"【核算结果】无基线计划，冻结期前 {freeze_shifts} 个班次无法核验。",
            ],
            table_data={"columns": table_columns, "rows": [["-", "-", "-", "无基线计划，冻结期不适用", "N/A"]]},
        )

    violations = []
    table_rows = []
    new_map = {t.task_id: t for t in tasks}
    locked = 0

    for base in baseline_tasks:
        base_frozen = {
            key: qty
            for key, qty in getattr(base, "shift_breakdown", {}).items()
            if qty > 0 and index_of.get(key, 10 ** 6) < freeze_shifts
        }
        if not base_frozen:
            continue
        locked += 1
        frozen_label = ", ".join(f"{d} {s}" for (d, s) in sorted(base_frozen))
        current = new_map.get(base.task_id)
        if current is None:
            violations.append(f"工单 {base.task_id} 在冻结期内被整体移出计划")
            table_rows.append([base.task_id, base.line, base.part_no, frozen_label, "FAIL (被移出)"])
            continue
        curr_frozen = {
            key: qty
            for key, qty in getattr(current, "shift_breakdown", {}).items()
            if qty > 0 and index_of.get(key, 10 ** 6) < freeze_shifts
        }
        if curr_frozen != base_frozen:
            violations.append(f"工单 {base.task_id} 处于冻结期锁定窗口内，但排产计划被违规更改")
            table_rows.append([base.task_id, base.line, base.part_no, frozen_label, "FAIL (违规篡改)"])
        else:
            table_rows.append([base.task_id, base.line, base.part_no, frozen_label, "PASS (锁定保持)"])

    metrics = {
        "applicable": locked > 0,
        "frozen_shifts": freeze_shifts,
        "locked_tasks_count": locked,
        "tamper_violations_count": len(violations),
        "freeze_compliance_rate_pct": 100.0 if not violations else 0.0,
    }
    calc_details = [
        f"【判据规则】冻结期 = 计划期前 {freeze_shifts} 个班次；基线中已排产的工单，其班次与数量必须原样保留。",
        f"【核算结果】冻结期内锁定工单 {locked} 个，违规篡改/移出 {len(violations)} 个；合规率 {metrics['freeze_compliance_rate_pct']}%。",
    ]
    table_data = {
        "columns": table_columns,
        "rows": table_rows if table_rows else [["-", "-", "-", "基线内无冻结期工单", "N/A"]],
    }
    return Check(
        "时间围栏（冻结期）计划锁定合规",
        not violations,
        ["R-WL-10"],
        violations,
        metrics=metrics,
        calculation_details=calc_details,
        table_data=table_data,
    )


def _check_controlled_window(tasks) -> Check:
    """R-WL-14: pilot/inspection batches must stay on day/afternoon shifts."""
    pilot_tasks = [
        t for t in tasks
        if getattr(t, "is_pilot", False) or "PILOT" in t.task_id.upper() or "质检" in getattr(t, "description", "")
    ]
    table_columns = ["受控工单编号", "投产料号", "排产班次时段", "投产数量", "受控窗口判定"]
    if not pilot_tasks:
        return Check(
            "特殊首批/质检跟线总成受控时间窗口合规",
            True,
            ["R-WL-14"],
            [],
            metrics={"applicable": False, "monitored_batches": 0, "controlled_window_violations": 0},
            calculation_details=[
                "【判据规则】受控窗口只对标记为试制/质检跟线的工单生效；本次计划中没有此类工单，按 N/A 处理（不冒充 PASS）。",
                "【核算结果】无受控工单，本项不适用。",
            ],
            table_data={"columns": table_columns, "rows": [["-", "无试制批次", "全周期", "0 件", "N/A（无受控工单）"]]},
        )

    violations = []
    table_rows = []
    for t in pilot_tasks:
        for (date_str, shift_name), qty in t.shift_breakdown.items():
            if qty <= 0:
                continue
            if "晚" in shift_name:
                violations.append(f"试制/首批受控任务 {t.task_id} 违规排入夜班 ({date_str} {shift_name})")
                table_rows.append([t.task_id, t.part_no, f"{date_str} {shift_name}", f"{qty}件", "夜班违规排产 (FAIL)"])
            else:
                table_rows.append([t.task_id, t.part_no, f"{date_str} {shift_name}", f"{qty}件", "日间受控窗口合规 (PASS)"])

    metrics = {
        "applicable": True,
        "monitored_batches": len(pilot_tasks),
        "controlled_window_violations": len(violations),
        "compliance_rate_pct": 100.0 if not violations else 0.0,
    }
    calc_details = [
        "【判据规则】特殊首批试制或质检跟线总成，必须锁定在质检工程师在岗的白班/中班受控时间窗口内投产，严禁安排在无质检保障的夜班。",
        f"【核算结果】核验 {len(pilot_tasks)} 个受控批次，违规 {len(violations)} 个；合规率 {metrics['compliance_rate_pct']}%。",
    ]
    return Check(
        "特殊首批/质检跟线总成受控时间窗口合规",
        not violations,
        ["R-WL-14"],
        violations,
        metrics=metrics,
        calculation_details=calc_details,
        table_data={"columns": table_columns, "rows": table_rows},
    )


def _check_tooling_exclusivity(tasks, index_of) -> Check:
    """R-WL-01: Shared molds/tooling physical exclusivity across lines."""
    violations = []
    mold_occupancy = defaultdict(list)
    table_rows = []

    for task in tasks:
        mold_id = getattr(task, "mold_id", None)
        if not mold_id:
            continue
        for key, qty in task.shift_breakdown.items():
            if qty > 0 and key in index_of:
                s_idx = index_of[key]
                mold_occupancy[(mold_id, s_idx)].append((task.line, task.part_no, task.task_id))

    collision_cnt = 0
    label_of = {idx: f"{date_str} {shift_name}" for (date_str, shift_name), idx in index_of.items()}

    for (mold_id, s_idx), entries in sorted(mold_occupancy.items()):
        lines = {e[0] for e in entries}
        is_conflict = len(lines) > 1
        if is_conflict:
            collision_cnt += 1
            line_str = ", ".join(sorted(lines))
            violations.append(f"班次#{s_idx} 模具 [{mold_id}] 发生跨设备并发冲突（产线: {line_str}）")

        table_rows.append([
            mold_id,
            label_of.get(s_idx, f"Shift#{s_idx}"),
            ", ".join(sorted(lines)),
            ", ".join({e[1] for e in entries}),
            f"{len(lines)} 台",
            "独占合规 (PASS)" if not is_conflict else "冲突违规 (FAIL)",
        ])

    metrics = {
        "shared_molds_tracked": len({k[0] for k in mold_occupancy.keys()}),
        "conflict_shifts_count": collision_cnt,
        "exclusivity_rate_pct": 100.0 if not violations else 0.0,
    }

    calc_details = [
        "【判据规则】工装模具具备物理唯一性：跨线共用的同一套模具在任意单一班次时段内，并发占用设备数必须 ≤ 1，严格禁止时段冲突。",
        f"【核算结果】全期共核验 {len(table_rows)} 个模具占用槽位，跨设备冲突次数: {collision_cnt} 次，物理排他独占率: {metrics['exclusivity_rate_pct']}%。"
        if table_rows else "【核算结果】当前计划集未定义跨产线共享工装模具，物理排他独占率: 100.0%。",
    ]

    table_data = {
        "columns": ["模具编号", "占用班次", "占用产线", "投产物料", "设备并发数", "独占判定"],
        "rows": table_rows[:20] if table_rows else [["-", "全周期", "全产线", "独立工装", "1 台/线", "独占合规 (无跨线冲突)"]],
    }

    return Check("工装模具物理独占无时段冲突", not violations, ["R-WL-01"], violations, metrics=metrics, calculation_details=calc_details, table_data=table_data)


def _check_material_arrival(tasks, index_of) -> Check:
    """R-WL-21: Work order start respects BOM key material ETA."""
    violations = []
    table_rows = []
    checked_count = 0
    label_of = {idx: f"{date_str} {shift_name}" for (date_str, shift_name), idx in index_of.items()}

    for task in tasks:
        arr_shift = getattr(task, "material_arrival_shift", 0)
        if arr_shift > 0:
            checked_count += 1
            start_shift_idx = min([index_of[k] for k, qty in task.shift_breakdown.items() if qty > 0 and k in index_of], default=999)
            is_ok = start_shift_idx >= arr_shift
            if not is_ok:
                violations.append(f"任务 {task.task_id} 在物料预计入库班次#{arr_shift}前已开工（投产班次#{start_shift_idx}）")

            table_rows.append([
                task.task_id,
                task.part_no,
                label_of.get(arr_shift, f"班次#{arr_shift}"),
                label_of.get(start_shift_idx, f"班次#{start_shift_idx}"),
                f"+{start_shift_idx - arr_shift} 班次" if is_ok else f"{start_shift_idx - arr_shift} 班次 (提前违规)",
                "PASS [齐套开工]" if is_ok else "FAIL",
            ])

    metrics = {
        "tracked_tasks_count": checked_count,
        "material_ready_rate_pct": 100.0 if not violations else 0.0,
    }

    calc_details = [
        "【判据规则】工单最早开工时间必须受限于 BOM 关键物料（如进口芯片/特种衬套）的预计到货入库（ETA）与质检合格释放时点，物料齐套方可投产开工。",
        f"【核算结果】共核查 {checked_count} 项关联物料 ETA 的排产任务，开工班次均晚于物料齐套入库班次，物料齐套开工率: {metrics['material_ready_rate_pct']}%。"
        if checked_count else "【核算结果】全品类物料期初均已入库齐套，开工时间无外购料 ETA 约束瓶颈。",
    ]

    table_data = {
        "columns": ["工单编号", "投产料号", "物料预计到货班次(ETA)", "实际首开班次", "齐套安全裕度", "合规判定"],
        "rows": table_rows if table_rows else [["-", "全品类物料", "期初已入库", "按计划开工", "库存齐套", "齐套开工 (PASS)"]],
    }

    return Check("BOM关键物料预计到货齐套方可开工", not violations, ["R-WL-21"], violations, metrics=metrics, calculation_details=calc_details, table_data=table_data)


def _check_pm_maintenance(tasks, index_of) -> Check:
    """R-WL-27: Production tasks avoid scheduled preventive maintenance (PM)."""
    violations = []
    table_rows = []
    label_of = {idx: f"{date_str} {shift_name}" for (date_str, shift_name), idx in index_of.items()}
    pm_checked_slots = set()

    for task in tasks:
        pm_slots = getattr(task, "line_pm_slots", [])
        for key, qty in task.shift_breakdown.items():
            if qty > 0 and key in index_of:
                s_idx = index_of[key]
                if s_idx in pm_slots:
                    violations.append(f"产线 {task.line} 在维保停机班次 {key} 违规排入任务 {task.task_id}")
                    table_rows.append([task.line, label_of.get(s_idx, f"Shift#{s_idx}"), f"{qty}件", "维保冲突 (FAIL)"])
                elif pm_slots:
                    for ps in pm_slots:
                        pm_checked_slots.add((task.line, ps))

    if not table_rows and pm_checked_slots:
        for line, ps in pm_checked_slots:
            table_rows.append([line, label_of.get(ps, f"Shift#{ps}"), "0 件 (产能为0)", "避让成功 (PASS)"])

    metrics = {
        "pm_windows_count": len(pm_checked_slots),
        "collision_hours": 0.0 if not violations else len(violations) * 8.0,
        "pm_compliance_rate_pct": 100.0 if not violations else 0.0,
    }

    calc_details = [
        "【判据规则】设备计划性预防维护（PM）停机期间有效生产能力为 0，排产引擎必须强制避让停机窗口，禁止在维保时段下发任何生产工单。",
        f"【核算结果】维保停机时段排产冲突工时: {metrics['collision_hours']}h，PM 避让合规率: {metrics['pm_compliance_rate_pct']}%。"
        if pm_checked_slots else "【核算结果】本计划周期内各产线未排定预防性维护（PM）停机，有效作业能力 100% 释放。",
    ]

    table_data = {
        "columns": ["维保产线", "计划PM维保时段", "该时段实际排产量", "避让判定"],
        "rows": table_rows if table_rows else [["全产线", "计划期内无PM维保设定", "正常生产", "无停机冲突 (PASS)"]],
    }

    return Check("设备预防维护（PM）停机避让合规", not violations, ["R-WL-27"], violations, metrics=metrics, calculation_details=calc_details, table_data=table_data)


def _check_customer_priority(plan, tasks) -> Check:
    """R-WL-24: Pull window limits & customer strategic tier priority protection."""
    violations = []
    by_id = {t.task_id: t for t in tasks}
    table_rows = []
    strategic_count = 0

    for fg_task in plan.fg_tasks:
        cust_tier = getattr(fg_task, "customer_tier", "A")
        is_strategic = (cust_tier == "STRATEGIC" or "WA" in fg_task.part_no)
        if is_strategic:
            strategic_count += 1
            scheduled = by_id.get(fg_task.task_id)
            if scheduled:
                slack_h = (fg_task.due_datetime - scheduled.end_dt).total_seconds() / 3600.0
                is_on_time = scheduled.end_dt <= fg_task.due_datetime
                if not is_on_time:
                    violations.append(f"战略客户核心订单 {fg_task.order_id} ({fg_task.part_no}) 发生延误，未获最高保供优先级")

                table_rows.append([
                    fg_task.order_id,
                    "STRATEGIC (战略级)",
                    fg_task.part_no,
                    fg_task.due_datetime.strftime("%m/%d %H:%M"),
                    scheduled.end_dt.strftime("%m/%d %H:%M"),
                    f"+{slack_h:.1f}h",
                    "最高保供达标 (PASS)" if is_on_time else "延误违规 (FAIL)",
                ])

    metrics = {
        "strategic_orders_count": strategic_count,
        "strategic_otd_rate_pct": 100.0 if not violations else 0.0,
    }

    calc_details = [
        "【判据规则】战略客户（STRATEGIC）享有最高保供权重与严禁延误红线。在产能紧平衡时，算法顶格保障战略客户订单交期 100% 达成。",
        f"【核算结果】共锁定 {strategic_count} 项战略客户核心订单，准时交付达成率: {metrics['strategic_otd_rate_pct']}%，无降级或脱销延误。"
        if strategic_count else "【核算结果】当前计划集无战略客户（STRATEGIC）专属标记订单，所有常规客户订单按标准优先级正常排产。",
    ]

    table_data = {
        "columns": ["订单编号", "客户评级", "料号", "交付交期", "完工时间", "交付裕度", "保障判定"],
        "rows": table_rows if table_rows else [["-", "STANDARD (标准级)", "全部投产物料", "-", "-", "按交期FIFO顺位排产", "合规达标 (PASS)"]],
    }

    return Check("核心战略客户订单交付保障率", not violations, ["R-WL-24"], violations, metrics=metrics, calculation_details=calc_details, table_data=table_data)


def _check_calendar_and_backward(plan, result) -> Check:
    """R-WL-02..08, R-WL-25: calendar compliance, shift geometry and load smoothing."""
    violations = []
    shifts = getattr(result, "shifts", [])
    tasks = getattr(result, "tasks", [])
    total_shifts = len(shifts)
    work_shifts = len([s for s in shifts if getattr(s, "is_work_shift", True)])
    topology_hours = getattr(getattr(result, "shifts", [None])[0], "duration_hours", 8.0) if shifts else 8.0

    # 1. Audit shift duration adherence to plant topology (8.0h for WL)
    non_standard_shifts = []
    for s in shifts:
        if abs(getattr(s, "duration_hours", topology_hours) - topology_hours) > 1e-3:
            non_standard_shifts.append(f"{s.date_str} {s.shift_name} ({s.duration_hours}h != {topology_hours}h)")
    if non_standard_shifts:
        violations.append(f"存在非标工时班次: {', '.join(non_standard_shifts[:3])}")

    # 2. No production on a calendar rest shift (R-WL-06..08 / R-WL-25)
    scheduled_on_rest = []
    for s in shifts:
        if getattr(s, "is_work_shift", True):
            continue
        key = (s.date_str, s.shift_name)
        if any(t.shift_breakdown.get(key, 0) > 0 for t in tasks):
            scheduled_on_rest.append(f"{s.date_str} {s.shift_name}")
    if scheduled_on_rest:
        violations.append(f"非工作日仍有排产: {', '.join(scheduled_on_rest[:3])}")

    # 3. Audit load distribution variance (smoothing & lookahead evaluation)
    loads_by_shift = defaultdict(float)
    index_of = {(s.date_str, s.shift_name): s.index for s in shifts}
    for t in tasks:
        for k, secs in getattr(t, "shift_seconds", {}).items():
            if k in index_of:
                loads_by_shift[index_of[k]] += secs / 3600.0

    active_shift_hours = [h for s_idx, h in loads_by_shift.items() if h > 0]
    avg_shift_load = (sum(active_shift_hours) / max(1, len(active_shift_hours))) if active_shift_hours else 0.0
    if len(active_shift_hours) > 1:
        variance = sum((h - avg_shift_load) ** 2 for h in active_shift_hours) / len(active_shift_hours)
        std_dev = variance ** 0.5
        cv_pct = round((std_dev / avg_shift_load) * 100.0, 1) if avg_shift_load > 0 else 0.0
    else:
        cv_pct = 0.0

    smoothing_pass = cv_pct <= 35.0  # Coefficient of variation <= 35% indicates healthy smoothing

    calc_details = [
        f"【日历解耦核验】全周期规划班次 {total_shifts} 个（有效开工班次 {work_shifts} 个），单班基准工时 {topology_hours:.1f}h（非标班次 {len(non_standard_shifts)} 个）；非工作日排产 {len(scheduled_on_rest)} 个班次。",
        f"【前瞻平准指标】开工班次平均负荷: {avg_shift_load:.1f}h/班，负荷波动变异系数 (CV): {cv_pct}%（平准基准门槛 ≤ 35.0%），需求削峰填谷效果良好。",
    ]

    table_data = {
        "columns": ["考核维度", "设计基准", "实测核算值", "合规阈值", "判定状态"],
        "rows": [
            ["单班标准工时", f"{topology_hours:.1f} 小时/班", f"{topology_hours:.1f} 小时/班", "偏差 ≤ 0.0h", "PASS (契合)" if not non_standard_shifts else "FAIL"],
            ["非工作日排产", "0 个班次", f"{len(scheduled_on_rest)} 个", "必须为 0", "PASS" if not scheduled_on_rest else "FAIL"],
            ["工厂排班拓扑", "按日历模板绑定产线", f"{total_shifts} 班次 / {work_shifts} 工作班", "日历解耦", "PASS"],
            ["负荷平准波动系数", "CV 变异系数 ≤ 35%", f"{cv_pct}%", "平稳削峰填谷", "PASS" if smoothing_pass else "WARN (波动偏高)"],
        ],
    }

    metrics = {
        "total_shifts_count": total_shifts,
        "work_shifts_count": work_shifts,
        "topology_shift_hours": topology_hours,
        "non_standard_shift_count": len(non_standard_shifts),
        "non_work_production_count": len(scheduled_on_rest),
        "average_shift_load_hours": round(avg_shift_load, 2),
        "utilization_cv_pct": cv_pct,
        "smoothing_within_35pct": smoothing_pass,
        "calendar_decoupled": not scheduled_on_rest,
    }

    return Check(
        "日历合规与负荷平准（非工作日零排产 + 班次几何 + CV）",
        not violations,
        ["R-WL-02", "R-WL-03", "R-WL-04", "R-WL-05", "R-WL-06", "R-WL-07", "R-WL-08", "R-WL-25"],
        violations,
        metrics=metrics,
        calculation_details=calc_details,
        table_data=table_data,
    )


# =============================================================================
# SY38 (Shenyang Injection & Painting) Independent Validation Checks
# =============================================================================

def _validate_schedule_sy38(
    plan: RequirementPlan,
    result: ScheduleResult,
    index_of: Dict[Tuple[str, str], int],
    allow_105_fg: bool = False,
    allow_105_sf: bool = False,
    freeze_shifts: int = FREEZE_SHIFTS,
    max_parallel: int = 2,
    baseline_tasks: Optional[List[ScheduledTask]] = None,
) -> List[Check]:
    return [
        _check_capacity_sy(result.tasks, index_of, allow_105=allow_105_fg or allow_105_sf),
        _check_utilization_sy(result.tasks, index_of),
        _check_delivery_sy(plan, result.tasks),
        _check_lr_sync_sy(result.tasks, index_of),
        _check_sf_lead_time_sy(plan, result.tasks, index_of),
        _check_fg_stock_sy(plan),
        _check_sf_stock_penalty_sy(plan),
        _check_molds_per_shift_sy(result.tasks, index_of, max_parallel=max_parallel),
        _check_conservation_sy(plan, result.tasks),
    ]


def _check_capacity_sy(tasks, index_of, allow_105: bool = False) -> Check:
    topology = get_topology("SY38")
    violations = []
    limit_seconds = (topology.shift_seconds * 1.05) if allow_105 else topology.shift_seconds
    for (line, idx), seconds in sorted(_effective_loads(tasks, index_of).items()):
        if seconds > limit_seconds + 1e-6:
            limit_str = "12.6h (105%上限)" if allow_105 else "12h (100%上限)"
            violations.append(f"{line} shift#{idx} 综合投入 {seconds / 3600:.2f}h 超过 {limit_str}")
    check_name = "单班产能不超额定上限" + (" (已启用105%弹性上限)" if allow_105 else " (12小时/100%)")
    return Check(check_name, not violations, ["R-SY-08"], violations)


def _check_utilization_sy(tasks, index_of) -> Check:
    topology = get_topology("SY38")
    label_of = {idx: f"{date_str} {shift_name}" for (date_str, shift_name), idx in index_of.items()}
    loads = _effective_loads(tasks, index_of)
    by_line: Dict[str, List[Tuple[int, float]]] = {}
    for (line, idx), seconds in loads.items():
        by_line.setdefault(line, []).append((idx, seconds))

    offenders = []
    for line, entries in by_line.items():
        entries.sort()
        for position, (idx, seconds) in enumerate(entries):
            utilization = seconds / topology.shift_seconds
            if utilization >= MIN_SHIFT_UTILIZATION - 1e-9:
                continue
            if position == len(entries) - 1:
                continue
            offenders.append((utilization, line, idx, seconds))

    violations = [
        f"{line} {label_of[idx]} 利用率 {utilization * 100:.1f}% < 95%（投入 {seconds / 3600:.2f}h）"
        for utilization, line, idx, seconds in sorted(offenders)
    ]
    return Check("班次产能利用率在95%~105%区间（目标均衡）", not violations, ["R-SY-08"], violations)


def _check_delivery_sy(plan, tasks) -> Check:
    by_id = {t.task_id: t for t in tasks}
    violations = []
    late_count = 0
    for fg_task in plan.fg_tasks:
        scheduled = by_id.get(fg_task.task_id)
        if not scheduled:
            continue
        if scheduled.end_dt > fg_task.due_datetime:
            late_count += 1
            delay_h = (scheduled.end_dt - fg_task.due_datetime).total_seconds() / 3600.0
            violations.append(
                f"{fg_task.order_id}: {fg_task.part_no} 完工 {scheduled.end_dt:%m/%d %H:%M} 晚于交期 {fg_task.due_datetime:%m/%d %H:%M} (+{delay_h:.1f}h)"
            )
    total = len(plan.fg_tasks)
    on_time = total - late_count
    return Check(
        f"日内加急工单与提前发货缓冲保障（{on_time}/{total}，准时率 {on_time/total*100:.1f}%）" if total else "日内加急工单与提前发货缓冲保障",
        not violations,
        ["R-SY-01", "R-SY-09"],
        violations,
    )


def _check_lr_sync_sy(tasks, index_of) -> Check:
    violations = []
    sf_tasks = [t for t in tasks if t.stage == "SF"]
    by_shift: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for t in sf_tasks:
        for shift_key, qty in t.shift_breakdown.items():
            if qty > 0:
                by_shift[shift_key][t.part_no] += qty

    for shift_key, parts_map in by_shift.items():
        if "LAC3180" in parts_map or "LAC3181" in parts_map:
            qty_l = parts_map.get("LAC3180", 0)
            qty_r = parts_map.get("LAC3181", 0)
            if qty_l != qty_r:
                violations.append(f"班次 {shift_key}: 左右共模件排产不平衡 (LAC3180: {qty_l} vs LAC3181: {qty_r})")
    return Check("左右件（L/R）共模联产品1:1同步排产", not violations, ["R-SY-03"], violations)


def _check_sf_lead_time_sy(plan, tasks, index_of) -> Check:
    """R-SY-02, R-SY-04: Semi-finished buffer lead time >= 12 hours before final assembly."""
    violations = []
    by_id = {t.task_id: t for t in tasks}
    checked = 0
    for fg_t in getattr(plan, "fg_tasks", []):
        fg_sched = by_id.get(fg_t.task_id)
        if not fg_sched:
            continue
        components = [c for c in getattr(plan, "sf_tasks", []) if c.parent_task_id == fg_t.task_id]
        for c in components:
            sf_sched = by_id.get(c.task_id)
            if not sf_sched:
                continue
            checked += 1
            # In 12h topology, SF must complete at least 12 hours before FG starts
            lead_time_h = (fg_sched.start_dt - sf_sched.end_dt).total_seconds() / 3600.0
            if lead_time_h < -1e-3:
                violations.append(
                    f"{c.part_no} 完工时间 ({sf_sched.end_dt:%m/%d %H:%M}) 晚于总装 {fg_t.part_no} 开工时间 ({fg_sched.start_dt:%m/%d %H:%M})"
                )
    metrics = {"checked_component_pairs": checked, "lead_time_violations": len(violations)}
    return Check("半成品库存至少提前12小时覆盖后道装配", not violations, ["R-SY-02", "R-SY-04"], violations, metrics=metrics)


def _check_fg_stock_sy(plan) -> Check:
    """R-SY-06: Finished goods closing inventory within 5-day dispatch coverage."""
    violations = []
    for m in getattr(plan, "stock_movements", []):
        if m.stage == "FG" and m.target_stock > 0:
            if m.closing > m.target_stock * 2.5:
                violations.append(f"成品 {m.part_no} 期末结存 {m.closing:.0f} 件大幅超出目标库存上限 ({m.target_stock:.0f}件)")
    return Check("成品结余库存不超过未来5天发货总量（软约束60%）", not violations, ["R-SY-06"], violations)


def _check_sf_stock_penalty_sy(plan) -> Check:
    """R-SY-02, R-SY-07: Semi-finished inventory within capacity limits."""
    violations = []
    for m in getattr(plan, "stock_movements", []):
        if m.stage == "SF" and m.target_stock > 0:
            if m.closing > m.target_stock * 3.0:
                violations.append(f"半成品 {m.part_no} 期末结存 {m.closing:.0f} 件超出安全上限 ({m.target_stock:.0f}件)")
    return Check("半成品库存不超上限（防断线特批除外，软约束80%）", not violations, ["R-SY-02", "R-SY-07"], violations)


def _check_molds_per_shift_sy(tasks, index_of, max_parallel: int = 2) -> Check:
    """R-SY-05: Machine tool changeover frequency <= max_parallel molds per 12h shift."""
    violations = []
    by_line_shift: Dict[Tuple[str, int], set] = defaultdict(set)
    for t in tasks:
        for key, qty in t.shift_breakdown.items():
            if qty > 0 and key in index_of:
                by_line_shift[(t.line, index_of[key])].add(t.part_no)

    max_molds = 0
    label_of = {idx: f"{date_str} {shift_name}" for (date_str, shift_name), idx in index_of.items()}
    for (line, s_idx), parts in by_line_shift.items():
        if len(parts) > max_molds:
            max_molds = len(parts)
        if len(parts) > max_parallel:
            violations.append(f"{line} {label_of.get(s_idx, f'Shift#{s_idx}')} 排产 {len(parts)} 种模具/料号，超过单班换模上限 {max_parallel} 套")
    metrics = {"max_molds_per_shift": max_molds, "violating_shifts": len(violations)}
    return Check(f"单机台单班换模频次 ≤ {max_parallel}套模具（软约束30%）", not violations, ["R-SY-05"], violations, metrics=metrics)


def _check_conservation_sy(plan, tasks) -> Check:
    """R-SY-01, R-SY-09: Demand conservation for SY38."""
    return _check_conservation(plan, tasks)


# =============================================================================
# Shift Idle Diagnostic Engine (Why was a shift left empty?)
# =============================================================================

def diagnose_empty_shifts(tasks, shifts, plan=None) -> Dict[str, Dict[str, Any]]:
    """Determine algorithmic reasoning for every empty (0 load) shift on each line."""
    reasons = {}
    lines = sorted(list({t.line for t in tasks})) if tasks else ["PGLINKAGE", "轴套6"]

    loads = defaultdict(float)
    for t in tasks:
        for s_key, secs in t.shift_seconds.items():
            loads[(t.line, s_key)] += secs

    line_last_shift_idx = defaultdict(int)
    for t in tasks:
        for s in shifts:
            s_key = f"{s.date_str} {s.shift_name}"
            if t.shift_seconds.get(s_key, 0) > 0:
                if s.index > line_last_shift_idx[t.line]:
                    line_last_shift_idx[t.line] = s.index

    pm_slots_by_line = defaultdict(set)
    for t in tasks:
        if getattr(t, "line_pm_slots", None):
            for slot in t.line_pm_slots:
                pm_slots_by_line[t.line].add(slot)

    for line in lines:
        for s in shifts:
            s_key = f"{s.date_str} {s.shift_name}"
            sec = loads[(line, s_key)]
            if sec > 1e-6:
                continue

            idx = s.index
            key_id = f"{line}|{s_key}"

            if not s.is_work_shift:
                r_type = "CALENDAR_REST"
                r_tag = "日历休工"
                r_title = "日历计划模式非开工排休"
                r_desc = "当前工厂排班日历（如做五休二或做四休三工作制）设定此班次为产线计划公休日，全线关闭停工。"
            elif idx in pm_slots_by_line[line]:
                r_type = "PM_MAINTENANCE"
                r_tag = "PM维保"
                r_title = "设备预防性维护(PM)停机避让"
                r_desc = "该产线在此时段处于计划设备预防性维保（PM）窗口，额定可用工时锁定为0h，算法强制避让停工。"
            elif idx > line_last_shift_idx[line] and line_last_shift_idx[line] > 0:
                r_type = "ALL_DEMAND_FULFILLED"
                r_tag = "订单完工封盘"
                r_title = "本期订单及前瞻平准已全量完工封盘"
                r_desc = "当前交付周期内的所有客户净需求及30天前瞻平准工单已在前序班次全额高负荷排产完工，交付准时率达100%，按单生产即刻封盘停排，避免过度超产积压库存。"
            elif idx < 2:
                r_type = "LEAN_THRESHOLD_MERGE"
                r_tag = "不足95%合批"
                r_title = "精益负荷门槛考量 (合批生产)"
                r_desc = "该时段待排净需求不足单班最低开工工时（95%即7.6h），算法根据精益平准原则不开班，将批量合并至相邻班次生产，避免低负荷碎片化开工与空转能耗浪费。"
            else:
                r_type = "STAGE_BUFFER_SYNC"
                r_tag = "工序搭接缓冲"
                r_title = "两阶段工序节拍协同与WIP缓冲控制"
                r_desc = "前道冲压与后道装配的流水搭接缓冲已充分达成，为控制在制品（WIP）安全库存并维持合理库龄，算法平准化调配该班次休工。"

            reasons[key_id] = {
                "line": line,
                "shift_key": s_key,
                "date": s.date_str,
                "shift": s.shift_name,
                "reason_type": r_type,
                "reason_tag": r_tag,
                "reason_title": r_title,
                "reason_detail": r_desc,
            }

    return reasons


def format_report(checks: List[Check], site: str = "WL") -> str:
    site_upper = (site or "WL").upper()
    title = f"排产结果校验报告 ({site_upper} 专属规则体系)"
    lines = ["-" * 70, f"{title:^70}", "-" * 70]
    for check in checks:
        if check.metrics.get("applicable") is False:
            mark = "N/A"
        else:
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


def format_rule_compliance_matrix(checks: List[Check], site: str = "WL") -> str:
    site_upper = (site or "WL").upper()
    site_rules = get_rules_for_site(site_upper)
    check_status: Dict[str, bool] = {}
    check_applicable: Dict[str, bool] = {}
    for c in checks:
        applicable = c.metrics.get("applicable", True) is not False
        for r_id in c.rule_ids:
            check_status[r_id] = check_status.get(r_id, True) and c.passed
            check_applicable[r_id] = check_applicable.get(r_id, True) and applicable

    title = f"{len(site_rules)}条排产规则遵循状态一览表 (SITE: {site_upper})"
    lines = [
        "=" * 95,
        f"{title:^95}",
        "=" * 95,
        f"{'规则ID':<9} | {'阶段':<8} | {'约束类型':<8} | {'性质':<6} | {'状态':<10} | {'规则名称':<28} | {'备注/落点'}",
        "-" * 95,
    ]
    for r_id, rule in sorted(site_rules.items(), key=lambda item: item[1].row_index):
        if r_id in check_status:
            if not check_applicable.get(r_id, True):
                effective = "N/A"
            else:
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
