"""
Exporter for APS MVP.
Writes the scheduled tasks into the 'Production plan-Done' sheet of the Excel workbook.
Ensures all values are clean integers (multiples of MPQ) with ZERO decimals.
Preserves existing formulas in other sheets by loading with data_only=False.
"""
from dataclasses import dataclass
from typing import List, Dict, Tuple, Any
from datetime import datetime
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from src.shift_manager import ShiftBucket

@dataclass
class ScheduledTask:
    line: str
    part_no: str
    description: str
    ct_seconds: float
    trp: float
    order_id: str
    quantity: float
    start_dt: datetime
    end_dt: datetime
    duration_hours: float
    shift_breakdown: Dict[Tuple[str, str], int]  # (date_str, shift_name) -> integer qty

def export_production_plan(
    template_excel_path: str,
    output_excel_path: str,
    scheduled_tasks: List[ScheduledTask],
    planning_dates: List[str]
) -> None:
    # Use data_only=False to preserve all Excel formulas in other sheets!
    wb = openpyxl.load_workbook(template_excel_path, data_only=False)
    if 'Production plan-Done' not in wb.sheetnames:
        ws = wb.create_sheet('Production plan-Done')
    else:
        ws = wb['Production plan-Done']

    # Clear old data starting from row 3 downwards
    if ws.max_row >= 3:
        ws.delete_rows(3, ws.max_row - 2)

    base_headers = ['Production Line', 'Part No.', 'Description', 'CT', 'TRP', '制造开始时间', '合计']
    shifts_per_day = ['早班', '中班', '晚班']

    # Rewrite Row 1 and Row 2
    for c in range(1, 8):
        ws.cell(1, c).value = None
    col_idx = 8
    date_col_map = {}
    for d_str in planning_dates:
        ws.cell(1, col_idx).value = d_str
        for s_name in shifts_per_day:
            date_col_map[(d_str, s_name)] = col_idx
            ws.cell(2, col_idx).value = s_name
            col_idx += 1

    for c, h in enumerate(base_headers, 1):
        ws.cell(2, c).value = h

    # Sort tasks: by line (PGLINKAGE first, then 轴套6), then by start_dt
    sorted_tasks = sorted(scheduled_tasks, key=lambda t: (0 if t.line == 'PGLINKAGE' else 1, t.start_dt))

    fill_pg = PatternFill(start_color="E8F0FE", end_color="E8F0FE", fill_type="solid")
    fill_sf = PatternFill(start_color="FEF7E0", end_color="FEF7E0", fill_type="solid")

    thin_border = Border(
        left=Side(style='thin', color='D3D3D3'),
        right=Side(style='thin', color='D3D3D3'),
        top=Side(style='thin', color='D3D3D3'),
        bottom=Side(style='thin', color='D3D3D3')
    )

    current_row = 3
    last_line = None

    for t in sorted_tasks:
        if last_line and t.line != last_line:
            # empty separator row
            current_row += 1
        last_line = t.line

        ws.cell(current_row, 1).value = t.line
        ws.cell(current_row, 2).value = t.part_no
        ws.cell(current_row, 3).value = t.description
        ws.cell(current_row, 4).value = t.ct_seconds
        ws.cell(current_row, 5).value = t.trp
        ws.cell(current_row, 6).value = t.start_dt.strftime('%Y-%m-%d %H:%M:%S')
        # Total is integer
        ws.cell(current_row, 7).value = int(round(t.quantity))

        fill_color = fill_pg if t.line == 'PGLINKAGE' else fill_sf
        for c in range(1, 8):
            ws.cell(current_row, c).fill = fill_color
            ws.cell(current_row, c).border = thin_border

        # shift values: pure integers
        for (d_str, s_name), qty in t.shift_breakdown.items():
            if (d_str, s_name) in date_col_map and qty > 0:
                c_idx = date_col_map[(d_str, s_name)]
                cell = ws.cell(current_row, c_idx)
                cell.value = int(round(qty))
                cell.alignment = Alignment(horizontal='right')

        current_row += 1

    wb.save(output_excel_path)
    print(f"Successfully exported {len(sorted_tasks)} scheduled tasks to {output_excel_path}")
