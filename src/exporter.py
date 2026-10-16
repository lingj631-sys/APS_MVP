"""
Exporter for the APS MVP.

Writes the schedule into 'Production plan-Done' using the two-row header layout
of the 'Sample' sheet (merged 规格信息 block + one merged date spanning the shift
columns), then appends per-line summary rows: output totals, manufacturing
hours, and shift capacity utilisation with the WE34 95% rule flagged.
"""
from datetime import datetime
from typing import Dict, List, Tuple

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from src.scheduler import ScheduledTask
from src.shift_manager import MIN_SHIFT_UTILIZATION, SHIFT_SECONDS, ShiftBucket

ShiftKey = Tuple[str, str]

SPEC_LABEL = "规格信息"
BASE_HEADERS = [
    "资源代码", "品目描述", "成品料号", "制造时间", "CT", "TRP",
    "制造开始时间", "备注", "合计",
]
SHIFT_HEADERS = ["早班", "中班", "晚班", "试产"]
OUTPUT_SUMMARY = "产量汇总"
HOURS_SUMMARY = "制造工时汇总(h)"
UTILIZATION_SUMMARY = "班次利用率(%)"

FIRST_SHIFT_COL = len(BASE_HEADERS) + 1
TOTAL_COL = len(BASE_HEADERS)

FILL_TASK_FG = PatternFill("solid", start_color="E8F0FE")
FILL_TASK_SF = PatternFill("solid", start_color="FEF7E0")
FILL_HEADER = PatternFill("solid", start_color="F3F3F3")
FILL_SUMMARY = PatternFill("solid", start_color="EDEDED")
FILL_TAIL = PatternFill("solid", start_color="FFF3CD")
FILL_VIOLATION = PatternFill("solid", start_color="F6C6C6")
THIN = Side(style="thin", color="D3D3D3")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
COLUMN_WIDTHS = {"A": 13, "B": 30, "C": 14, "D": 10, "E": 6, "F": 6, "G": 17, "H": 24, "I": 9}


def export_production_plan(
    template_excel_path: str,
    output_excel_path: str,
    scheduled_tasks: List[ScheduledTask],
    shifts: List[ShiftBucket],
) -> None:
    dates = _ordered_dates(shifts)
    total_cols = TOTAL_COL + len(SHIFT_HEADERS) * len(dates)
    col_of = _shift_columns(dates)

    wb = openpyxl.load_workbook(template_excel_path, data_only=False)
    ws = (
        wb["Production plan-Done"]
        if "Production plan-Done" in wb.sheetnames
        else wb.create_sheet("Production plan-Done")
    )
    _reset_sheet(ws)
    _write_header(ws, dates)

    row = 3
    for line in _ordered_lines(scheduled_tasks):
        line_tasks = sorted(
            (t for t in scheduled_tasks if t.line == line),
            key=lambda t: (t.start_dt, t.part_no),
        )
        fill = FILL_TASK_FG if _is_fg_line(line_tasks) else FILL_TASK_SF
        for task in line_tasks:
            _write_task_row(ws, row, task, col_of, fill)
            row += 1
        row = _write_summary_rows(ws, row, line, line_tasks, col_of) + 1

    _write_grand_total(ws, row, scheduled_tasks, col_of)
    _finish_sheet(ws, total_cols, row)

    wb.save(output_excel_path)
    print(f"Successfully exported {len(scheduled_tasks)} scheduled tasks to {output_excel_path}")


def _ordered_dates(shifts: List[ShiftBucket]) -> List[str]:
    seen: List[str] = []
    for shift in shifts:
        if shift.date_str not in seen:
            seen.append(shift.date_str)
    return seen


def _shift_columns(dates: List[str]) -> Dict[ShiftKey, int]:
    col_of: Dict[ShiftKey, int] = {}
    for i, date_str in enumerate(dates):
        for j, shift_name in enumerate(SHIFT_HEADERS[:3]):
            col_of[(date_str, shift_name)] = FIRST_SHIFT_COL + i * len(SHIFT_HEADERS) + j
    return col_of


def _ordered_lines(tasks: List[ScheduledTask]) -> List[str]:
    seen: List[str] = []
    for task in sorted(tasks, key=lambda t: t.start_dt):
        if task.line not in seen:
            seen.append(task.line)
    return seen


def _is_fg_line(tasks: List[ScheduledTask]) -> bool:
    return any(t.stage == "FG" for t in tasks)


def _reset_sheet(ws) -> None:
    for merge in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(merge))
    if ws.max_row:
        ws.delete_rows(1, ws.max_row)


def _write_header(ws, dates: List[str]) -> None:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=2)
    ws.merge_cells(start_row=1, start_column=3, end_row=1, end_column=TOTAL_COL)
    ws.cell(1, 3).value = SPEC_LABEL
    for i, header in enumerate(BASE_HEADERS, start=1):
        ws.cell(2, i).value = header

    for i, date_str in enumerate(dates):
        first_col = FIRST_SHIFT_COL + i * len(SHIFT_HEADERS)
        ws.merge_cells(start_row=1, start_column=first_col, end_row=1, end_column=first_col + 3)
        cell = ws.cell(1, first_col)
        cell.value = _parse_date(date_str)
        cell.number_format = "M/D"
        for j, shift_name in enumerate(SHIFT_HEADERS):
            ws.cell(2, first_col + j).value = shift_name


def _parse_date(date_str: str) -> datetime:
    month, day = (int(part) for part in date_str.split("/"))
    return datetime(2026, month, day)


def _write_task_row(ws, row: int, task: ScheduledTask, col_of: Dict[ShiftKey, int], fill: PatternFill) -> None:
    ws.cell(row, 1).value = task.line
    ws.cell(row, 2).value = task.description
    ws.cell(row, 3).value = task.part_no
    ws.cell(row, 4).value = f"{task.duration_hours:.1f}H"
    ws.cell(row, 5).value = task.ct_seconds
    ws.cell(row, 6).value = task.trp
    ws.cell(row, 7).value = task.start_dt
    ws.cell(row, 7).number_format = "yyyy-mm-dd hh:mm"
    ws.cell(row, 8).value = task.order_id
    ws.cell(row, 9).value = int(round(task.quantity))
    for key, qty in task.shift_breakdown.items():
        col = col_of.get(key)
        if col is not None and qty:
            ws.cell(row, col).value = int(qty)
    for col in range(1, TOTAL_COL + 1):
        ws.cell(row, col).fill = fill


def _shift_loads(tasks: List[ScheduledTask]) -> Dict[ShiftKey, float]:
    loads: Dict[ShiftKey, float] = {}
    for task in tasks:
        for key, seconds in task.shift_seconds.items():
            loads[key] = loads.get(key, 0.0) + seconds
    return loads


def _write_summary_rows(ws, row: int, line: str, tasks: List[ScheduledTask], col_of: Dict[ShiftKey, int]) -> int:
    loads = _shift_loads(tasks)
    worked = sorted(loads, key=_shift_sort_key)
    closing = worked[-1] if worked else None
    output_row, hours_row, util_row = row, row + 1, row + 2

    for target, label in ((output_row, OUTPUT_SUMMARY), (hours_row, HOURS_SUMMARY), (util_row, UTILIZATION_SUMMARY)):
        ws.cell(target, 1).value = line
        ws.cell(target, 2).value = label

    ws.cell(output_row, 9).value = sum(int(round(t.quantity)) for t in tasks)
    ws.cell(hours_row, 4).value = f"{sum(t.duration_hours for t in tasks):.1f}H"
    ws.cell(hours_row, 9).value = round(sum(loads.values()) / 3600.0, 2)
    if worked:
        ws.cell(util_row, 9).value = round(
            sum(loads.values()) / (len(worked) * SHIFT_SECONDS) * 100, 1
        )

    for key, seconds in loads.items():
        col = col_of.get(key)
        if col is None:
            continue
        ws.cell(output_row, col).value = sum(int(t.shift_breakdown.get(key, 0)) for t in tasks)
        ws.cell(hours_row, col).value = round(seconds / 3600.0, 2)
        utilization = seconds / SHIFT_SECONDS
        cell = ws.cell(util_row, col)
        cell.value = round(utilization * 100, 1)
        cell.number_format = "0.0"
        if utilization < MIN_SHIFT_UTILIZATION - 1e-9:
            cell.fill = FILL_TAIL if key == closing else FILL_VIOLATION

    return util_row


def _shift_sort_key(key: ShiftKey) -> Tuple[int, int, int]:
    month, day = (int(part) for part in key[0].split("/"))
    return month, day, SHIFT_HEADERS.index(key[1]) if key[1] in SHIFT_HEADERS else 9


def _write_grand_total(ws, row: int, tasks: List[ScheduledTask], col_of: Dict[ShiftKey, int]) -> None:
    """Quantity totals, matching the total row of the 'Sample' sheet.

    Per-shift hours are deliberately not summed here: two lines share a shift
    slot, so an hours total would read as a capacity breach.
    """
    outputs: Dict[ShiftKey, int] = {}
    for task in tasks:
        for key, qty in task.shift_breakdown.items():
            outputs[key] = outputs.get(key, 0) + int(qty)

    ws.cell(row, 1).value = "全厂"
    ws.cell(row, 2).value = "总计(数量)"
    ws.cell(row, 4).value = f"{sum(t.duration_hours for t in tasks):.1f}H"
    ws.cell(row, 9).value = sum(int(round(t.quantity)) for t in tasks)
    for key, qty in outputs.items():
        col = col_of.get(key)
        if col is not None:
            ws.cell(row, col).value = qty
    for col in range(1, TOTAL_COL + 1):
        ws.cell(row, col).fill = FILL_SUMMARY


def _finish_sheet(ws, total_cols: int, last_row: int) -> None:
    for row in (1, 2):
        for col in range(1, total_cols + 1):
            cell = ws.cell(row, col)
            cell.font = Font(bold=True)
            cell.fill = FILL_HEADER
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = BORDER
    for row in range(3, last_row + 1):
        for col in range(1, total_cols + 1):
            ws.cell(row, col).border = BORDER
    for letter, width in COLUMN_WIDTHS.items():
        ws.column_dimensions[letter].width = width
    for col in range(FIRST_SHIFT_COL, total_cols + 1):
        ws.column_dimensions[get_column_letter(col)].width = 7
    ws.freeze_panes = f"{get_column_letter(FIRST_SHIFT_COL)}3"
