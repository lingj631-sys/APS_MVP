import io

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

p = "APS_业务规则确认清单.xlsx"
wb = openpyxl.load_workbook(p)
ws = wb["业务确认清单"]
rows = []
for r in range(4, ws.max_row + 1):
    if ws.cell(r, 3).value:
        rows.append([ws.cell(r, c).value for c in range(1, 14)])

# Archive sheet
if "已确认归档" in wb.sheetnames:
    del wb["已确认归档"]
arch = wb.create_sheet("已确认归档")
arch.merge_cells("A1:M1")
arch["A1"] = "已确认问题归档（2026-11-02，5 项）"
arch["A1"].font = Font(name="微软雅黑", size=14, bold=True, color="FF1E3A5F")
arch.row_dimensions[1].height = 30
headers = [ws.cell(3, c).value for c in range(1, 14)]
for c, h in enumerate(headers, start=1):
    cell = arch.cell(2, c, h)
    cell.font = Font(name="微软雅黑", size=9, bold=True, color="FFFFFFFF")
    cell.fill = PatternFill("solid", fgColor="FF1E3A5F")
    cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    cell.border = Border(*([Side(style="thin", color="FFBFBFBF")] * 4))
widths = [6, 8, 10, 24, 34, 62, 46, 32, 30, 14, 34, 30, 10]
for c, w in enumerate(widths, start=1):
    arch.column_dimensions[openpyxl.utils.get_column_letter(c)].width = w
for ri, values in enumerate(rows, start=3):
    for c, value in enumerate(values, start=1):
        cell = arch.cell(ri, c, value)
        cell.font = Font(name="微软雅黑", size=9)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        cell.border = Border(*([Side(style="thin", color="FFBFBFBF")] * 4))
    arch.cell(ri, 13).value = "已确认"
    arch.row_dimensions[ri].height = 160
arch.freeze_panes = "A3"

# Clear pending rows and leave a note
if ws.max_row >= 4:
    ws.delete_rows(4, ws.max_row - 3)
ws.cell(1, 1).value = "Phase B/C 业务确认清单（2026-11-02：当前无待确认）"
ws.cell(2, 1).value = "5 项问题已全部确认并归档到『已确认归档』；技术缺口见 aps_progress_dashboard.md。"
ws.merge_cells("A4:M4")
note = ws["A4"]
note.value = "当前没有待业务确认的问题。已确认的问题请查看『已确认归档』；数据仍待提供的项目见『数据提供清单』。"
note.font = Font(name="微软雅黑", size=11, bold=True, color="FF0B5394")
note.alignment = Alignment(wrap_text=True, vertical="center")
note.fill = PatternFill("solid", fgColor="FFE2EFDA")
ws.row_dimensions[4].height = 40

guide = wb["说明"]
guide["A1"].value = "APS 业务规则确认清单（2026-11-02）"
guide["B3"].value = "2026-11-02 已确认 5 项，当前无待确认问题；已确认内容归档在『已确认归档』。技术缺口见 aps_progress_dashboard.md。"
guide["B9"].value = "本版 pending 清单为空；后续新问题再追加，已确认记录不要删除归档。"
wb.save(p)
print("archived", len(rows), "rows")
