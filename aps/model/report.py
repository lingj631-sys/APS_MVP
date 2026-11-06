from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aps.model.context import StructureContext

__all__ = ["BindingReport", "build_binding_reports"]


@dataclass(frozen=True)
class BindingReport:
    rule_id: str
    name: str                               # 规则中文名称
    category: str                           # 规则类别：物理硬约束 / 多目标优化准则
    description: str                        # 业务管理准则说明
    math_expr: str                          # 系统约束判定条件
    status: str                             # "COMPILED" | "DEGRADED" | "SKIPPED"
    constraints_count: int
    penalty_terms_count: int
    provenance: str
    impact_note: str = ""                   # 管理效能保障说明


def build_binding_reports(ctx: StructureContext) -> list[BindingReport]:
    degraded = ctx.dataset.degraded_fields
    reports: list[BindingReport] = []

    # 1. 产线唯一认证分配
    reports.append(
        BindingReport(
            rule_id="R1_LINE_ASSIGNMENT",
            name="产线认证与排他分配",
            category="物理硬约束",
            description="工单只能排入通过工程认证的有效产线生产；单工单在排程期内独占单线，禁止多线分裂投产。",
            math_expr="sum(assign[t, l] for l in certified) == 1",
            status="COMPILED",
            constraints_count=len(ctx.task_ids),
            penalty_terms_count=0,
            provenance="aps.model.rules:c1_line_assignment",
            impact_note="杜绝未认证机台加工风险与工艺违规换线。",
        )
    )

    # 2. 净需求严格守恒
    reports.append(
        BindingReport(
            rule_id="R2_NET_REQUIREMENT",
            name="需求交付总量守恒",
            category="物理硬约束",
            description="工单在各班次实际产出数量求和必须精确等于 MRP 冲销净需求，严禁欠产缺货或超产积压。",
            math_expr="sum(x[t, l, s] for l, s) == task.net_requirement",
            status="COMPILED",
            constraints_count=len(ctx.task_ids),
            penalty_terms_count=0,
            provenance="aps.model.rules:c2_net_requirement",
            impact_note="保证全厂实际产出与客户订单净需求精确对齐。",
        )
    )

    # 3. 产线班次工时容量上限
    cap_count = sum(ctx.dataset.slot_count(l.line_code) for l in ctx.dataset.lines)
    reports.append(
        BindingReport(
            rule_id="R3_SLOT_CAPACITY",
            name="单班工时容量上限",
            category="物理硬约束",
            description="产线单班分配的全部工单加工工时毫秒数之和严禁超出班次额定容量上限，关班班次强置为 0 负荷。",
            math_expr="load_ms[l, s] <= slot_capacity_ms[l, s] * is_open[l, s]",
            status="COMPILED",
            constraints_count=cap_count,
            penalty_terms_count=0,
            provenance="aps.model.rules:c3_slot_capacity",
            impact_note="杜绝单班超负荷爆线，确保排程计划物理可执行。",
        )
    )

    # 4. 成品 MPQ 包装整倍数
    fg_tasks = [t for t in ctx.dataset.tasks if t.stage == "FG" and t.mpq > 1]
    reports.append(
        BindingReport(
            rule_id="R4_FG_MPQ_PACKING",
            name="成品包装整箱规 (MPQ)",
            category="物理硬约束",
            description="成品出件按标准包装箱容整数倍装箱，尾数由收线末班吸收；单班产能低于箱规时自适应跨班连续装配。",
            math_expr="x[t, l, s] == q * mpq (+ rem * tail_rem)",
            status="COMPILED" if fg_tasks else "SKIPPED",
            constraints_count=len(fg_tasks),
            penalty_terms_count=0,
            provenance="aps.model.rules:c4_fg_mpq",
            impact_note="保障仓储物流整箱转运，避免线边散件堆积。",
        )
    )

    # 5. 半成品 MPQ 批量投产门槛
    sf_tasks = [t for t in ctx.dataset.tasks if t.stage == "SF" and t.mpq > 1]
    reports.append(
        BindingReport(
            rule_id="R5_SF_MPQ_THRESHOLD",
            name="半成品经济生产批量",
            category="物理硬约束",
            description="半成品开机产出总量必须跨越经济批量门槛，总量达标后单班出件灵活平滑以支持流水对接。",
            math_expr="sum(x) >= min(mpq, net_req) * sf_open",
            status="COMPILED" if sf_tasks else "SKIPPED",
            constraints_count=len(sf_tasks),
            penalty_terms_count=0,
            provenance="aps.model.rules:c5_sf_mpq",
            impact_note="避免冲压、注塑前道工序零星小单频繁试模能耗浪费。",
        )
    )

    # 6. 上下游工序流水搭接平衡
    has_bom = len(ctx.dataset.bom) > 0
    reports.append(
        BindingReport(
            rule_id="R6_STAGE_FLOW_BALANCE",
            name="跨工序时间流水协同",
            category="物理硬约束",
            description="总装成品当前班次的累计消耗量绝不能超过半成品截至上一班次的实际累计产出与期初可用库存之和。",
            math_expr="cum_sf[child, s-1] + initial_stock >= ratio * cum_fg[parent, s]",
            status="COMPILED" if has_bom else "SKIPPED",
            constraints_count=len(ctx.dataset.bom),
            penalty_terms_count=0,
            provenance="aps.model.rules:c6_cross_stage_balance",
            impact_note="杜绝总装线边零部件缺料导致的停线窝工。",
        )
    )

    # 7. 交期延误计算与交付保证
    reports.append(
        BindingReport(
            rule_id="R7_DUE_DATE_LATENESS",
            name="工单交付时序偏序",
            category="多目标优化准则",
            description="动态判定工单最后活跃班次为完工班次，精确量化交期逾期班次数，作为全局准时交付优化的基准度量。",
            math_expr="late_shifts >= end_slot - due_index (if active)",
            status="COMPILED",
            constraints_count=len(ctx.late_shifts),
            penalty_terms_count=len(ctx.late_shifts),
            provenance="aps.model.rules:c7_due_date",
            impact_note="锁定订单按期交付，第一优先级 (P1) 最小化总延误。",
        )
    )

    # 8. 客户优先级保供加权
    cust_degraded = "demand.customer_code" in degraded
    reports.append(
        BindingReport(
            rule_id="R8_CUSTOMER_PRIORITY_OTD",
            name="战略客户优先交付",
            category="多目标优化准则",
            description="针对 Tier A 核心战略客户订单放大延误惩罚权重，在产能受限时系统优先保障高价值客户准时出货。",
            math_expr="Min P1 = sum(late_shifts[t] * weight(customer_tier))",
            status="DEGRADED" if cust_degraded else "COMPILED",
            constraints_count=len(ctx.late_shifts),
            penalty_terms_count=len(ctx.late_shifts),
            provenance="aps.model.solver.solve_schedule:P1",
            impact_note="保障核心主机厂战略客户供应链信誉，缺列时安全等权降级。",
        )
    )

    # 9. 95% 开班利用率底线与末班豁免
    reports.append(
        BindingReport(
            rule_id="R9_UTILIZATION_95",
            name="单班开班饱和度控制",
            category="多目标优化准则",
            description="产线一旦开班负荷率需达到预设饱和度红线，严禁半开半停散乱排产；单线排程周期的收线末班自动免除限制。",
            math_expr="load_ms + under_util + floor_ms * is_tail >= floor_ms * is_open",
            status="COMPILED",
            constraints_count=cap_count,
            penalty_terms_count=len(ctx.under_util),
            provenance="aps.model.rules:c9_utilization_floor",
            impact_note="提升设备综合稼动率，驱动集中连续生产与能耗节约。",
        )
    )

    # 10. 连续生产块与换型平滑
    reports.append(
        BindingReport(
            rule_id="R10_CHANGEOVER_SMOOTHING",
            name="连续生产与换型平滑",
            category="多目标优化准则",
            description="同工单任务在跨班排产时保持连续作业，第三优先级 (P3) 最小化启动脉冲块数，防止频繁切模换产。",
            math_expr="Min P3 = sum(block_start[t, l, s])",
            status="COMPILED",
            constraints_count=len(ctx.block_start),
            penalty_terms_count=len(ctx.block_start),
            provenance="aps.model.solver.solve_schedule:P3",
            impact_note="减少切模调机辅助工时，延长工装模具连续作业寿命。",
        )
    )

    return reports
