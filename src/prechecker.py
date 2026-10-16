"""
Pre-solver feasibility checker and capacity bottleneck analyzer.

Implements R-EX-04, D-08, and provides the assertion target for R-TC-04
(CAPACITY_OVERLOAD_EXCEPTION). Runs before OR-Tools CP-SAT to catch gross
overloads early, preventing wasteful solver search and generating actionable
delivery delay suggestions.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from src.data_loader import RequirementPlan
from src.shift_manager import SHIFT_SECONDS, ShiftBucket


@dataclass
class DelaySuggestion:
    order_id: str
    part_no: str
    original_due: datetime
    suggested_due: datetime
    excess_hours: float
    reason: str


@dataclass
class LineLoadSummary:
    line: str
    stage: str
    required_hours: float
    available_hours: float
    utilization_rate: float
    is_overloaded: bool


@dataclass
class PrecheckResult:
    is_feasible: bool
    status: str  # "OK" or "CAPACITY_OVERLOAD_EXCEPTION"
    line_loads: Dict[str, LineLoadSummary]
    bottlenecks: List[str] = field(default_factory=list)
    delay_suggestions: List[DelaySuggestion] = field(default_factory=list)

    @property
    def has_exception(self) -> bool:
        return self.status == "CAPACITY_OVERLOAD_EXCEPTION"


def precheck_capacity(
    plan: RequirementPlan,
    shifts: List[ShiftBucket],
    max_utilization_threshold: float = 1.0,
) -> PrecheckResult:
    """Check macro capacity and cumulative due-date feasibility before solving."""
    if not shifts:
        return PrecheckResult(
            is_feasible=False,
            status="CAPACITY_OVERLOAD_EXCEPTION",
            line_loads={},
            bottlenecks=["No shifts available in calendar"],
        )

    # 1. Total capacity per line
    total_calendar_seconds = len(shifts) * SHIFT_SECONDS
    total_calendar_hours = total_calendar_seconds / 3600.0

    line_stage = {}
    line_work_seconds = {}
    for task in plan.tasks:
        line_stage[task.line] = task.stage
        # work seconds = lots * lot_millis / 1000
        sec = (task.lots * task.lot_millis) / 1000.0
        line_work_seconds[task.line] = line_work_seconds.get(task.line, 0.0) + sec

    line_loads = {}
    overloaded_lines = []
    for line, work_sec in line_work_seconds.items():
        req_hours = work_sec / 3600.0
        rate = req_hours / total_calendar_hours if total_calendar_hours > 0 else 999.0
        is_over = rate > max_utilization_threshold
        if is_over:
            overloaded_lines.append(line)
        line_loads[line] = LineLoadSummary(
            line=line,
            stage=line_stage.get(line, "UNKNOWN"),
            required_hours=req_hours,
            available_hours=total_calendar_hours,
            utilization_rate=rate,
            is_overloaded=is_over,
        )

    bottlenecks = []
    delay_suggestions = []

    # 2. Cumulative due-date bottleneck check (Delivery Feasibility)
    # Group FG tasks by line and chronological due date
    fg_tasks_by_line = {}
    for task in plan.fg_tasks:
        fg_tasks_by_line.setdefault(task.line, []).append(task)

    for line, tasks in fg_tasks_by_line.items():
        sorted_tasks = sorted(tasks, key=lambda t: t.due_datetime)
        cum_required_sec = 0.0
        for task in sorted_tasks:
            task_sec = (task.lots * task.lot_millis) / 1000.0
            cum_required_sec += task_sec

            # Max shifts available up to this due_datetime
            available_shifts = [s for s in shifts if s.end_dt <= task.due_datetime]
            cum_avail_sec = len(available_shifts) * SHIFT_SECONDS

            if cum_required_sec > cum_avail_sec + 1e-3:
                deficit_sec = cum_required_sec - cum_avail_sec
                deficit_h = deficit_sec / 3600.0
                needed_shifts = int(-(-deficit_sec // SHIFT_SECONDS))
                # Find earliest subsequent shift that could absorb deficit
                last_idx = available_shifts[-1].index if available_shifts else 0
                target_idx = min(len(shifts) - 1, last_idx + needed_shifts)
                suggested_due = shifts[target_idx].end_dt

                bottlenecks.append(
                    f"产线 {line} 订单 {task.order_id} ({task.part_no}): 截至交期 "
                    f"{task.due_datetime:%m/%d %H:%M} 累计需求 {cum_required_sec / 3600:.1f}h "
                    f"> 累计极限产能 {cum_avail_sec / 3600:.1f}h (缺口 {deficit_h:.1f}h)"
                )
                delay_suggestions.append(
                    DelaySuggestion(
                        order_id=task.order_id,
                        part_no=task.part_no,
                        original_due=task.due_datetime,
                        suggested_due=suggested_due,
                        excess_hours=deficit_h,
                        reason="交期前累计产能不足以支持交付",
                    )
                )

    if overloaded_lines or delay_suggestions:
        status = "CAPACITY_OVERLOAD_EXCEPTION"
        is_feasible = False
    else:
        status = "OK"
        is_feasible = True

    return PrecheckResult(
        is_feasible=is_feasible,
        status=status,
        line_loads=line_loads,
        bottlenecks=bottlenecks,
        delay_suggestions=delay_suggestions,
    )


def format_precheck_report(result: PrecheckResult) -> str:
    lines = [
        "-" * 70,
        "                    排产前粗能力可行性预检报告                    ",
        "-" * 70,
    ]
    status_tag = "PASS [可行]" if result.is_feasible else f"EXCEPTION [{result.status}]"
    lines.append(f"全局预检结论: {status_tag}")
    lines.append("\n产线宏观负荷分析:")
    for line, load in sorted(result.line_loads.items()):
        flag = " [超载!]" if load.is_overloaded else " [正常]"
        lines.append(
            f"  - 产线 {line:<12} 阶段 {load.stage:<4} | 需求 {load.required_hours:>6.1f}h / "
            f"可用 {load.available_hours:>6.1f}h | 负荷率 {load.utilization_rate * 100:>5.1f}%{flag}"
        )

    if result.bottlenecks:
        lines.append("\n交期瓶颈预警 (前置波峰拥堵):")
        for b in result.bottlenecks:
            lines.append(f"  * {b}")

    if result.delay_suggestions:
        lines.append("\n" + "=" * 70)
        lines.append("              《交期调整建议表》（建议延期至月末/空闲期）         ")
        lines.append("=" * 70)
        lines.append(
            f"{'订单号':<24} | {'料号':<10} | {'原交期':<12} | {'建议调整交期':<12} | {'缺口工时'}"
        )
        lines.append("-" * 70)
        for s in result.delay_suggestions:
            lines.append(
                f"{s.order_id:<24} | {s.part_no:<10} | {s.original_due:%m/%d %H:%M} | "
                f"{s.suggested_due:%m/%d %H:%M} | +{s.excess_hours:.1f}h"
            )
        lines.append("=" * 70)

    lines.append("-" * 70)
    return "\n".join(lines)
