from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aps.model.context import StructureContext

__all__ = ["BindingReport", "build_binding_reports"]


@dataclass(frozen=True)
class BindingReport:
    rule_id: str
    name: str                               # 中文规则名称
    category: str                           # 规则类别：物理硬约束 / 字典序优化目标 / 动态平衡
    description: str                        # 人类可理解的制造车间业务场景说明
    math_expr: str                          # 数学模型/CP-SAT 约束逻辑
    status: str                             # "COMPILED" | "DEGRADED" | "SKIPPED"
    constraints_count: int
    penalty_terms_count: int
    provenance: str
    impact_note: str = ""                   # 生效意义与未生效后果


def build_binding_reports(ctx: StructureContext) -> list[BindingReport]:
    degraded = ctx.dataset.degraded_fields
    reports: list[BindingReport] = []

    # 1. 产线唯一认证分配
    reports.append(
        BindingReport(
            rule_id="R1_LINE_ASSIGNMENT",
            name="产线唯一认证与排他性分配",
            category="基石物理硬约束",
            description="每个工单只能分配给经过工程认证的产线生产，严禁未认证设备越权加工；单工单在排产周期内必须整体归属单线，禁止跨线随意分裂投产。",
            math_expr="sum(assign[t, l] for l in certified) == 1",
            status="COMPILED",
            constraints_count=len(ctx.task_ids),
            penalty_terms_count=0,
            provenance="aps.model.rules:c1_line_assignment",
            impact_note="确保全厂 100% 工单落入合规认证产线，避免非法换线与工艺风险。",
        )
    )

    # 2. 净需求严格守恒
    reports.append(
        BindingReport(
            rule_id="R2_NET_REQUIREMENT",
            name="净需求物料总量严格守恒",
            category="基石物理硬约束",
            description="任务在所有产线和所有班次实际排产数量之和，必须严格等于 MRP 冲销后的净需求件数，绝对禁止欠产导致缺货，也严禁超产积压库存。",
            math_expr="sum(x[t, l, s] for l, s) == task.net_requirement",
            status="COMPILED",
            constraints_count=len(ctx.task_ids),
            penalty_terms_count=0,
            provenance="aps.model.rules:c2_net_requirement",
            impact_note="保障工厂交付数量与客户订单净需求精确 1:1 对齐。",
        )
    )

    # 3. 产线班次工时容量上限
    cap_count = sum(ctx.dataset.slot_count(l.line_code) for l in ctx.dataset.lines)
    reports.append(
        BindingReport(
            rule_id="R3_SLOT_CAPACITY",
            name="产线单班额定工时容量上限",
            category="基石物理硬约束",
            description="每个班次内分配给该线的所有任务生产工时毫秒数求和，严禁超出该产线的单班额定工时上限（如 8 小时班次不得超过 28,800 秒负荷），闭班班次负荷强置为 0。",
            math_expr="load_ms[l, s] <= slot_capacity_ms[l, s] * is_open[l, s]",
            status="COMPILED",
            constraints_count=cap_count,
            penalty_terms_count=0,
            provenance="aps.model.rules:c3_slot_capacity",
            impact_note="杜绝产线单班超负荷爆线，从物理上杜绝不可执行计划。",
        )
    )

    # 4. 成品 MPQ 包装整倍数
    fg_tasks = [t for t in ctx.dataset.tasks if t.stage == "FG" and t.mpq > 1]
    reports.append(
        BindingReport(
            rule_id="R4_FG_MPQ_PACKING",
            name="成品 MPQ 最小包装箱整倍数投产",
            category="装配包装硬约束",
            description="成品（FG）在各班次的产出件数必须为标准包装箱容量（MPQ）的整数倍，杜绝现场拆箱零头堆积；若订单总量存在非整倍尾数，严格限制仅在收线末班吸收尾数。",
            math_expr="x[t, l, s] == q * mpq (非整除时末班 x == q * mpq + rem)",
            status="COMPILED" if fg_tasks else "SKIPPED",
            constraints_count=len(fg_tasks),
            penalty_terms_count=0,
            provenance="aps.model.rules:c4_fg_mpq",
            impact_note="确保成品仓储物流按满箱转运，避免线边散件混放。",
        )
    )

    # 5. 半成品 MPQ 批量投产门槛
    sf_tasks = [t for t in ctx.dataset.tasks if t.stage == "SF" and t.mpq > 1]
    reports.append(
        BindingReport(
            rule_id="R5_SF_MPQ_THRESHOLD",
            name="半成品经济投产批量 MPQ 门槛",
            category="制造经济批量约束",
            description="半成品（SF）开机投产总数量必须达到最小经济批量 MPQ 门槛，禁止小碎单频繁开机试模；但总量达标后单班可灵活按需出件，平滑前道负荷。",
            math_expr="sum(x) >= mpq * sf_open 且 <= net_req * sf_open",
            status="COMPILED" if sf_tasks else "SKIPPED",
            constraints_count=len(sf_tasks),
            penalty_terms_count=0,
            provenance="aps.model.rules:c5_sf_mpq",
            impact_note="避免冲压、转子、注塑等前道工序因零碎需求频繁试模浪费能耗。",
        )
    )

    # 6. 上下游工序流水搭接平衡
    has_bom = len(ctx.dataset.bom) > 0
    reports.append(
        BindingReport(
            rule_id="R6_STAGE_FLOW_BALANCE",
            name="跨工序时间相位流水累计平衡",
            category="多阶工序协同硬约束",
            description="成品（FG）在班次 s 的累计投产件数乘以 BOM 装配单耗，绝不能超过子件半成品（SF）截至前一班次 s-1 的实际累计产出与期初可用库存之和，严禁流水断流或预支未来产出。",
            math_expr="cum_sf[child, s-1] + initial_stock >= ratio * cum_fg[parent, s]",
            status="COMPILED" if has_bom else "SKIPPED",
            constraints_count=len(ctx.dataset.bom),
            penalty_terms_count=0,
            provenance="aps.model.rules:c6_cross_stage_balance",
            impact_note="从根本上杜绝“总装在线边等待零部件、零部件跟不上总装”的停工待料风险。",
        )
    )

    # 7. 交期延误计算与交付保证
    reports.append(
        BindingReport(
            rule_id="R7_DUE_DATE_LATENESS",
            name="工单完工时序与延误班次偏序",
            category="交付履约度量",
            description="动态判定每个任务实际排产的最后活跃班次为完工班次，若完工班次晚于需求交期，将延误班次数精确沉淀为正数，作为全局优化目标的基础度量。",
            math_expr="end_slot >= s (if active); late_shifts >= end_slot - due_index",
            status="COMPILED",
            constraints_count=len(ctx.late_shifts),
            penalty_terms_count=len(ctx.late_shifts),
            provenance="aps.model.rules:c7_due_date",
            impact_note="为四层字典序优化的第一优先级（P1 交付保障）提供精确数学基础。",
        )
    )

    # 8. 客户优先级保供加权
    cust_degraded = "demand.customer_code" in degraded
    reports.append(
        BindingReport(
            rule_id="R8_CUSTOMER_PRIORITY_OTD",
            name="战略客户等级准时保供优先权",
            category="多目标第一优先级 (P1)",
            description="将核心战略客户（Tier A 级）订单的交付延误惩罚权重放大，确保在工厂总产能受限时优先保供高价值客户；若数据源缺列则安全降级为标准保供。",
            math_expr="Min P1 = sum(late_shifts[t] * weight(customer_tier))",
            status="DEGRADED" if cust_degraded else "COMPILED",
            constraints_count=len(ctx.late_shifts),
            penalty_terms_count=len(ctx.late_shifts),
            provenance="aps.model.solver.solve_schedule:P1",
            impact_note="优先锁定 Tier A 客户保供，若需求表未配置客户代码则自动等权保底降级运行。",
        )
    )

    # 9. 95% 开班利用率底线与末班豁免
    reports.append(
        BindingReport(
            rule_id="R9_UTILIZATION_95",
            name="产线单班 95% 饱和度红线与收线豁免",
            category="多目标第二优先级 (P2)",
            description="一旦产线决定开班，该班次的实际工时负荷率必须达到 95% 饱和度红线，严禁半开半停散乱排产；单线排产周期的收线末班自动享受豁免，解决订单尾数冲突。",
            math_expr="load_ms + under_util + floor_ms * is_tail >= 0.95 * cap * is_open",
            status="COMPILED",
            constraints_count=cap_count,
            penalty_terms_count=len(ctx.under_util),
            provenance="aps.model.rules:c9_utilization_floor",
            impact_note="迫使排产引擎集中负荷连续生产，严禁工厂电费人工空耗；末班豁免保障小单合规出厂。",
        )
    )

    # 10. 连续生产块与换型平滑
    reports.append(
        BindingReport(
            rule_id="R10_CHANGEOVER_SMOOTHING",
            name="同任务跨班连续整批作业 (防碎片打断)",
            category="多目标第三优先级 (P3)",
            description="同一个工单任务在跨班次连续生产时，中间严禁切换其他产品或插入非必要换模；通过最小化启动脉冲块数，驱动同一物料大批量集中平稳产出。",
            math_expr="Min P3 = sum(block_start[t, l, s]) (前阶差分 started >= active[s] - active[s-1])",
            status="COMPILED",
            constraints_count=len(ctx.block_start),
            penalty_terms_count=len(ctx.block_start),
            provenance="aps.model.solver.solve_schedule:P3",
            impact_note="最大化模具使用寿命与换型工时利用率，杜绝车间来回折腾换模。",
        )
    )

    return reports
