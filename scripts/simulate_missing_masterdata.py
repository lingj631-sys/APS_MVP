import os
import shutil

import openpyxl
from openpyxl.styles import Font

PATH = "APS_DataSource_Template.xlsx"
BACKUP = "archive/data/APS_DataSource_Template.pre_simulated_2026-11-02.xlsx"
shutil.copy2(PATH, BACKUP)

wb = openpyxl.load_workbook(PATH, data_only=False)


def col(ws, header):
    for c in range(1, ws.max_column + 1):
        if ws.cell(1, c).value == header:
            return c
    raise KeyError(header)


def rows(ws):
    return range(2, ws.max_row + 1)


# 1) FG/SF: special shift, simultaneous, SF mold ids
fg = wb["产线与成品参数 Parameters-FG"]
c_special = col(fg, "特殊班次")
c_sim = col(fg, "同时生产")
for r in rows(fg):
    fg.cell(r, c_special).value = "常规"
    if not fg.cell(r, c_sim).value:
        fg.cell(r, c_sim).value = "N"

sf = wb["半成品制造参数 Parameters-SFN"]
c_special = col(sf, "特殊班次")
c_sim = col(sf, "同时生产")
c_mold = col(sf, "模具编号 (Mold ID)")
mold_by_family = {
    "G38": "MOLD_WL_AXLE_01",
    "DX11": "MOLD_WL_AXLE_02",
    "E371": "MOLD_WL_AXLE_03",
    "930B": "MOLD_WL_AXLE_04",
    "CM2E": "MOLD_WL_AXLE_05",
    "L743": "MOLD_WL_AXLE_06",
    "LEAP": "MOLD_WL_AXLE_07",
    "KP31": "MOLD_WL_AXLE_08",
    "CX11": "MOLD_WL_AXLE_09",
}
c_family = col(sf, "产品族名")
for r in rows(sf):
    sf.cell(r, c_special).value = "常规"
    sf.cell(r, c_sim).value = "Y"
    if not sf.cell(r, c_mold).value:
        sf.cell(r, c_mold).value = mold_by_family.get(str(sf.cell(r, c_family).value), "MOLD_WL_AXLE_01")

# 2) BOM descriptions from FG/SF parameter sheets
fg_desc = {fg.cell(r, col(fg, "成品料号 (PN)")).value: fg.cell(r, col(fg, "品名描述")).value for r in rows(fg)}
sf_desc = {sf.cell(r, col(sf, "半成品料号 (PN)")).value: sf.cell(r, col(sf, "品名描述")).value for r in rows(sf)}
bom = wb["产品装配清单 BOM List"]
c_fg_no = col(bom, "母件：成品料号 (FG PN)")
c_fg_desc = col(bom, "母件品名描述")
c_sf_no = col(bom, "子件：半成品料号 (SF PN)")
c_sf_desc = col(bom, "子件品名描述")
for r in rows(bom):
    fg_no = bom.cell(r, c_fg_no).value
    sf_no = bom.cell(r, c_sf_no).value
    if not bom.cell(r, c_fg_desc).value and fg_no in fg_desc:
        bom.cell(r, c_fg_desc).value = fg_desc[fg_no]
    if not bom.cell(r, c_sf_desc).value and sf_no in sf_desc:
        bom.cell(r, c_sf_desc).value = sf_desc[sf_no]

# 3) Routing single-shift rated output = hours * 3600 / CT * TRP
line_base = wb["产线基础数据"]
c_line = col(line_base, "产线")
c_hours = col(line_base, "单班工时 (小时)")
c_trp = col(line_base, "效率 TRP")
base = {
    line_base.cell(r, c_line).value: (
        float(line_base.cell(r, c_hours).value or 8),
        float(line_base.cell(r, c_trp).value or 1),
    )
    for r in range(2, line_base.max_row + 1)
}
routing = wb["工艺路线与候选机台 Routing-Machines"]
c_primary = col(routing, "主候选线")
c_ct = col(routing, "CT (秒)")
c_out = col(routing, "单班定额产能 (件)")
for r in rows(routing):
    if routing.cell(r, c_out).value:
        continue
    hours, trp = base.get(routing.cell(r, c_primary).value, (8.0, 1.0))
    ct = float(routing.cell(r, c_ct).value or 0)
    if ct > 0:
        routing.cell(r, c_out).value = round(hours * 3600.0 / ct * trp, 1)

# 4) PM start/end times
pm = wb["设备预防维护 Preventive-Maintenance"]
c_start = col(pm, "开始时间")
c_end = col(pm, "结束时间")
c_start_date = col(pm, "开始日期")
c_end_date = col(pm, "结束日期")
for r in rows(pm):
    pm.cell(r, c_start).value = "08:00"
    pm.cell(r, c_end).value = "16:00"
    if float(pm.cell(r, col(pm, "停机工时 (小时)")).value or 0) > 24:
        pm.cell(r, c_end_date).value = "2026-10-15"

# 5) Add tooling rows for new simulated molds
tooling = wb["工装模具约束 Tooling-Mold"]
existing = {tooling.cell(r, col(tooling, "模具编号 (Mold ID)")).value for r in rows(tooling)}
new_molds = [
    ("MOLD_WL_AXLE_05", "CM2E系列轴套挤压模", "轴套6, 轴套7", "WC07786, WC08355", 1, 50),
    ("MOLD_WL_AXLE_06", "L743系列轴套拉延模", "轴套6, 轴套7", "WD48699, WD48700", 1, 50),
    ("MOLD_WL_AXLE_07", "LEAP系列轴套精密成型模", "轴套6, 轴套7", "WBC9318, WBC9324", 1, 55),
    ("MOLD_WL_AXLE_08", "KP31系列轴套成型模", "轴套6, 轴套7", "WCA1903, WCA1927", 1, 55),
    ("MOLD_WL_AXLE_09", "CX11系列轴套成型模", "轴套6, 轴套7", "W268001, W268002", 1, 55),
]
for mold, name, lines, parts, qty, setup in new_molds:
    if mold in existing:
        continue
    values = ["WL", mold, name, lines, parts, qty, setup]
    tooling.append(values)
    r = tooling.max_row
    for c in range(1, 8):
        tooling.cell(r, c).font = Font(name="微软雅黑", size=9)
        tooling.cell(r, c).alignment = openpyxl.styles.Alignment(wrap_text=True, vertical="top")

wb.save(PATH)
print("simulated master data written:", PATH)
print("backup:", BACKUP)
