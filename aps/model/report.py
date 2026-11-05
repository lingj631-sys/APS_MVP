from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aps.model.context import StructureContext

__all__ = ["BindingReport", "build_binding_reports"]


@dataclass(frozen=True)
class BindingReport:
    rule_id: str
    status: str                             # "COMPILED" | "DEGRADED" | "SKIPPED"
    constraints_count: int
    penalty_terms_count: int
    provenance: str


def build_binding_reports(ctx: StructureContext) -> list[BindingReport]:
    degraded = ctx.dataset.degraded_fields
    reports: list[BindingReport] = []

    # 1. 产线唯一认证分配
    reports.append(
        BindingReport(
            rule_id="R1_LINE_ASSIGNMENT",
            status="COMPILED",
            constraints_count=len(ctx.task_ids),
            penalty_terms_count=0,
            provenance="aps.model.rules.build_core_constraints:c1",
        )
    )

    # 2. 净需求严格守恒
    reports.append(
        BindingReport(
            rule_id="R2_NET_REQUIREMENT",
            status="COMPILED",
            constraints_count=len(ctx.task_ids),
            penalty_terms_count=0,
            provenance="aps.model.rules.build_core_constraints:c2",
        )
    )

    # 3. 产线班次工时容量上限
    cap_count = sum(ctx.dataset.slot_count(l.line_code) for l in ctx.dataset.lines)
    reports.append(
        BindingReport(
            rule_id="R3_SLOT_CAPACITY",
            status="COMPILED",
            constraints_count=cap_count,
            penalty_terms_count=0,
            provenance="aps.model.rules.build_core_constraints:c3",
        )
    )

    # 4. 成品 MPQ 包装整倍数
    fg_tasks = [t for t in ctx.dataset.tasks if t.stage == "FG" and t.mpq > 1]
    reports.append(
        BindingReport(
            rule_id="R4_FG_MPQ_PACKING",
            status="COMPILED" if fg_tasks else "SKIPPED",
            constraints_count=len(fg_tasks),
            penalty_terms_count=0,
            provenance="aps.model.rules.build_core_constraints:c4",
        )
    )

    # 5. 半成品 MPQ 批量投产门槛
    sf_tasks = [t for t in ctx.dataset.tasks if t.stage == "SF" and t.mpq > 1]
    reports.append(
        BindingReport(
            rule_id="R5_SF_MPQ_THRESHOLD",
            status="COMPILED" if sf_tasks else "SKIPPED",
            constraints_count=len(sf_tasks),
            penalty_terms_count=0,
            provenance="aps.model.rules.build_core_constraints:c5",
        )
    )

    # 6. 上下游工序流水搭接平衡
    has_bom = len(ctx.dataset.bom) > 0
    reports.append(
        BindingReport(
            rule_id="R6_STAGE_FLOW_BALANCE",
            status="COMPILED" if has_bom else "SKIPPED",
            constraints_count=len(ctx.dataset.bom),
            penalty_terms_count=0,
            provenance="aps.model.rules.build_core_constraints:c6",
        )
    )

    # 7. 交期延误计算与交付保证
    reports.append(
        BindingReport(
            rule_id="R7_DUE_DATE_LATENESS",
            status="COMPILED",
            constraints_count=len(ctx.late_shifts),
            penalty_terms_count=len(ctx.late_shifts),
            provenance="aps.model.rules.build_core_constraints:c7",
        )
    )

    # 8. 客户优先级保供加权
    cust_degraded = "demand.customer_code" in degraded
    reports.append(
        BindingReport(
            rule_id="R8_CUSTOMER_PRIORITY_OTD",
            status="DEGRADED" if cust_degraded else "COMPILED",
            constraints_count=len(ctx.late_shifts),
            penalty_terms_count=len(ctx.late_shifts),
            provenance="aps.model.solver.solve_schedule:P1",
        )
    )

    # 9. 95% 开班利用率底线与末班豁免
    reports.append(
        BindingReport(
            rule_id="R9_UTILIZATION_95",
            status="COMPILED",
            constraints_count=cap_count,
            penalty_terms_count=len(ctx.under_util),
            provenance="aps.model.rules.build_core_constraints:c9",
        )
    )

    # 10. 连续生产块与换型平滑
    reports.append(
        BindingReport(
            rule_id="R10_CHANGEOVER_SMOOTHING",
            status="COMPILED",
            constraints_count=len(ctx.block_start),
            penalty_terms_count=len(ctx.block_start),
            provenance="aps.model.solver.solve_schedule:P3",
        )
    )

    return reports
