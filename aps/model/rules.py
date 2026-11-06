from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from ortools.sat.python import cp_model

from aps.calendar.geometry import MS_PER_HOUR
from aps.model.context import StructureContext

__all__ = ["build_core_constraints"]


def build_core_constraints(ctx: StructureContext,
                           allow_soft_utilization: bool = True,
                           use_assumptions: bool = False) -> list[str]:
    model = ctx.model
    ds = ctx.dataset
    lines = {l.line_code: l for l in ds.lines}
    rule_names: list[str] = []

    def _asm(name: str) -> cp_model.IntVar:
        lit = model.NewBoolVar(f"asm.{name}")
        ctx.assumptions[name] = lit
        ctx.assumption_names[lit.Index()] = name
        return lit

    def _add(ct, name: str) -> None:
        if use_assumptions:
            lit = _asm(name)
            model.Add(ct).OnlyEnforceIf(lit)
        else:
            model.Add(ct)

    # 1. 认证产线唯一分配 (Unique Line Assignment)
    for t_id in ctx.task_ids:
        cert_lines = [l for (tid, l) in ctx.assign if tid == t_id]
        if cert_lines:
            _add(sum(ctx.assign[(t_id, l)] for l in cert_lines) == 1, f"c1_assign.{t_id}")
    rule_names.append("c1_line_assignment")

    # 2. 净需求严格守恒 (Net Requirement Conservation) - O(1) indexed
    for t_id in ctx.task_ids:
        task = ctx.tasks_by_id[t_id]
        x_vars = ctx.x_by_task[t_id]
        if x_vars:
            _add(sum(x_vars) == task.net_requirement, f"c2_net_req.{t_id}")
    rule_names.append("c2_net_requirement")

    # 3. 班次负荷计算与工时容量上限 (Slot Capacity Limit)
    for line in ds.lines:
        l_code = line.line_code
        line_slots = ds.slot_count(l_code)
        cap_ms = int(round(line.shift_hours * MS_PER_HOUR))

        for s in range(line_slots):
            load_terms = []
            for t_id in ctx.task_ids:
                if (t_id, l_code, s) in ctx.x:
                    task = ctx.tasks_by_id[t_id]
                    route = ds.route(task.pn, l_code)
                    unit_ms = route.unit_ms if route else int(round(20.0 * 1000 / line.trp_efficiency))
                    load_terms.append(unit_ms * ctx.x[(t_id, l_code, s)])

            max_util = ctx.config.max_utilization_pct if (hasattr(ctx, "config") and ctx.config) else 1.05
            effective_cap_ms = int(round(cap_ms * max_util))
            model.Add(ctx.load_ms[(l_code, s)] == (sum(load_terms) if load_terms else 0))
            _add(ctx.load_ms[(l_code, s)] <= effective_cap_ms * ctx.is_open[(l_code, s)], f"c3_cap.{l_code}.{s}")
            model.Add(ctx.load_ms[(l_code, s)] <= effective_cap_ms * 2 * ctx.is_open[(l_code, s)])
    rule_names.append("c3_slot_capacity")

    # 4. 成品（FG）MPQ 整数倍生产 (FG MPQ Multiple with Tail Remainder) - O(1) indexed
    for t_id in ctx.task_ids:
        task = ctx.tasks_by_id[t_id]
        if task.stage == "FG" and task.mpq > 1:
            rem = task.net_requirement % task.mpq
            task_tail_vars = []
            cert_lines = ds.certified_lines(task.pn)
            for l_code in cert_lines:
                route = ds.route(task.pn, l_code)
                ub = route.per_shift_cap if route and route.per_shift_cap > 0 else task.net_requirement
                if task.mpq > ub:
                    # 当成品物料的包装箱量 MPQ 大于产线单班物理最大额定产能时 (例如包装箱 420 件，而产线单班产能仅 336 件)
                    # 此时单班物理上不可能一次出整箱，必须跨班连续装箱，免除单班整倍数约束
                    continue
                max_q = ub // task.mpq + 1

                for s, x_var in ctx.x_by_task_line.get((t_id, l_code), ()):
                    q_var = model.NewIntVar(0, max_q, f"q.{t_id}.{l_code}.{s}")
                    if rem == 0:
                        _add(x_var == q_var * task.mpq, f"c4_fg_mpq.{t_id}.{l_code}.{s}")
                    else:
                        tail_var = model.NewBoolVar(f"tail_rem.{t_id}.{l_code}.{s}")
                        task_tail_vars.append(tail_var)
                        _add(x_var == q_var * task.mpq + rem * tail_var, f"c4_fg_mpq.{t_id}.{l_code}.{s}")
            if task_tail_vars:
                model.Add(sum(task_tail_vars) <= 1)
    rule_names.append("c4_fg_mpq")

    # 5. 半成品（SF）MPQ 订单门槛与自定义最小生产批次个数 - O(1) indexed
    for t_id in ctx.task_ids:
        task = ctx.tasks_by_id[t_id]
        if task.stage == "SF":
            sf_batch = ctx.config.sf_min_batch_qty if (hasattr(ctx, "config") and ctx.config and ctx.config.sf_min_batch_qty) else task.mpq
            if sf_batch > 1:
                effective_batch = min(sf_batch, task.net_requirement)
                x_vars = ctx.x_by_task[t_id]
                if x_vars:
                    sf_open = model.NewBoolVar(f"sf_open.{t_id}")
                    _add(sum(x_vars) >= effective_batch * sf_open, f"c5_sf_mpq.{t_id}.min")
                    _add(sum(x_vars) <= task.net_requirement * sf_open, f"c5_sf_mpq.{t_id}.max")
    rule_names.append("c5_sf_mpq")

    # 6. 跨工序时间相位流水累计平衡 (Cross-Stage Cumulative Balance) - O(1) dictionary lookups
    for link in ds.bom:
        fg_tasks = [t_id for t_id in ctx.task_ids if ctx.tasks_by_id[t_id].pn == link.parent_pn]
        sf_tasks = [t_id for t_id in ctx.task_ids if ctx.tasks_by_id[t_id].pn == link.child_pn]
        if not fg_tasks or not sf_tasks:
            continue

        ratio_int = int(round(link.ratio * 100))
        sf_stock = int(round(ds.initial_stock.get(link.child_pn, 0)))

        fg_lines = ds.certified_lines(link.parent_pn)
        sf_lines = ds.certified_lines(link.child_pn)
        max_s = ds.horizon.days * 3

        for s in range(max_s):
            cum_fg_terms = [
                ctx.cum[(t_id, l, s)]
                for t_id in fg_tasks
                for l in fg_lines
                if (t_id, l, s) in ctx.cum
            ]
            if not cum_fg_terms and s > 0:
                continue

            cum_fg_s = sum(cum_fg_terms) if cum_fg_terms else 0

            if s == 0:
                cum_sf_prev = sf_stock
            else:
                cum_sf_prev_terms = [
                    ctx.cum[(t_id, l, s - 1)]
                    for t_id in sf_tasks
                    for l in sf_lines
                    if (t_id, l, s - 1) in ctx.cum
                ]
                cum_sf_prev = sf_stock + (sum(cum_sf_prev_terms) if cum_sf_prev_terms else 0)

            _add(cum_sf_prev * 100 >= cum_fg_s * ratio_int, f"c6_balance.{link.parent_pn}.{link.child_pn}.{s}")
    rule_names.append("c6_cross_stage_balance")

    # 7. 任务完工槽位与延误班次 (Due Date and Late Shifts)
    for t_id in ctx.task_ids:
        task = ctx.tasks_by_id[t_id]
        cert_lines = [l for (tid, l) in ctx.assign if tid == t_id]
        for l_code in cert_lines:
            due_idx = ds.due_index(task.due_date, l_code)
            line_slots = ds.slot_count(l_code)
            for s in range(line_slots):
                act_var = ctx.active[(t_id, l_code, s)]
                model.Add(ctx.end_slot[t_id] >= s).OnlyEnforceIf(act_var)

            if use_assumptions:
                lit = _asm(f"c7_due.{t_id}.{l_code}")
                model.Add(ctx.late_shifts[t_id] >= ctx.end_slot[t_id] - due_idx).OnlyEnforceIf(
                    [lit, ctx.assign[(t_id, l_code)]]
                )
            else:
                model.Add(ctx.late_shifts[t_id] >= ctx.end_slot[t_id] - due_idx).OnlyEnforceIf(
                    ctx.assign[(t_id, l_code)]
                )
    rule_names.append("c7_due_date")

    # 8. 收线末班定义 (Tail Shift Definition)
    for line in ds.lines:
        l_code = line.line_code
        line_slots = ds.slot_count(l_code)
        for s in range(line_slots):
            model.Add(ctx.is_tail[(l_code, s)] <= ctx.is_open[(l_code, s)])
            for s_next in range(s + 1, line_slots):
                model.Add(ctx.is_open[(l_code, s_next)] == 0).OnlyEnforceIf(ctx.is_tail[(l_code, s)])
    rule_names.append("c8_tail_shift_definition")

    # 9. 产线单班最小开班利用率底线与末班豁免 (Utilization Floor)
    min_util = ctx.config.min_utilization_pct if (hasattr(ctx, "config") and ctx.config) else 0.95
    if min_util > 0.0:
        for line in ds.lines:
            l_code = line.line_code
            line_slots = ds.slot_count(l_code)
            cap_ms = int(round(line.shift_hours * MS_PER_HOUR))
            floor_ms = int(round(min_util * cap_ms))

            for s in range(line_slots):
                if allow_soft_utilization:
                    gap_var = model.NewIntVar(0, floor_ms, f"under_util.{l_code}.{s}")
                    ctx.under_util[(l_code, s)] = gap_var
                    _add(
                        ctx.load_ms[(l_code, s)] + gap_var + floor_ms * ctx.is_tail[(l_code, s)]
                        >= floor_ms * ctx.is_open[(l_code, s)],
                        f"c9_floor.{l_code}.{s}",
                    )
                else:
                    _add(
                        ctx.load_ms[(l_code, s)] + floor_ms * ctx.is_tail[(l_code, s)]
                        >= floor_ms * ctx.is_open[(l_code, s)],
                        f"c9_floor.{l_code}.{s}",
                    )
        rule_names.append("c9_utilization_floor")

    # 10. 单班最大换型次数限制 (Max Changeovers Per Shift)
    max_chg = ctx.config.max_changeovers_per_shift if (hasattr(ctx, "config") and ctx.config) else None
    if max_chg is not None and max_chg >= 0:
        for line in ds.lines:
            l_code = line.line_code
            line_slots = ds.slot_count(l_code)
            candidate_tasks = [t_id for (t_id, lc) in ctx.assign if lc == l_code]
            for s in range(line_slots):
                active_terms = [ctx.active[(t_id, l_code, s)] for t_id in candidate_tasks if (t_id, l_code, s) in ctx.active]
                if len(active_terms) > (1 + max_chg):
                    model.Add(sum(active_terms) <= 1 + max_chg)
        rule_names.append("c10_max_changeovers_per_shift")

    if use_assumptions and ctx.assumptions:
        model.AddAssumptions(list(ctx.assumptions.values()))

    return rule_names
