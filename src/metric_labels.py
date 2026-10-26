"""Plain-language labels for validation/audit metrics shown in the UI."""
from __future__ import annotations

from typing import Any, Dict

METRIC_LABELS: Dict[str, str] = {
    # capacity / utilization
    "allow_105_fg": "成品允许 105% 上浮",
    "allow_105_sf": "半成品允许 105% 上浮",
    "total_active_shifts": "开班班次数",
    "overload_shifts_count": "超载班次数（应为 0）",
    "total_production_hours": "各线加工工时",
    "total_setup_hours": "各线换型工时",
    "min_threshold_pct": "班次负荷下限（%）",
    "max_threshold_pct": "班次负荷上限（%）",
    "total_working_shifts": "考核班次数",
    "qualified_shifts": "达标班次数",
    "tail_exempt_shifts": "尾班豁免数",
    "violating_shifts": "低于下限班次数（应为 0）",
    # conservation / MPQ
    "total_demand_pieces": "净需求总件数",
    "total_scheduled_pieces": "计划总件数",
    "conservation_gap": "拆解守恒差额（应为 0）",
    "conservation_rate_pct": "拆解守恒率（%）",
    "total_fg_tasks": "成品工单数",
    "mpq_compliant_tasks": "MPQ 合规工单数",
    "compliance_rate_pct": "合规率（%）",
    "total_sf_tasks": "半成品工单数",
    "compliant_tasks": "合规工单数",
    # material / inventory
    "min_safety_buffer_pieces": "最小齐套安全余量（件）",
    "stockout_shifts_count": "断料班次数（应为 0）",
    "anti_starvation_rate_pct": "防断线合规率（%）",
    "total_monitored_materials": "监控物料数",
    "unbalanced_materials_count": "库存不平物料数（应为 0）",
    "inventory_healthy_rate_pct": "库存健康率（%）",
    # delivery
    "total_demand_orders": "订单数",
    "on_time_orders_count": "准时订单数",
    "late_orders_count": "延期订单数（应为 0）",
    "overtime_tail_orders_count": "尾班超时订单数",
    "on_time_delivery_rate_pct": "准时交付率（%）",
    "avg_slack_hours": "平均交付裕度（h）",
    "min_slack_hours": "最小交付裕度（h）",
    # changeover / concurrency / sequence
    "total_changeovers": "换型次数",
    "cross_shift_switches": "跨班切换次数",
    "cross_shift_inherits": "跨班继承次数",
    "inheritance_rate_pct": "跨班继承率（%）",
    "saved_setup_hours": "节省换型工时（h）",
    "active_shifts_count": "在产班次数",
    "max_blocks_per_task": "单工单最大块数",
    "total_blocks": "生产块总数",
    "contiguity_hard_cap": "连续块硬上限",
    "parallel_model_violations": "并行型号违规数",
    "max_parallel_models": "单班最大并行型号数",
    "threshold": "合规上限",
    "violation_shifts_count": "违规班次数",
    "multi_order_parts_count": "多工单料号数",
    "sequence_violations_count": "顺位违规数",
    # freeze / controlled window / tooling / PM
    "applicable": "是否适用",
    "frozen_shifts": "冻结班次数",
    "locked_tasks_count": "锁定工单数",
    "tamper_violations_count": "冻结期改动数",
    "freeze_compliance_rate_pct": "冻结期合规率（%）",
    "monitored_batches": "监控批次",
    "controlled_window_violations": "受控窗口违规数",
    "shared_molds_tracked": "共享模具数",
    "conflict_shifts_count": "模具冲突班次数",
    "exclusivity_rate_pct": "模具独占合规率（%）",
    "tracked_tasks_count": "校验工单数",
    "material_ready_rate_pct": "物料齐套率（%）",
    "pm_windows_count": "PM 窗口数",
    "collision_hours": "PM 冲突工时",
    "pm_compliance_rate_pct": "PM 避让合规率（%）",
    "strategic_orders_count": "战略客户订单数",
    "strategic_otd_rate_pct": "战略客户准时率（%）",
    # calendar
    "total_shifts_count": "总班次数",
    "work_shifts_count": "工作日班次数",
    "topology_shift_hours": "单班标准工时（h）",
    "non_standard_shift_count": "非标班次数",
    "non_work_production_count": "非工作日排产数",
    "average_shift_load_hours": "平均班次负荷（h）",
    "utilization_cv_pct": "负荷波动 CV（%）",
    "smoothing_within_35pct": "负荷波动 ≤35%（CV）",
    "calendar_decoupled": "日历解耦",
    # audit extras
    "checked_component_pairs": "校验前后工序对",
    "lead_time_violations": "提前期违规数",
    "max_molds_per_shift": "单班最大模具数",
}


def label_for(key: str) -> str:
    return METRIC_LABELS.get(key, key)


def labels_for(metrics: Dict[str, Any]) -> Dict[str, str]:
    return {key: label_for(key) for key in (metrics or {})}
