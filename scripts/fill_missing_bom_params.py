import openpyxl
from openpyxl.styles import Alignment, Font

PATH = "APS_DataSource_Template.xlsx"
wb = openpyxl.load_workbook(PATH, data_only=False)

fg = wb["产线与成品参数 Parameters-FG"]
sf = wb["半成品制造参数 Parameters-SFN"]
bom = wb["产品装配清单 BOM List"]


def col(ws, header):
    for c in range(1, ws.max_column + 1):
        if ws.cell(1, c).value == header:
            return c
    raise KeyError(header)


fg_no = col(fg, "成品料号 (PN)")
sf_no = col(sf, "半成品料号 (PN)")
bom_fg = col(bom, "母件：成品料号 (FG PN)")
bom_sf = col(bom, "子件：半成品料号 (SF PN)")

fg_set = {fg.cell(r, fg_no).value for r in range(2, fg.max_row + 1)}
sf_set = {sf.cell(r, sf_no).value for r in range(2, sf.max_row + 1)}
parents = sorted({bom.cell(r, bom_fg).value for r in range(2, bom.max_row + 1)} - fg_set)
children = sorted({bom.cell(r, bom_sf).value for r in range(2, bom.max_row + 1)} - sf_set)

# Add simulated FG parameter rows for BOM parents that are not in Parameters-FG.
for pn in parents:
    values = ["WL", pn, f"模拟-{pn}", 60, 8, 3, 60, "SIM", "MOLD_WL_LINK_01", "常规", "N"]
    fg.append(values)
    r = fg.max_row
    for c in range(1, len(values) + 1):
        fg.cell(r, c).font = Font(name="微软雅黑", size=9)
        fg.cell(r, c).alignment = Alignment(wrap_text=True, vertical="top")

# Add simulated SF parameter rows for BOM children that are not in Parameters-SFN.
for pn in children:
    values = ["WL", pn, f"模拟-{pn}", 100, 0.03, 1000, 100, "SIM", "MOLD_WL_AXLE_01", "常规", "Y"]
    sf.append(values)
    r = sf.max_row
    for c in range(1, len(values) + 1):
        sf.cell(r, c).font = Font(name="微软雅黑", size=9)
        sf.cell(r, c).alignment = Alignment(wrap_text=True, vertical="top")

# Refill BOM descriptions from the extended parameter tables.
fg_desc = {fg.cell(r, fg_no).value: fg.cell(r, col(fg, "品名描述")).value for r in range(2, fg.max_row + 1)}
sf_desc = {sf.cell(r, sf_no).value: sf.cell(r, col(sf, "品名描述")).value for r in range(2, sf.max_row + 1)}
bom_fg_desc = col(bom, "母件品名描述")
bom_sf_desc = col(bom, "子件品名描述")
for r in range(2, bom.max_row + 1):
    p = bom.cell(r, bom_fg).value
    c = bom.cell(r, bom_sf).value
    if not bom.cell(r, bom_fg_desc).value and p in fg_desc:
        bom.cell(r, bom_fg_desc).value = fg_desc[p]
    if not bom.cell(r, bom_sf_desc).value and c in sf_desc:
        bom.cell(r, bom_sf_desc).value = sf_desc[c]

wb.save(PATH)
print("added FG", len(parents), "SF", len(children))
