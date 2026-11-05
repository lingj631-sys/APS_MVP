from __future__ import annotations

import time
from dataclasses import dataclass, field as dc_field
from typing import Any

from ortools.sat.python import cp_model

from aps.model.context import StructureContext
from aps.model.rules import build_core_constraints

__all__ = ["SolutionSnapshot", "SolveSummary", "solve_schedule"]


@dataclass
class SolveSummary:
    status: str                             # OPTIMAL | FEASIBLE | INFEASIBLE | TIMEOUT
    wall_time_seconds: float
    deterministic_time: float
    p1_lateness_shifts: int
    p2_utilization_gap_ms: int
    p3_changeover_blocks: int
    p4_wip_units: int
    diagnostics: list[str] = dc_field(default_factory=list)


class SolutionSnapshot:
    """持久化保存 CP-SAT 变量解，防止多阶段字典序求解超时覆盖上一层可行解。"""

    def __init__(self, values: dict[int, int]):
        self._values = values

    def Value(self, var: cp_model.IntVar) -> int:
        return self._values.get(var.Index(), 0)


def _translate_assumption(name: str) -> str:
    parts = name.split(".")
    rule = parts[0]
    if rule == "c1_assign" and len(parts) >= 2:
        return f"工单 {parts[1]} 无法在认证产线完成排产分配"
    if rule == "c2_net_req" and len(parts) >= 2:
        return f"工单 {parts[1]} 的净需求量无法在当前排产周期内完成生产"
    if rule == "c3_cap" and len(parts) >= 3:
        return f"产线 {parts[1]} 在班次槽位 {parts[2]} 超出单班工时容量上限"
    if rule == "c4_fg_mpq" and len(parts) >= 4:
        return f"成品工单 {parts[1]} 在产线 {parts[2]} 班次 {parts[3]} 的生产数量无法整除 MPQ 最小包装数"
    if rule == "c5_sf_mpq" and len(parts) >= 2:
        return f"半成品工单 {parts[1]} 总生产量低于经济投产批量 MPQ 门槛"
    if rule == "c6_balance" and len(parts) >= 4:
        return f"母件 {parts[1]} 与子件 {parts[2]} 在班次槽位 {parts[3]} 发生工序流水断流（子件产出落后于母件投产）"
    if rule == "c7_due" and len(parts) >= 3:
        return f"工单 {parts[1]} 在产线 {parts[2]} 严重延误交期"
    if rule == "c9_floor" and len(parts) >= 3:
        return f"产线 {parts[1]} 在班次槽位 {parts[2]} 产能利用率低于 95% 底线且未享受末班豁免"
    return f"排产物理约束冲突: {name}"


def _diagnose_infeasibility(ctx: StructureContext, solver: cp_model.CpSolver) -> list[str]:
    conflict_indices = solver.SufficientAssumptionsForInfeasibility()
    if not conflict_indices:
        return ["模型在求解当前排产窗口时发生不可行冲突（未返回具体最小假设集）"]

    diagnostics: list[str] = []
    seen = set()
    for idx in conflict_indices:
        name = ctx.assumption_names.get(idx, str(idx))
        msg = _translate_assumption(name)
        if msg not in seen:
            seen.add(msg)
            diagnostics.append(msg)
    return diagnostics


def solve_schedule(ctx: StructureContext,
                   time_limit_seconds: float = 60.0,
                   allow_soft_utilization: bool = True) -> tuple[Any | None, SolveSummary]:
    start_time = time.perf_counter()
    model = ctx.model

    # 1. 正常求解时使用高效直接约束模式 (use_assumptions=False)
    build_core_constraints(ctx, allow_soft_utilization=allow_soft_utilization, use_assumptions=False)

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 8
    stage_time_limit = max(5.0, time_limit_seconds / 4.0)

    val_p1 = 0
    val_p2 = 0
    val_p3 = 0
    val_p4 = 0
    best_snapshot: SolutionSnapshot | None = None

    def _snapshot(slv: cp_model.CpSolver) -> SolutionSnapshot:
        vals = {}
        for var_dict in (ctx.x, ctx.active, ctx.started, ctx.cum, ctx.end_slot,
                         ctx.late_shifts, ctx.load_ms, ctx.is_open, ctx.is_tail,
                         ctx.block_start, ctx.under_util):
            for v in var_dict.values():
                idx = v.Index()
                if idx not in vals:
                    vals[idx] = slv.Value(v)
        return SolutionSnapshot(vals)

    # -------------------------------------------------------------
    # Layer 1: P1 交付优先 (OTD Delivery / Late Shifts Minimization)
    # -------------------------------------------------------------
    solver.parameters.max_time_in_seconds = stage_time_limit
    late_vars = list(ctx.late_shifts.values())
    if late_vars:
        obj_p1 = sum(late_vars)
        model.Minimize(obj_p1)
    status_p1 = solver.Solve(model)

    if status_p1 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        elapsed = time.perf_counter() - start_time
        if status_p1 == cp_model.INFEASIBLE:
            diag_ctx = StructureContext.build(ctx.dataset)
            build_core_constraints(diag_ctx, allow_soft_utilization=allow_soft_utilization, use_assumptions=True)
            diag_solver = cp_model.CpSolver()
            diag_solver.parameters.max_time_in_seconds = 10.0
            diag_solver.Solve(diag_ctx.model)
            diagnostics = _diagnose_infeasibility(diag_ctx, diag_solver)
        else:
            diagnostics = [f"求解超时（已耗时 {elapsed:.2f} 秒），未能在给定时间内收敛至可行解。"]

        return None, SolveSummary(
            status="INFEASIBLE" if status_p1 == cp_model.INFEASIBLE else "TIMEOUT",
            wall_time_seconds=elapsed,
            deterministic_time=solver.deterministic_time if status_p1 == cp_model.INFEASIBLE else 0.0,
            p1_lateness_shifts=0,
            p2_utilization_gap_ms=0,
            p3_changeover_blocks=0,
            p4_wip_units=0,
            diagnostics=diagnostics,
        )

    best_snapshot = _snapshot(solver)
    if late_vars:
        val_p1 = int(round(solver.ObjectiveValue()))
        model.Add(obj_p1 <= val_p1)

    # -------------------------------------------------------------
    # Layer 2: P2 产能合规 (Capacity & 95% Floor Utilization)
    # -------------------------------------------------------------
    rem_time = max(2.0, time_limit_seconds - (time.perf_counter() - start_time))
    if rem_time > 2.0 and allow_soft_utilization and ctx.under_util:
        solver.parameters.max_time_in_seconds = rem_time / 3.0
        obj_p2 = sum(ctx.under_util.values())
        model.Minimize(obj_p2)
        status_p2 = solver.Solve(model)
        if status_p2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            best_snapshot = _snapshot(solver)
            val_p2 = int(round(solver.ObjectiveValue()))
            model.Add(obj_p2 <= val_p2)

    # -------------------------------------------------------------
    # Layer 3: P3 换型平滑 (Changeover Blocks Minimization)
    # -------------------------------------------------------------
    rem_time = max(2.0, time_limit_seconds - (time.perf_counter() - start_time))
    block_vars = list(ctx.block_start.values())
    if rem_time > 2.0 and block_vars:
        solver.parameters.max_time_in_seconds = rem_time / 2.0
        obj_p3 = sum(block_vars)
        model.Minimize(obj_p3)
        status_p3 = solver.Solve(model)
        if status_p3 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            best_snapshot = _snapshot(solver)
            val_p3 = int(round(solver.ObjectiveValue()))
            model.Add(obj_p3 <= val_p3)

    # -------------------------------------------------------------
    # Layer 4: P4 节约在制品与工时 (WIP / End Slot Minimization)
    # -------------------------------------------------------------
    rem_time = max(2.0, time_limit_seconds - (time.perf_counter() - start_time))
    end_vars = list(ctx.end_slot.values())
    if rem_time > 2.0 and end_vars:
        solver.parameters.max_time_in_seconds = rem_time
        obj_p4 = sum(end_vars)
        model.Minimize(obj_p4)
        status_p4 = solver.Solve(model)
        if status_p4 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            best_snapshot = _snapshot(solver)
            val_p4 = int(round(solver.ObjectiveValue()))

    elapsed = time.perf_counter() - start_time
    final_status = "OPTIMAL" if status_p1 == cp_model.OPTIMAL else "FEASIBLE"
    return best_snapshot, SolveSummary(
        status=final_status,
        wall_time_seconds=elapsed,
        deterministic_time=solver.deterministic_time,
        p1_lateness_shifts=val_p1,
        p2_utilization_gap_ms=val_p2,
        p3_changeover_blocks=val_p3,
        p4_wip_units=val_p4,
        diagnostics=[],
    )
