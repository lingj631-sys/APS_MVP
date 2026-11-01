"""Fill the 2026-10 planner template with clearly-labelled simulated values.

Context: the planner's new 15-sheet framework removed several fields the solver
still needs (headcount, SF lead time) and left others empty.  This script fills
them from existing master data where possible, otherwise with documented mock
values, and adds the `生产提前期 (days)` column back to Parameters-SFN.

All simulated values are listed in Specification/masterdata_simulation_notes.md.
"""
from __future__ import annotations

import csv
import os
import shutil
import sys
from copy import copy
from datetime import datetime

import openpyxl

TEMPLATE = "APS_DataSource_Template.xlsx"
BACKUP = os.path.join("archive", "data", "APS_DataSource_Template.pre_simulation.xlsx")

# ---- reference data ------------------------------------------------------
campaign = {}
with open(os.path.join("data", "master", "Campaign-Families.csv"), encoding="utf-8-sig") as fh:
    for row in csv.DictReader(fh):
        part = (row.get("Part No.") or "").strip()
        if part:
            campaign[part] = (
                (row.get("Mold ID") or "").strip(),
                (row.get("Product Family") or "").strip(),
            )

wb = openpyxl.load_workbook(TEMPLATE)

def sheet(name_prefix):
    for ws in wb.worksheets:
        if ws.title.startswith(name_prefix) or ws.title.endswith(name_prefix):
            return ws
    raise KeyError(name_prefix)

def header_map(ws):
    return {str(ws.cell(1, c).value).strip(): c for c in range(1, ws.max_column + 1)}

def col(ws, name):
    return header_map(ws)[name]

if not os.path.exists(BACKUP):
    shutil.copy2(TEMPLATE, BACKUP)
    print("backup:", BACKUP)
else:
    if "--force" not in sys.argv:
        raise SystemExit(
            "模板已存在模拟补数备份；为避免覆盖你后续手工修改，"
            "如确需从备份重建请加 --force"
        )
    # Re-run from the clean pre-simulation copy so edits stay idempotent.
    shutil.copy2(BACKUP, TEMPLATE)
    wb = openpyxl.load_workbook(TEMPLATE)
    print("restored from backup:", BACKUP)

# ---- 1. 产线基础数据: dedupe to 3 unique lines ---------------------------
ws = sheet("产线基础数据")
headers = header_map(ws)
line_rows = [
    ("PGLINKAGE", 0.71, 8, 3),
    ("轴套6", 0.85, 8, 3),
    ("轴套7", 0.85, 8, 3),
]
if ws.max_row > 2:
    ws.delete_rows(3, ws.max_row - 2)
for idx, values in enumerate(line_rows, start=2):
    for c, name in enumerate(("产线", "效率 TRP", "单班工时 (小时)", "每天班次数"), start=1):
        cell = ws.cell(idx, c)
        cell.value = values[c - 1]
        if idx > 2:
            cell._style = copy(ws.cell(2, c)._style)
print("line_base rows:", ws.max_row - 1)

# ---- 2. Tooling / policy lookups ----------------------------------------
ws_tool = sheet("工装模具约束")
tool_h = header_map(ws_tool)
tool_parts = {}
for r in range(2, ws_tool.max_row + 1):
    mold = str(ws_tool.cell(r, tool_h["模具编号 (Mold ID)"]).value or "").strip()
    parts = str(ws_tool.cell(r, tool_h["适用零件料号"]).value or "")
    for part in parts.split(","):
        part = part.strip()
        if part and mold:
            tool_parts.setdefault(part, mold)

policy_max = {}
ws_pol = sheet("库存策略")
pol_h = header_map(ws_pol)
for r in range(2, ws_pol.max_row + 1):
    part = str(ws_pol.cell(r, pol_h["料号 (PN)"]).value or "").strip()
    period = str(ws_pol.cell(r, pol_h["周期类型"]).value or "").strip().upper()
    if part and period == "NORMAL":
        try:
            policy_max[part] = float(ws_pol.cell(r, pol_h["最大库存 (件)"]).value or 0)
        except (TypeError, ValueError):
            policy_max[part] = 0.0

# ---- 3. Parameters-FG: family + mold + keep special shift empty ----------
ws_fg = sheet("Parameters-FG")
fg_h = header_map(ws_fg)
fg_col_part = fg_h["成品料号 (PN)"]
fg_filled = 0
mold_to_fg = {}
for r in range(2, ws_fg.max_row + 1):
    part = str(ws_fg.cell(r, fg_col_part).value or "").strip()
    if not part:
        continue
    campaign_mold, family = campaign.get(part, ("", ""))
    mold = campaign_mold or tool_parts.get(part, "")
    ws_fg.cell(r, fg_h["产品族名"]).value = family or None
    ws_fg.cell(r, fg_h["模具编号 (Mold ID)"]).value = mold or None
    if mold:
        mold_to_fg.setdefault(mold, []).append(part)
    fg_filled += 1
print("FG rows:", fg_filled)

# Keep Tooling-Mold's 适用零件料号 consistent with the FG mold assignment.
if mold_to_fg:
    for r in range(2, ws_tool.max_row + 1):
        mold = str(ws_tool.cell(r, tool_h["模具编号 (Mold ID)"]).value or "").strip()
        if not mold or mold not in mold_to_fg:
            continue
        existing = [p.strip() for p in str(ws_tool.cell(r, tool_h["适用零件料号"]).value or "").split(",") if p.strip()]
        merged = existing + [p for p in mold_to_fg[mold] if p not in existing]
        ws_tool.cell(r, tool_h["适用零件料号"]).value = ", ".join(merged)
    print("tooling parts updated for:", sorted(mold_to_fg))

# ---- 4. Parameters-SFN: family, mold, max stock -------------------------
ws_sf = sheet("Parameters-SFN")
sf_h = header_map(ws_sf)
sf_col_part = sf_h["半成品料号 (PN)"]
sf_filled = 0
for r in range(2, ws_sf.max_row + 1):
    part = str(ws_sf.cell(r, sf_col_part).value or "").strip()
    if not part:
        continue
    family = campaign.get(part, ("", ""))[1]
    mold = tool_parts.get(part, "")
    ws_sf.cell(r, sf_h["产品族名"]).value = family or None
    ws_sf.cell(r, sf_h["模具编号 (Mold ID)"]).value = mold or None
    ws_sf.cell(r, sf_h["最大库存 (件)"]).value = policy_max.get(part) or 0
    sf_filled += 1
print("SFN rows:", sf_filled)

# ---- 5. Routing: headcount + family, clear stray note -------------------
ws_rt = sheet("工艺路线")
rt_h = header_map(ws_rt)
rt_part = rt_h["料号 (PN)"]
rt_stage = rt_h["阶段"]
rt_head = rt_h["人数"]
rt_family = rt_h["产品族名"]
rt_capacity = rt_h["单班定额产能 (件)"]
rt_filled = 0
for r in range(2, ws_rt.max_row + 1):
    part = str(ws_rt.cell(r, rt_part).value or "").strip()
    if not part:
        continue
    stage = str(ws_rt.cell(r, rt_stage).value or "").strip().upper()
    ws_rt.cell(r, rt_head).value = 3 if stage == "FG" else 2
    ws_rt.cell(r, rt_family).value = campaign.get(part, ("", ""))[1] or None
    # the old template left a text note in this numeric column
    value = ws_rt.cell(r, rt_capacity).value
    if isinstance(value, str) and value.strip():
        ws_rt.cell(r, rt_capacity).value = None
    rt_filled += 1
print("Routing rows:", rt_filled)

# ---- 6. Material-Arrival-ETA: fill ETA time by shift --------------------
ws_ma = sheet("关键物料到货")
ma_h = header_map(ws_ma)
shift_time = {"早班": "08:00", "中班": "14:00", "晚班": "22:00"}
ma_filled = 0
for r in range(2, ws_ma.max_row + 1):
    part = str(ws_ma.cell(r, ma_h["物料料号 (PN)"]).value or "").strip()
    if not part:
        continue
    shift = str(ws_ma.cell(r, ma_h["预计入库班次"]).value or "").strip()
    ws_ma.cell(r, ma_h["预计入库时间"]).value = shift_time.get(shift, "08:00")
    ma_filled += 1
print("Material rows:", ma_filled)

# ---- 7. Operation-LeadTime: fill prep/rest minutes ----------------------
# 语义：提前准备时间 = 静置/等待 + 首批转运准备；4~12h 为主，极少数可 45min。
ws_lt = sheet("工序提前期")
lt_h = header_map(ws_lt)
SF_REST_MINUTES = {
    "G38": 45,      # 可极限 45min 转运
    "DX11": 480,    # 8h
    "E371": 480,
    "930B": 360,
    "CM2E": 300,
    "L743": 360,
    "LEAP": 420,
    "KP31": 300,
    "CX11": 240,
}
lt_filled = 0
for r in range(2, ws_lt.max_row + 1):
    part = str(ws_lt.cell(r, lt_h["料号 (PN)"]).value or "").strip()
    if not part:
        continue
    stage = str(ws_lt.cell(r, lt_h["阶段"]).value or "").strip().upper()
    if stage == "SF":
        family = campaign.get(part, ("", ""))[1]
        minutes = SF_REST_MINUTES.get(family, 360)
    else:
        minutes = 45
    ws_lt.cell(r, lt_h["提前准备时间（分钟）"]).value = minutes
    lt_filled += 1
print("Operation-LeadTime rows:", lt_filled)

# ---- 8. BOM-referenced semi-finished parts missing from SFN -------------
# WB80510 BOM requires W268001/W268002 but they had no SFN/Routing rows, so the
# planner silently skipped their kitting.  Add simulated CX11 axle sub-assemblies.
EXTRA_SF = [
    ("W268001", "CX11 DS 轴套分总成"),
    ("W268002", "CX11 PS 轴套分总成"),
]

def next_data_row(ws, key_col=2):
    r = 2
    while r <= ws.max_row and str(ws.cell(r, key_col).value or "").strip():
        r += 1
    return r

def append_row(ws, mapping, style_row=2):
    r = next_data_row(ws, key_col=2)
    headers = header_map(ws)
    for header, value in mapping.items():
        c = headers[header]
        cell = ws.cell(r, c)
        cell._style = copy(ws.cell(style_row, c)._style)
        cell.value = value
    return r

for part, desc in EXTRA_SF:
    append_row(ws_sf, {
        "站点": "WL",
        "半成品料号 (PN)": part,
        "品名描述": desc,
        "最小起投批量 MPQ": 100,
        "报废率 (小数)": 0.03,
        "最大库存 (件)": 1000,
        "最小起订量 MOQ": 100,
        "产品族名": "CX11",
    })
    append_row(ws_rt, {
        "站点": "WL",
        "料号 (PN)": part,
        "阶段": "SF",
        "工序序号": 10,
        "工序名称": "冲压/注塑",
        "主候选线": "轴套6",
        "认证": "TRUE",
        "CT (秒)": 25.0,
        "换型时间 (分钟)": 45,
        "优先级": 1,
        "生效日期": "2026-01-01",
        "失效日期": "2026-12-31",
        "人数": 2,
        "产品族名": "CX11",
    })
    append_row(ws_lt, {
        "站点": "WL",
        "料号 (PN)": part,
        "阶段": "SF",
        "工序序号": 10,
        "工序名称": "冲压/注塑",
        "提前准备时间（分钟）": 240,
        "转运批量 (件)": 100,
        "容器": "标准周转箱",
    })
    append_row(ws_pol, {
        "站点": "WL",
        "料号 (PN)": part,
        "周期类型": "NORMAL",
        "起始日 (Day)": 1,
        "结束日 (Day)": 25,
        "目标库存天数": 1,
        "最大库存天数": 3,
        "安全库存 (件)": 50,
        "最大库存 (件)": 1000,
        "生效日期": "2026-10-01",
    })
    append_row(ws_pol, {
        "站点": "WL",
        "料号 (PN)": part,
        "周期类型": "MONTH_END",
        "起始日 (Day)": 26,
        "结束日 (Day)": 31,
        "目标库存天数": 1,
        "最大库存天数": 2,
        "安全库存 (件)": 25,
        "最大库存 (件)": 500,
        "生效日期": "2026-10-01",
    })
print("added SF parts:", [p for p, _ in EXTRA_SF])

# ---- 9. Rebuild the Instructions sheet from the canonical schema --------
from src.instructions import build_counts, write_instructions  # noqa: E402

ws_ins = sheet("填写说明")
write_instructions(ws_ins, build_counts(wb))
print("instructions rebuilt:", ws_ins.max_row, "rows")

wb.save(TEMPLATE)
print("saved", TEMPLATE, "at", datetime.now().strftime("%H:%M:%S"))