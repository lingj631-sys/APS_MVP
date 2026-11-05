# -*- coding: utf-8 -*-
"""Why did the plan fail?  Structured, data-backed diagnosis for the infeasible case.

The solver only reports "no feasible schedule".  A planner cannot act on that, so
this module rebuilds the story from the same objects the solver used:

* ``order_gaps``   – per order, due date vs cumulative capacity (the hard numbers)
* ``line_details`` – what each line makes, FG or SF, hours, who competes for it
* ``data_checks``  – input problems that make the instance impossible on its own
* ``rule_checks``  – which enabled rules bind, with the numbers that prove it
* ``algorithm_checks`` – self-audit of the ingestion / capacity maths

Everything here is pure computation: no solver call, no mutation of the plan.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

HOUR_SECONDS = 3600.0

# Rule ids are shown to planners, so keep the human name next to the code.
RULE_NAMES = {
    "R-FG-01": "成品交期保障（硬交期）",
    "R-FG-02": "成品按 MPQ 整批投产",
    "R-FG-03": "成品班次利用率 95% 硬下限",
    "R-FG-13": "同产线每班不超过 3 个型号",
    "R-SF-01": "半成品班次利用率 95% 硬下限",
    "R-SF-02": "半成品顺序拉动（先半成品后成品）",
    "R-SF-04": "冻结窗口",
    "R-TL-01": "模具/工装互斥",
    "R-MT-01": "物料到货后才可投产",
    "R-PM-01": "设备保养占用班次",
    "R-WL-10": "冻结窗口（温岭）",
    "R-WL-12": "顺序拉动（温岭）",
    "R-WL-20": "成品交期保障（温岭）",
    "R-WL-23": "物料齐套（温岭）",
    "R-WL-26": "班次利用率 95%（温岭）",
    "R-MT-01": "物料到货后才可投产",
}


def _task_hours(task: Any) -> float:
    """Processing hours exactly as the scheduler accounts them."""
    return (float(getattr(task, "lots", 0) or 0) * float(getattr(task, "lot_millis", 0) or 0)) / 1000.0 / HOUR_SECONDS


def _fmt(dt: Optional[datetime]) -> str:
    return dt.strftime("%m/%d %H:%M") if dt else ""


def _line_capacity_until(shifts: Sequence[Any], moment: datetime) -> float:
    return sum(float(s.duration_hours) for s in shifts if s.end_dt <= moment)


def _earliest_finish(shifts: Sequence[Any], work_hours: float, shift_hours: float) -> Optional[datetime]:
    """When the line could finish this much work if it ran flat out from shift 0."""
    if not shifts or shift_hours <= 0:
        return None
    need = max(0, int(math.ceil(work_hours / shift_hours - 1e-9)))
    if need <= 0:
        return shifts[0].start_dt
    idx = min(need, len(shifts))
    return shifts[idx - 1].end_dt


# --------------------------------------------------------------------------
# 1. order gap table
# --------------------------------------------------------------------------
def _stock_rows(plan: Any, snapshot: Any) -> Dict[str, Dict[str, float]]:
    """Opening stock / stock coverage per FG task, replaying the loader's netting.

    The walk has to cover *every* order of the part, including the ones that are
    fully covered by stock (they still consume it), otherwise the later orders see
    stock that is already gone.
    """
    order_map = {o.order_id: o for o in getattr(snapshot, "orders", [])}
    fg_params = getattr(snapshot, "fg_params", {}) or {}
    task_by_order = {t.order_id: t for t in getattr(plan, "fg_tasks", [])}
    avail: Dict[str, float] = defaultdict(float, getattr(snapshot, "initial_stock", {}) or {})
    out: Dict[str, Dict[str, float]] = {}

    for order in sorted(getattr(snapshot, "orders", []), key=lambda o: (o.due_datetime, o.part_no)):
        part = order.part_no
        param = fg_params.get(part)
        if param is None:
            continue
        target = float(getattr(param, "target_stock", 0.0) or 0.0)
        before = float(avail[part])
        demand = float(order.demand_quantity)
        net_need = max(0.0, demand + target - before)
        covered = max(0.0, min(demand, demand - net_need))

        task = task_by_order.get(order.order_id)
        produced = float(getattr(task, "quantity", 0.0) or 0.0) if task else 0.0
        mpq = float(getattr(task, "mpq", 0) or 0) or float(getattr(param, "mpq", 0) or 0) or 1.0
        expected = math.ceil(net_need / mpq - 1e-9) * mpq if net_need > 0 else 0.0
        avail[part] = before + produced - demand

        if task is None:
            continue
        out[task.task_id] = {
            "opening": before,
            "target_stock": target,
            "demand": demand,
            "covered": covered,
            "net": net_need,
            "produced": produced,
            "expected": expected,
        }
    return out


def build_order_gaps(plan: Any, shifts: Sequence[Any], snapshot: Any, shift_hours: float) -> List[Dict[str, Any]]:
    """One row per FG order: due, qty, stock, line capacity until due, cumulative need, gap.

    ``cum_required`` is *all* work on that line (FG and SF) due at or before this
    order's due date — that is what actually has to fit before the shift closes.
    The pre-solver check only summed FG work, which is why semi-finished lines
    never showed up in its bottleneck list.
    """
    if not shifts:
        return []
    stock = _stock_rows(plan, snapshot)
    line_tasks: Dict[str, List[Any]] = defaultdict(list)
    for task in getattr(plan, "tasks", []):
        line_tasks[task.line].append(task)

    rows: List[Dict[str, Any]] = []
    for line, tasks in line_tasks.items():
        ordered = sorted(tasks, key=lambda t: (t.due_datetime, t.task_id))
        cum = 0.0
        for task in ordered:
            cum += _task_hours(task)
            if task.stage != "FG":
                continue
            info = stock.get(task.task_id, {})
            capacity = _line_capacity_until(shifts, task.due_datetime)
            gap = max(0.0, cum - capacity)
            earliest = _earliest_finish(shifts, cum, shift_hours)
            late_hours = 0.0
            if earliest is not None and earliest > task.due_datetime:
                late_hours = (earliest - task.due_datetime).total_seconds() / HOUR_SECONDS
            rows.append({
                "line": line,
                "stage": "FG",
                "order_id": task.order_id,
                "part_no": task.part_no,
                "description": getattr(task, "description", ""),
                "due": _fmt(task.due_datetime),
                "due_iso": task.due_datetime.isoformat(timespec="minutes"),
                "demand_qty": round(info.get("demand", 0.0), 1),
                "opening_stock": round(info.get("opening", 0.0), 1),
                "stock_covered": round(info.get("covered", 0.0), 1),
                "net_qty": round(info.get("net", 0.0), 1),
                "required_hours": round(_task_hours(task), 1),
                "cum_required_hours": round(cum, 1),
                "cum_capacity_hours": round(capacity, 1),
                "gap_hours": round(gap, 1),
                "earliest_finish": _fmt(earliest),
                "late_hours": round(late_hours, 1),
                "verdict": "交期不可达" if gap > 0.05 else "可达成",
            })
    # Gap rows first (that is what the planner must act on), then chronological.
    rows.sort(key=lambda r: (0 if r["gap_hours"] > 0.05 else 1, r["due_iso"], r["line"]))
    return rows


# --------------------------------------------------------------------------
# 2. line detail
# --------------------------------------------------------------------------
def build_line_details(plan: Any, shifts: Sequence[Any], shift_hours: float) -> List[Dict[str, Any]]:
    """What each line makes, how loaded it is, and who competes for it."""
    total_capacity = sum(float(s.duration_hours) for s in shifts)
    line_tasks: Dict[str, List[Any]] = defaultdict(list)
    for task in getattr(plan, "tasks", []):
        line_tasks[task.line].append(task)

    # reverse BOM: SF part -> FG parts that consume it
    consumers: Dict[str, List[str]] = defaultdict(list)
    for fg, comps in (getattr(plan, "bom", {}) or {}).items():
        for sf_pn, _ratio in comps:
            consumers[sf_pn].append(fg)

    details: List[Dict[str, Any]] = []
    for line, tasks in line_tasks.items():
        stages = sorted({t.stage for t in tasks})
        hours = sum(_task_hours(t) for t in tasks)
        parts: Dict[tuple, Dict[str, Any]] = {}
        orders: Dict[str, float] = defaultdict(float)
        for task in tasks:
            key = (task.stage, task.part_no)
            entry = parts.setdefault(key, {"stage": task.stage, "part_no": task.part_no,
                                           "description": getattr(task, "description", ""),
                                           "hours": 0.0, "orders": set()})
            entry["hours"] += _task_hours(task)
            entry["orders"].add(task.order_id)
            orders[task.order_id] += _task_hours(task)

        part_rows = []
        for entry in sorted(parts.values(), key=lambda e: -e["hours"]):
            part_rows.append({
                "stage": entry["stage"],
                "part_no": entry["part_no"],
                "description": entry["description"],
                "hours": round(entry["hours"], 1),
                "share": round(entry["hours"] / hours * 100, 1) if hours else 0.0,
                "orders": len(entry["orders"]),
                "feeds_fg": sorted({fg for fg in consumers.get(entry["part_no"], [])})[:6],
            })

        order_rows = [
            {"order_id": oid, "hours": round(h, 1)}
            for oid, h in sorted(orders.items(), key=lambda kv: -kv[1])[:8]
        ]
        is_fg = "FG" in stages
        is_sf = "SF" in stages
        stage_label = "成品线" if is_fg and not is_sf else ("半成品线" if is_sf and not is_fg else "成品+半成品混线")
        details.append({
            "line": line,
            "stages": stages,
            "stage_label": stage_label,
            "is_fg": is_fg,
            "is_sf": is_sf,
            "required_hours": round(hours, 1),
            "capacity_hours": round(total_capacity, 1),
            "utilization_rate": round(hours / total_capacity * 100, 1) if total_capacity else 0.0,
            "is_overloaded": total_capacity > 0 and hours > total_capacity,
            "part_count": len(part_rows),
            "order_count": len(orders),
            "parts": part_rows,
            "top_orders": order_rows,
        })
    details.sort(key=lambda d: -d["required_hours"])
    return details


# --------------------------------------------------------------------------
# 3. data checks
# --------------------------------------------------------------------------
def build_data_checks(
    plan: Any,
    snapshot: Any,
    shifts: Sequence[Any],
    order_gaps: Sequence[Dict[str, Any]],
    shift_hours: float,
    unassigned_parts: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []

    gap_rows = [r for r in order_gaps if r["gap_hours"] > 0.05]
    if gap_rows:
        worst = max(gap_rows, key=lambda r: r["late_hours"])
        checks.append({
            "level": "error",
            "code": "DUE_BEFORE_LEADTIME",
            "title": "客户的要货时间早于最快可完工时间",
            "detail": (
                f"{len(gap_rows)} 张工单在交期前放不下它的累计工时。最严重的是 "
                f"{worst['order_id']}（{worst['part_no']}，交期 {worst['due']}）："
                f"期初库存 {worst['opening_stock']:.0f}、要 {worst['demand_qty']:.0f} 件，"
                f"到交期只剩 {worst['cum_capacity_hours']:.1f}h 产能，而该线累计要做 "
                f"{worst['cum_required_hours']:.1f}h；即使从第一天满载不停，也要 {worst['earliest_finish']} 才能做完。"
            ),
            "metrics": {
                "orders": len(gap_rows),
                "max_gap_hours": round(max(r["gap_hours"] for r in gap_rows), 1),
                "max_late_hours": round(worst["late_hours"], 1),
            },
        })

    no_stock = [r for r in gap_rows if r["opening_stock"] <= 0]
    if no_stock:
        checks.append({
            "level": "warn",
            "code": "NO_STOCK_NO_LEADTIME",
            "title": "这些工单没有可用库存，也没有生产提前期",
            "detail": (
                f"{len(no_stock)} 张超期工单的期初库存为 0（或已被前面的订单吃掉），"
                "只能靠本次排产生产；而下单/到货没有预留任何提前期，"
                "属于订单数据本身不可执行，而不是排产算法排不出来。"
            ),
            "metrics": {"orders": len(no_stock),
                        "orders_list": [r["order_id"] for r in no_stock][:12]},
        })

    # due-date clustering
    orders = list(getattr(snapshot, "orders", []))
    if orders:
        by_day: Dict[str, int] = defaultdict(int)
        for o in orders:
            by_day[o.due_datetime.strftime("%m/%d")] += 1
        top_days = sorted(by_day.items(), key=lambda kv: -kv[1])[:3]
        if len(orders) >= 10:
            checks.append({
                "level": "info" if top_days[0][1] / len(orders) < 0.5 else "warn",
                "code": "DUE_CLUSTER",
                "title": "交期高度聚集",
                "detail": (
                    f"计划期内 {len(orders)} 张订单，最集中的 3 天是 "
                    + "、".join(f"{d}（{n} 单）" for d, n in top_days)
                    + "。交期越集中，累计曲线越陡，越容易撞上产能台阶。"
                ),
                "metrics": {"orders": len(orders), "top_days": [{"date": d, "orders": n} for d, n in top_days]},
            })

    # BOM parts without master data
    missing_params = []
    for fg, comps in (getattr(snapshot, "bom", {}) or {}).items():
        for sf_pn, _ratio in comps:
            if sf_pn not in (getattr(snapshot, "sf_params", {}) or {}) and sf_pn not in missing_params:
                missing_params.append(sf_pn)
    if missing_params:
        checks.append({
            "level": "warn",
            "code": "BOM_PART_NO_PARAM",
            "title": "BOM 里引用了没有主数据的半成品",
            "detail": (
                "这些料号出现在成品 BOM 里，但 Parameters-SFN 没有它们的 CT/产线/MPQ，"
                "齐套计算会被跳过，实际生产可能漏工序：" + "、".join(missing_params[:10])
            ),
            "metrics": {"parts": missing_params[:10]},
        })

    if unassigned_parts:
        checks.append({
            "level": "error",
            "code": "UNASSIGNED_LINE",
            "title": "有料号没有配置产线",
            "detail": "这些料号在 Routing-Machines 没有『主候选线』，任务落到“未分配产线”，无法排到车间："
                      + "、".join(list(unassigned_parts)[:10]),
            "metrics": {"parts": list(unassigned_parts)[:10]},
        })

    zero_ct = [t.task_id for t in getattr(plan, "tasks", []) if (getattr(t, "ct_seconds", 0) or 0) <= 0]
    if zero_ct:
        checks.append({
            "level": "error",
            "code": "ZERO_CT",
            "title": "工艺 CT 为 0",
            "detail": f"{len(zero_ct)} 个任务的单件工时 ≤ 0，产能会被严重低估。",
            "metrics": {"tasks": zero_ct[:10]},
        })

    return checks


# --------------------------------------------------------------------------
# 4. rule checks
# --------------------------------------------------------------------------
def build_rule_checks(
    plan: Any,
    snapshot: Any,
    shifts: Sequence[Any],
    shift_hours: float,
    order_gaps: Sequence[Dict[str, Any]],
    line_details: Sequence[Dict[str, Any]],
    enabled_rules: Optional[Iterable[str]] = None,
    pulled_orders: int = 0,
    disabled_templates: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    enabled = set(enabled_rules or [])
    disabled = set(disabled_templates or [])
    # legacy rule id -> compiled template, so a rule switched off in the rule set can
    # be reported as "not in force" instead of "suspicious".
    RULE_TEMPLATE = {
        "R-FG-02": "min_batch",
        "R-FG-13": "concurrency",
        "R-SF-02": "sequence",
        "R-SF-03": "material_balance",
        "R-MT-01": "material_ready",
        "R-PM-01": "pm_block",
        "R-TL-01": "tooling_exclusivity",
    }
    checks: List[Dict[str, Any]] = []
    total_capacity = sum(float(s.duration_hours) for s in shifts) or 1.0
    total_work = sum(_task_hours(t) for t in getattr(plan, "tasks", []))
    line_count = max(1, len({t.line for t in getattr(plan, "tasks", [])}))
    plant_capacity = total_capacity * line_count

    # --- hard due dates
    gap_rows = [r for r in order_gaps if r["gap_hours"] > 0.05]
    if gap_rows:
        worst = max(gap_rows, key=lambda r: r["gap_hours"])
        checks.append({
            "rule_id": "R-FG-01",
            "rule_name": RULE_NAMES["R-FG-01"],
            "nature": "hard",
            "status": "blocking",
            "evidence": (
                f"{len(gap_rows)} 处累计缺口，最大 {worst['gap_hours']:.1f}h（{worst['order_id']} @ {worst['line']}）。"
                "只要交期是硬约束，这些缺口就等价于“无解”。"
            ),
            "numbers": {"gap_points": len(gap_rows),
                        "max_gap_hours": round(worst["gap_hours"], 1)},
        })

    # --- 95% per-shift floor (counting argument, per line)
    tight_lines = []
    for row in line_details:
        slack = row["capacity_hours"] - row["required_hours"]
        if slack < shift_hours:
            tight_lines.append((row["line"], row["required_hours"], row["capacity_hours"], slack))
    checks.append({
        "rule_id": "R-FG-03",
        "rule_name": RULE_NAMES["R-FG-03"],
        "nature": "hard",
        "status": "info",
        "evidence": (
            "95% 是『每个已开班次』的硬下限（收官班次除外）。按整线口径它不是直接吃产能，"
            "而是要求每个班都凑够 7.6h 才准开机。"
            + (f"本期有 {len(tight_lines)} 条线的剩余裕度不足一个班，换模、待料、波动一来就没有回旋空间："
               + "、".join(f"{l}（{w:.0f}h / {c:.0f}h，余 {s:.1f}h）" for l, w, c, s in tight_lines[:4])
               if tight_lines else "每条线都留有至少一个班的裕度。")
        ),
        "numbers": {"tight_lines": len(tight_lines), "threshold_hours": round(shift_hours * 0.95, 2)},
    })

    # --- concurrency <= 3 models per shift
    conflicts = [(row["line"], row["part_count"]) for row in line_details if row["part_count"] > 3]
    checks.append({
        "rule_id": "R-FG-13",
        "rule_name": RULE_NAMES["R-FG-13"],
        "nature": "one_shift",
        "status": "suspected" if conflicts else "ok",
        "evidence": (
            "规则限制的是『同一产线同一班次最多 3 个型号』，不是整期型号数；"
            "但型号越多，越难把同一班塞满 3 个以内又要凑够 95%："
            + ("；".join(f"{line} 本期 {n} 个型号" for line, n in sorted(conflicts, key=lambda x: -x[1])[:5])
               if conflicts else "各产线型号数都在 3 个以内，未触发。")
        ),
        "numbers": {"lines_over_3": len(conflicts)},
    })

    # --- tooling / mold exclusivity
    mold_parts: Dict[str, set] = defaultdict(set)
    for task in getattr(plan, "tasks", []):
        mold = getattr(task, "mold_id", None)
        if mold:
            mold_parts[mold].add(task.part_no)
    shared = {m: p for m, p in mold_parts.items() if len(p) > 1}
    checks.append({
        "rule_id": "R-TL-01",
        "rule_name": RULE_NAMES["R-TL-01"],
        "nature": "hard",
        "status": "suspected" if shared else "ok",
        "evidence": (
            f"{len(shared)} 套模具在本期被多个料号共用，模具一次只能在一个机台上，"
            "共用模具的订单只能串行生产：" if shared else "本期没有多料号共用同一套模具。"
        ) + (("；".join(f"模具 {m} → {'/'.join(sorted(p)[:4])}" for m, p in list(shared.items())[:3])) if shared else ""),
        "numbers": {"shared_molds": len(shared)},
    })

    # --- material arrival (only parts that really feed the plan)
    consumed_parts = {t.part_no for t in getattr(plan, "tasks", [])}
    for _fg, comps in (getattr(snapshot, "bom", {}) or {}).items():
        for sf_pn, _ratio in comps:
            if sf_pn in (getattr(snapshot, "sf_params", {}) or {}):
                consumed_parts.add(sf_pn)
    late_arrivals = []
    horizon_end = max((s.end_dt for s in shifts), default=None)
    for ma in getattr(snapshot, "material_arrivals", []) or []:
        if ma.part_no not in consumed_parts:
            continue
        try:
            eta = datetime.strptime(str(ma.eta_date).replace("/", "-")[:10], "%Y-%m-%d")
        except Exception:
            continue
        if horizon_end and eta > horizon_end:
            late_arrivals.append((ma.part_no, ma.eta_date))
    checks.append({
        "rule_id": "R-MT-01",
        "rule_name": RULE_NAMES["R-MT-01"],
        "nature": "hard",
        "status": "suspected" if late_arrivals else "ok",
        "evidence": (
            "物料到货晚于计划期末，依赖它的工单在本期内永远等不到料："
            + "、".join(f"{p}（ETA {d}）" for p, d in late_arrivals[:5])
            if late_arrivals else "本期实际要用到的料，ETA 都在计划期内（或没有到货约束），未触发。"
        ),
        "numbers": {"late_arrivals": len(late_arrivals)},
    })

    # --- PM maintenance
    pm_total = len(getattr(snapshot, "pm_schedules", []) or [])
    pm_in_horizon = 0
    last_shift = max((s.end_dt for s in shifts), default=None)
    for pm in getattr(snapshot, "pm_schedules", []) or []:
        try:
            start = datetime.strptime(str(pm.start_date).replace("/", "-")[:10], "%Y-%m-%d")
        except Exception:
            continue
        if last_shift and shifts and start <= last_shift:
            pm_in_horizon += 1
    checks.append({
        "rule_id": "R-PM-01",
        "rule_name": RULE_NAMES["R-PM-01"],
        "nature": "hard",
        "status": "info",
        "evidence": (
            f"本期有 {pm_in_horizon} 条保养计划落在计划期内（共 {pm_total} 条）。"
            "保养班次按停机处理，会直接吃掉产能；如果保养日期本身是预估的，可以先挪开验证是否是它卡住。"
        ),
        "numbers": {"pm_total": pm_total, "pm_in_horizon": pm_in_horizon},
    })

    # --- pull-forward
    if pulled_orders:
        checks.append({
            "rule_id": "LOOKAHEAD",
            "rule_name": "30 天需求前拉（跨期预产）",
            "nature": "soft",
            "status": "suspected",
            "evidence": (
                f"本期已被前拉进 {pulled_orders} 张未来订单，这部分工时也要在同样的班次里消化。"
                "如果只想排本期要交的货，先在站点设置关闭前拉再排一次，可排除它的影响。"
            ),
            "numbers": {"pulled_orders": pulled_orders},
        })

    checks.append({
        "rule_id": "COMBINATION",
        "rule_name": "规则组合（不是单条规则）",
        "nature": "-",
        "status": "info",
        "evidence": (
            f"全厂需求 {total_work:.1f}h / 全厂可用 {plant_capacity:.0f}h（{total_work / plant_capacity * 100:.1f}%），"
            "总量并没有爆表；但交期窗口 + 95% 填充 + 换模/模具 + 冻结窗口同时成立时，可行域会被切得很碎。"
            "判断方法：先放宽交期（允许延期）出一版可行解，再逐条收紧，看是哪一条把它压死。"
        ),
        "numbers": {"total_work_hours": round(total_work, 1), "plant_capacity_hours": round(plant_capacity, 1)},
    })

    for check in checks:
        template = RULE_TEMPLATE.get(check.get("rule_id"))
        if template and template in disabled:
            check["status"] = "disabled"
            check["evidence"] = (
                "本期已在规则集中【禁用】，不参与求解（对应的旧口径开关也已关闭）。" + check["evidence"]
            )
    return checks


# --------------------------------------------------------------------------
# 5. algorithm self-audit
# --------------------------------------------------------------------------
def build_algorithm_checks(
    plan: Any,
    snapshot: Any,
    shifts: Sequence[Any],
    shift_hours: float,
    order_gaps: Sequence[Dict[str, Any]],
    start_dt: datetime,
) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    num_shifts = len(shifts)

    # Maintenance windows: parse the calendar dates and verify they land on a real
    # shift of *this* run.  The scheduler maps them by date now, so a window that
    # cannot be placed is a data problem (unknown shift name / rest day), not an
    # off-by-N index.
    pm_all = list(getattr(snapshot, "pm_schedules", []) or [])
    shift_names: List[str] = []
    for s in shifts:
        if s.shift_name not in shift_names:
            shift_names.append(s.shift_name)
    year = shifts[0].start_dt.year if shifts else start_dt.year

    def _pm_span(pm: Any):
        if not shift_names:
            return None, None
        try:
            start = datetime.strptime(str(pm.start_date).replace("/", "-")[:10], "%Y-%m-%d").date()
            end = datetime.strptime(str(pm.end_date).replace("/", "-")[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None, None
        try:
            lo = (start, shift_names.index(getattr(pm, "start_shift", shift_names[0])))
            hi = (end, shift_names.index(getattr(pm, "end_shift", shift_names[-1])))
        except ValueError:
            lo, hi = (start, 0), (end, max(0, len(shift_names) - 1))
        return (lo, hi) if hi >= lo else (hi, lo)

    pm_rows = []
    for pm in pm_all:
        lo, hi = _pm_span(pm)
        if lo is None:
            pm_rows.append((pm, []))
            continue
        hits = []
        for s in shifts:
            try:
                mm, dd = [int(x) for x in str(s.date_str).split("/")]
                key = (datetime(year, mm, dd).date(), shift_names.index(s.shift_name))
            except (TypeError, ValueError):
                continue
            if lo <= key <= hi:
                hits.append(s)
        pm_rows.append((pm, hits))

    placed = [(pm, hits) for pm, hits in pm_rows if hits]
    # A maintenance row outside the horizon is normal — it simply is not this run's
    # problem.  Only a window that falls inside the calendar's date span yet matches
    # no shift (rest day / unknown shift name) deserves a warning.
    first_day = shifts[0].start_dt.date() if shifts else None
    last_day = shifts[-1].start_dt.date() if shifts else None
    in_span_unplaced = []
    for pm, hits in pm_rows:
        if hits or first_day is None:
            continue
        lo, hi = _pm_span(pm)
        if lo and hi and not (hi[0] < first_day or lo[0] > last_day):
            in_span_unplaced.append(pm)
    if pm_all:
        per_line = defaultdict(list)
        for pm, hits in placed:
            per_line[pm.line].extend(f"{s.date_str} {s.shift_name}" for s in hits)
        checks.append({
            "level": "warn" if in_span_unplaced else "ok",
            "code": "PM_DATE_MAPPING",
            "title": "设备保养按日期折算到本次日历的班次",
            "detail": (
                f"数据里共 {len(pm_all)} 条保养计划，其中 {len(placed)} 条落在本次 {num_shifts} 个班次内"
                + ("：" + "；".join(f"{line} {', '.join(days)}" for line, days in list(per_line.items())[:4])
                   if per_line else "。")
                + (f"另 {len(pm_all) - len(placed)} 条不在本期日历内（跨期或落在休息日），按“不影响本期”处理。"
                   if len(pm_all) > len(placed) else "")
                + (f"其中 {len(in_span_unplaced)} 条日期在本期内但没有对应班次（休息日/班次名不匹配），需要确认。"
                   if in_span_unplaced else "")
            ),
            "metrics": {"pm_total": len(pm_all), "in_calendar": len(placed),
                        "unplaced": len(pm_all) - len(placed), "unplaced_in_span": len(in_span_unplaced)},
        })

    max_mat = max((getattr(t, "material_arrival_shift", 0) or 0) for t in getattr(plan, "tasks", []) or [0])
    if max_mat >= num_shifts:
        checks.append({
            "level": "warn",
            "code": "MAT_ARRIVAL_INDEX",
            "title": "物料到货班次索引仍按数据日期轴折算",
            "detail": (
                f"最大到货下标 {max_mat} ≥ 本次班次数 {num_shifts}。当前数据里没有真正被消耗的到货料号，"
                "所以本轮没造成影响；一旦有大料到货约束，需要同样改成按 D+1 的日期折算。"
            ),
            "metrics": {"max_index": max_mat, "num_shifts": num_shifts},
        })

    # The pre-solver check sums FG tasks only, so SF-only lines never appear there.
    sf_lines = {t.line for t in getattr(plan, "tasks", []) if t.stage == "SF"}
    fg_lines = {t.line for t in getattr(plan, "tasks", []) if t.stage == "FG"}
    sf_only = sf_lines - fg_lines
    if sf_only:
        busiest = sorted(
            ((line, sum(_task_hours(t) for t in plan.tasks if t.line == line)) for line in sf_only),
            key=lambda kv: -kv[1],
        )[:3]
        checks.append({
            "level": "warn",
            "code": "PRECHECK_FG_ONLY",
            "title": "预检只核成品线，半成品线的交期缺口不报",
            "detail": (
                "『检出关键产能瓶颈缺口』只遍历成品任务，纯半成品线（"
                + "、".join(f"{l} {h:.0f}h" for l, h in busiest)
                + "）的交期缺口不会出现在那张清单里，但求解器照样会撞上。这是上报口径的差异，不是数据错误。"
            ),
            "metrics": {"sf_only_lines": len(sf_only)},
        })

    # stock netting consistency: plan quantities vs an independent recomputation
    mismatched = 0
    stock = _stock_rows(plan, snapshot)
    for task in getattr(plan, "fg_tasks", []):
        info = stock.get(task.task_id)
        if info and abs(info["expected"] - info["produced"]) > 1e-6:
            mismatched += 1
    checks.append({
        "level": "ok" if mismatched == 0 else "warn",
        "code": "STOCK_NETTING",
        "title": "库存冲减与 MPQ 整批投产的自检",
        "detail": (
            f"{len(getattr(plan, 'fg_tasks', []))} 个成品批次逐一复算『期初库存 + 要货 - 整批产出』，"
            + ("与计划中的投产数量完全一致。" if mismatched == 0 else f"有 {mismatched} 个批次对不上，需要复查。")
        ),
        "metrics": {"fg_tasks": len(getattr(plan, "fg_tasks", [])), "mismatched": mismatched},
    })

    # due earlier than the first shift
    first_shift = shifts[0].start_dt if shifts else start_dt
    early = [t for t in getattr(plan, "tasks", []) if t.due_datetime <= first_shift]
    if early:
        checks.append({
            "level": "warn",
            "code": "DUE_BEFORE_START",
            "title": "有工单交期早于第一个班次",
            "detail": f"{len(early)} 个任务的交期不晚于 {first_shift:%m/%d %H:%M}，无论产能多少都不可能准时。",
            "metrics": {"tasks": len(early)},
        })

    checks.append({
        "level": "ok",
        "code": "CAPACITY_MODEL",
        "title": "产能口径自检",
        "detail": (
            f"本次 {len(shifts)} 班 × {shift_hours:g}h = {len(shifts) * shift_hours:.0f}h/线，"
            f"与界面显示一致；工时为 lots × lot_millis（毫秒）折算，未发现单位错位。"
        ),
        "metrics": {"shifts": len(shifts), "shift_hours": shift_hours},
    })
    return checks


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------
def build_diagnosis(
    *,
    plan: Any,
    snapshot: Any,
    shifts: Sequence[Any],
    start_dt: datetime,
    shift_hours: float,
    enabled_rules: Optional[Iterable[str]] = None,
    unassigned_parts: Optional[Iterable[str]] = None,
    pulled_orders: int = 0,
    disabled_templates: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Assemble the full "why is this infeasible" payload."""
    shifts = list(shifts or [])
    order_gaps = build_order_gaps(plan, shifts, snapshot, shift_hours)
    line_details = build_line_details(plan, shifts, shift_hours)
    data_checks = build_data_checks(plan, snapshot, shifts, order_gaps, shift_hours, unassigned_parts)
    rule_checks = build_rule_checks(plan, snapshot, shifts, shift_hours, order_gaps, line_details,
                                    enabled_rules=enabled_rules, pulled_orders=pulled_orders,
                                    disabled_templates=disabled_templates)
    algorithm_checks = build_algorithm_checks(plan, snapshot, shifts, shift_hours, order_gaps, start_dt)

    gap_rows = [r for r in order_gaps if r["gap_hours"] > 0.05]
    overload = [r for r in line_details if r["is_overloaded"]]
    total_work = sum(_task_hours(t) for t in getattr(plan, "tasks", []))
    per_line_capacity = sum(float(s.duration_hours) for s in shifts)
    plant_capacity = per_line_capacity * max(1, len(line_details)) or 1.0

    if overload:
        primary = "产能总量不足"
        headline = f"{'、'.join(r['line'] for r in overload)} 的需求工时超过了计划期总产能。"
    elif gap_rows:
        primary = "交期早于生产提前期（数据不可执行）"
        worst = max(gap_rows, key=lambda r: r["late_hours"])
        headline = (
            f"总产能是够的（全厂 {total_work:.0f}h / {plant_capacity:.0f}h），"
            f"但有 {len(gap_rows)} 张工单的交期早于最快完工时间；最严重的是 {worst['order_id']}，"
            f"要 {worst['cum_required_hours']:.1f}h，交期前只有 {worst['cum_capacity_hours']:.1f}h。"
        )
    else:
        primary = "规则组合冲突"
        headline = (
            f"总产能和单条交期都过得去（负载 {total_work / plant_capacity * 100:.1f}%），"
            "是硬规则的组合把可行域切没了，需要按规则逐条放宽验证。"
        )

    return {
        "primary_cause": primary,
        "headline": headline,
        "summary": {
            "start": _fmt(start_dt),
            "shifts": len(shifts),
            "shift_hours": shift_hours,
            "hours": round(per_line_capacity, 1),
            "plant_capacity_hours": round(plant_capacity, 1),
            "lines": len(line_details),
            "orders": len(getattr(snapshot, "orders", []) or []),
            "fg_tasks": len(getattr(plan, "fg_tasks", [])),
            "sf_tasks": len(getattr(plan, "sf_tasks", [])),
            "gap_orders": len(gap_rows),
            "overloaded_lines": [r["line"] for r in overload],
            "total_work_hours": round(total_work, 1),
            "utilization_rate": round(total_work / plant_capacity * 100, 1) if plant_capacity else 0.0,
        },
        "order_gaps": order_gaps,
        "line_details": line_details,
        "data_checks": data_checks,
        "rule_checks": rule_checks,
        "algorithm_checks": algorithm_checks,
    }
