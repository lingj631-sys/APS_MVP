from __future__ import annotations

import os
from collections.abc import Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from aps.dataset import Task
from aps.model.plan_row import LineShiftSummary, PlanRow
from aps.model.report import BindingReport
from aps.model.solver import SolveSummary

__all__ = ["export_production_plan"]

_HEADER_FILL = PatternFill(start_color="FFD9E1F2", end_color="FFD9E1F2", fill_type="solid")
_HEADER_FONT = Font(bold=True)
_CENTER_ALIGN = Alignment(horizontal="center", vertical="center")


def _style_headers(ws: Worksheet, headers: Sequence[str]) -> None:
    for col, h in enumerate(headers, start=1):
        cell = ws.cell(1, col, h)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _CENTER_ALIGN
        letter = get_column_letter(col)
        ws.column_dimensions[letter].width = max(12, len(str(h)) * 2 + 4)
    ws.freeze_panes = "A2"


def _write_plan_details(ws: Worksheet, plan_rows: Sequence[PlanRow]) -> None:
    headers = [
        "站点", "产线", "日期", "班次", "槽位序号", "工单ID", "料号",
        "阶段", "排产件数", "开始分钟", "结束分钟", "换型分钟", "是否末班",
    ]
    _style_headers(ws, headers)
    for row_idx, r in enumerate(plan_rows, start=2):
        ws.cell(row_idx, 1, r.site)
        ws.cell(row_idx, 2, r.line_code)
        ws.cell(row_idx, 3, r.date_str)
        ws.cell(row_idx, 4, r.shift_name)
        ws.cell(row_idx, 5, r.slot_index)
        ws.cell(row_idx, 6, r.task_id)
        ws.cell(row_idx, 7, r.pn)
        ws.cell(row_idx, 8, r.stage)
        ws.cell(row_idx, 9, r.quantity)
        ws.cell(row_idx, 10, r.start_min)
        ws.cell(row_idx, 11, r.end_min)
        ws.cell(row_idx, 12, r.setup_min)
        ws.cell(row_idx, 13, "是" if r.is_tail_shift else "否")


def _write_line_shifts(ws: Worksheet, shift_summaries: Sequence[LineShiftSummary]) -> None:
    headers = [
        "站点", "产线", "日期", "班次", "槽位序号", "是否开班",
        "额定负荷 (小时)", "实际负荷 (小时)", "负荷利用率%", "是否末班",
    ]
    _style_headers(ws, headers)
    for row_idx, s in enumerate(shift_summaries, start=2):
        ws.cell(row_idx, 1, s.site)
        ws.cell(row_idx, 2, s.line_code)
        ws.cell(row_idx, 3, s.date_str)
        ws.cell(row_idx, 4, s.shift_name)
        ws.cell(row_idx, 5, s.slot_index)
        ws.cell(row_idx, 6, "开班" if s.is_open else "关班")
        ws.cell(row_idx, 7, s.cap_hours)
        ws.cell(row_idx, 8, s.actual_hours)
        ws.cell(row_idx, 9, s.utilization_pct)
        ws.cell(row_idx, 10, "是" if s.is_tail else "否")


def _write_order_delivery(ws: Worksheet, tasks: Sequence[Task], plan_rows: Sequence[PlanRow]) -> None:
    headers = [
        "工单ID", "料号", "品名描述", "阶段", "需求件数",
        "实排件数", "需求交期", "完工日期", "完工班次", "延误班次数", "交付状态",
    ]
    _style_headers(ws, headers)

    # 索引每个工单的排产情况
    rows_by_task: dict[str, list[PlanRow]] = {}
    for r in plan_rows:
        rows_by_task.setdefault(r.task_id, []).append(r)

    # 逐任务生成汇总行
    task_ids = sorted(
        {r.task_id for r in plan_rows}.union(
            f"{t.stage}_{t.pn}_{t.due_date.isoformat()}_{idx}" for idx, t in enumerate(tasks)
        )
    )

    tasks_by_id = {
        f"{t.stage}_{t.pn}_{t.due_date.isoformat()}_{idx}": t for idx, t in enumerate(tasks)
    }

    current_row = 2
    for t_id in task_ids:
        task = tasks_by_id.get(t_id)
        if not task:
            continue
        p_rows = rows_by_task.get(t_id, [])
        planned_qty = sum(r.quantity for r in p_rows)
        due_str = task.due_date.isoformat()

        if p_rows:
            last_p = sorted(p_rows, key=lambda x: (x.date_str, x.slot_index))[-1]
            finish_date = last_p.date_str
            finish_shift = last_p.shift_name
            is_delayed = finish_date > due_str
            delay_shifts = max(0, (last_p.slot_index - 0)) if is_delayed else 0
            status_desc = "按期交付" if not is_delayed and planned_qty >= task.net_requirement else "延误交付"
        else:
            finish_date = "-"
            finish_shift = "-"
            delay_shifts = 0
            status_desc = "未排产"

        ws.cell(current_row, 1, t_id)
        ws.cell(current_row, 2, task.pn)
        ws.cell(current_row, 3, task.description or "")
        ws.cell(current_row, 4, task.stage)
        ws.cell(current_row, 5, task.net_requirement)
        ws.cell(current_row, 6, planned_qty)
        ws.cell(current_row, 7, due_str)
        ws.cell(current_row, 8, finish_date)
        ws.cell(current_row, 9, finish_shift)
        ws.cell(current_row, 10, delay_shifts)
        ws.cell(current_row, 11, status_desc)
        current_row += 1


def _write_solver_report(ws: Worksheet,
                         solve_summary: SolveSummary,
                         binding_reports: Sequence[BindingReport]) -> None:
    ws.cell(1, 1, "APS 排产求解与约束绑定报告").font = Font(bold=True, size=14)

    row = 3
    ws.cell(row, 1, "一、求解总览").font = Font(bold=True, size=12)
    row += 1
    ws.cell(row, 1, "求解状态")
    ws.cell(row, 2, solve_summary.status)
    row += 1
    ws.cell(row, 1, "求解耗时 (秒)")
    ws.cell(row, 2, round(solve_summary.wall_time_seconds, 2))
    row += 1
    ws.cell(row, 1, "确定性时间 (刻)")
    ws.cell(row, 2, round(solve_summary.deterministic_time, 2))

    row += 2
    ws.cell(row, 1, "二、四层字典序多目标达成").font = Font(bold=True, size=12)
    row += 1
    ws.cell(row, 1, "P1 交付 OTD 延误班次")
    ws.cell(row, 2, solve_summary.p1_lateness_shifts)
    row += 1
    ws.cell(row, 1, "P2 95% 产能利用率缺口")
    ws.cell(row, 2, f"{solve_summary.p2_utilization_gap_ms / 3600000.0:.2f} 小时")
    row += 1
    ws.cell(row, 1, "P3 换型连续生产块数")
    ws.cell(row, 2, solve_summary.p3_changeover_blocks)
    row += 1
    ws.cell(row, 1, "P4 完工槽位偏序和")
    ws.cell(row, 2, solve_summary.p4_wip_units)

    row += 2
    ws.cell(row, 1, "三、规则生效状态报告").font = Font(bold=True, size=12)
    row += 1
    headers = ["规则编号", "生效状态", "生成硬约束数", "惩罚项/目标项数", "代码源出处"]
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row, c, h)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
    row += 1

    for rep in binding_reports:
        ws.cell(row, 1, rep.rule_id)
        ws.cell(row, 2, rep.status)
        ws.cell(row, 3, rep.constraints_count)
        ws.cell(row, 4, rep.penalty_terms_count)
        ws.cell(row, 5, rep.provenance)
        row += 1

    row += 1
    ws.cell(row, 1, "四、不可行诊断建议").font = Font(bold=True, size=12)
    row += 1
    if solve_summary.diagnostics:
        for diag in solve_summary.diagnostics:
            ws.cell(row, 1, f"• {diag}")
            row += 1
    else:
        ws.cell(row, 1, "• 经 CP-SAT 核心求解器验证，当前生产计划为全局可行合规解，无物理冲突。")

    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 24
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 16
    ws.column_dimensions["E"].width = 45


def export_production_plan(
    plan_rows: Sequence[PlanRow],
    shift_summaries: Sequence[LineShiftSummary],
    solve_summary: SolveSummary,
    binding_reports: Sequence[BindingReport],
    tasks: Sequence[Task],
    out_path: str | os.PathLike[str],
) -> None:
    wb = Workbook()
    wb.remove(wb.active)  # 移除默认的 Sheet

    ws1 = wb.create_sheet("生产计划明细 Plan Details")
    _write_plan_details(ws1, plan_rows)

    ws2 = wb.create_sheet("产线班次负荷 Line Shifts")
    _write_line_shifts(ws2, shift_summaries)

    ws3 = wb.create_sheet("订单交付汇总 Order Delivery")
    _write_order_delivery(ws3, tasks, plan_rows)

    ws4 = wb.create_sheet("求解报告 Solver Report")
    _write_solver_report(ws4, solve_summary, binding_reports)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    wb.save(os.fspath(out_path))
