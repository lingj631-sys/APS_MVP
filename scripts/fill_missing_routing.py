import openpyxl
from openpyxl.styles import Alignment, Font

PATH = "APS_DataSource_Template.xlsx"
wb = openpyxl.load_workbook(PATH, data_only=False)
routing = wb["工艺路线与候选机台 Routing-Machines"]
fg = wb["产线与成品参数 Parameters-FG"]
sf = wb["半成品制造参数 Parameters-SFN"]
line_base = wb["产线基础数据"]


def col(ws, header):
    for c in range(1, ws.max_column + 1):
        if ws.cell(1, c).value == header:
            return c
    raise KeyError(header)


def rows(ws):
    return range(2, ws.max_row + 1)


existing = {(routing.cell(r, col(routing, "料号 (PN)")).value, routing.cell(r, col(routing, "阶段")).value) for r in rows(routing)}
base = {
    line_base.cell(r, col(line_base, "产线")).value: (
        float(line_base.cell(r, col(line_base, "单班工时 (小时)")).value or 8),
        float(line_base.cell(r, col(line_base, "效率 TRP")).value or 1),
    )
    for r in rows(line_base)
}

add = []
for r in rows(fg):
    pn = fg.cell(r, col(fg, "成品料号 (PN)")).value
    if (pn, "FG") not in existing:
        add.append((pn, "FG", "20", "总装", "PGLINKAGE", 46.0, 30, 3))
for r in rows(sf):
    pn = sf.cell(r, col(sf, "半成品料号 (PN)")).value
    if (pn, "SF") not in existing:
        add.append((pn, "SF", "10", "轴套成型", "轴套6", 90.0, 45, 2))

for pn, stage, op, opname, line, ct, setup, crew in add:
    hours, trp = base.get(line, (8.0, 1.0))
    values = [
        "WL", pn, stage, op, opname, line, "TRUE", ct, setup, 1,
        "2026-01-01", "2026-12-31", round(hours * 3600.0 / ct * trp, 1), crew, "SIM",
    ]
    routing.append(values)
    r = routing.max_row
    for c in range(1, len(values) + 1):
        routing.cell(r, c).font = Font(name="微软雅黑", size=9)
        routing.cell(r, c).alignment = Alignment(wrap_text=True, vertical="top")

wb.save(PATH)
print("added routing rows", len(add))
