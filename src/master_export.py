"""Export a site's current master data back into the 15-sheet template layout.

Tab-6 "下载主数据填写模板" must hand each plant a workbook that already carries
*that plant's* data, otherwise a SY38 planner would download WL rows and have to
rewrite every site/line by hand.  This module copies the canonical template (for
headers/styles) and replaces the data rows with the site's current snapshot +
master-table rows.
"""
from __future__ import annotations

import csv
import os
from copy import copy
from typing import Any, Dict, List, Optional

import openpyxl
from openpyxl.utils import get_column_letter

from src.instructions import write_instructions
from src.master_schema import SHEET_BY_KEY, column_positions, resolve_sheet


def _campaign_map() -> Dict[str, Dict[str, str]]:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "master", "Campaign-Families.csv")
    out: Dict[str, Dict[str, str]] = {}
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            part = (row.get("Part No.") or "").strip()
            if part:
                out[part] = {
                    "family": (row.get("Product Family") or "").strip(),
                    "mold": (row.get("Mold ID") or "").strip(),
                }
    return out


def _enrich_rows(master_rows: Dict[str, List[dict]], snapshot) -> Dict[str, List[dict]]:
    """Fill optional fields that the legacy source doesn't carry in v2 columns."""
    campaign = _campaign_map()
    tool_parts: Dict[str, str] = {}
    for row in master_rows.get("tooling", []):
        mold = str(row.get("mold_id") or "").strip()
        for part in str(row.get("applicable_parts") or "").split(","):
            part = part.strip()
            if part and mold:
                tool_parts.setdefault(part, mold)

    headcount = {}
    for param in list(getattr(snapshot, "fg_params", {}).values()) + list(getattr(snapshot, "sf_params", {}).values()):
        if param.headcount > 0:
            headcount[param.part_no] = param.headcount

    for key in ("parameters_fg", "parameters_sfn"):
        for row in master_rows.get(key, []):
            part = str(row.get("part_no") or "").strip()
            meta = campaign.get(part, {})
            if not row.get("product_family"):
                row["product_family"] = meta.get("family") or None
            if not row.get("mold_id"):
                row["mold_id"] = tool_parts.get(part) or meta.get("mold") or None

    for row in master_rows.get("routing", []):
        part = str(row.get("part_no") or "").strip()
        meta = campaign.get(part, {})
        if not row.get("headcount"):
            row["headcount"] = headcount.get(part)
        if not row.get("product_family"):
            row["product_family"] = meta.get("family") or None
    return master_rows


def _clear_rows(ws) -> None:
    if ws.max_row > 1:
        ws.delete_rows(2, ws.max_row - 1)


def _write_sheet(ws, spec, rows: List[dict]) -> None:
    positions = column_positions(ws, spec)
    active = [c for c in spec.active_columns() if c.key in positions]
    styles = {
        col.key: (copy(ws.cell(2, positions[col.key])._style), ws.cell(2, positions[col.key]).number_format)
        for col in active
    }
    _clear_rows(ws)
    for offset, row in enumerate(rows, start=2):
        ws.row_dimensions[offset].height = 22
        for col in active:
            cell = ws.cell(offset, positions[col.key])
            style, number_format = styles[col.key]
            cell._style = copy(style)
            cell.number_format = number_format
            value = row.get(col.key)
            cell.value = value if value not in ("",) else None
    last_col = max(positions.values()) if positions else ws.max_column
    ws.auto_filter.ref = f"A1:{get_column_letter(last_col)}{max(len(rows) + 1, 2)}"


def _write_demand(ws, spec, snapshot, site: str) -> None:
    """Demand is a date-axis wide table; rebuild it from snapshot.orders."""
    headers = {str(ws.cell(1, c).value).strip(): c for c in range(1, ws.max_column + 1)}
    part_col = headers.get("料号 (PN)", 2)
    desc_col = headers.get("品名描述", 3)
    site_col = headers.get("站点", 1)
    date_styles = {
        c: (copy(ws.cell(2, c)._style), ws.cell(2, c).number_format)
        for c in range(4, ws.max_column + 1)
    }
    # Date headers keep the template's header style (not a data-row style).
    date_header_style = {
        c: (copy(ws.cell(1, c)._style), ws.cell(1, c).number_format)
        for c in range(4, ws.max_column + 1)
    }
    default_header = (copy(ws.cell(1, 4)._style), ws.cell(1, 4).number_format)
    default_data = date_styles.get(4) or (
        copy(ws.cell(2, site_col)._style), ws.cell(2, site_col).number_format
    )
    base_style = (copy(ws.cell(2, site_col)._style), ws.cell(2, site_col).number_format)
    _clear_rows(ws)

    dates = list(snapshot.planning_dates)
    by_part: Dict[str, Dict[str, Any]] = {}
    for order in snapshot.orders:
        item = by_part.setdefault(order.part_no, {"desc": order.description, "qty": {}})
        item["qty"][order.due_date] = item["qty"].get(order.due_date, 0.0) + float(order.demand_quantity)

    for idx, date in enumerate(dates, start=4):
        header_cell = ws.cell(1, idx)
        style, number_format = date_header_style.get(idx, default_header)
        header_cell._style = copy(style)
        header_cell.number_format = number_format
        header_cell.value = date
        if idx not in date_header_style:
            ws.column_dimensions[get_column_letter(idx)].width = 10
    for idx in range(4 + len(dates), ws.max_column + 1):
        ws.cell(1, idx).value = None

    for row_idx, part in enumerate(sorted(by_part), start=2):
        ws.row_dimensions[row_idx].height = 22
        data = by_part[part]
        for c in (site_col, part_col, desc_col):
            cell = ws.cell(row_idx, c)
            cell._style = copy(base_style[0])
            cell.number_format = base_style[1]
        ws.cell(row_idx, site_col).value = site
        ws.cell(row_idx, part_col).value = part
        ws.cell(row_idx, desc_col).value = data["desc"]
        for idx, date in enumerate(dates, start=4):
            cell = ws.cell(row_idx, idx)
            style, number_format = date_styles.get(idx, default_data)
            cell._style = copy(style)
            cell.number_format = number_format
            cell.value = data["qty"].get(date) or None
    last_col = 3 + len(dates)
    ws.auto_filter.ref = f"A1:{get_column_letter(last_col)}{max(len(by_part) + 1, 2)}"


def export_site_workbook(
    snapshot,
    master_rows: Dict[str, List[dict]],
    template_path: str,
    out_path: str,
    site: str,
) -> str:
    wb = openpyxl.load_workbook(template_path)
    site = site.upper()
    master_rows = _enrich_rows(master_rows, snapshot)

    for key in ("demand", "stock", "material_arrival", "parameters_fg", "parameters_sfn",
                "bom", "routing", "setup_matrix", "inventory_policy", "operation_leadtime",
                "tooling", "line_base", "pm", "customer"):
        spec = SHEET_BY_KEY[key]
        ws = resolve_sheet(wb, spec)
        if ws is None:
            continue
        if key == "demand":
            _write_demand(ws, spec, snapshot, site)
        elif key == "line_base":
            rows = [
                {
                    "line": lb.line,
                    "trp": lb.trp,
                    "hours_per_shift": lb.hours_per_shift,
                    "shifts_per_day": lb.shifts_per_day,
                }
                for lb in getattr(snapshot, "line_base", [])
            ]
            _write_sheet(ws, spec, rows)
        else:
            rows = master_rows.get(key, [])
            _write_sheet(ws, spec, rows)

    ws_ins = resolve_sheet(wb, SHEET_BY_KEY["instructions"])
    if ws_ins is not None:
        counts = {}
        for key in ("demand", "stock", "material_arrival", "parameters_fg", "parameters_sfn",
                    "bom", "routing", "setup_matrix", "inventory_policy", "operation_leadtime",
                    "tooling", "line_base", "pm", "customer"):
            spec = SHEET_BY_KEY[key]
            if key == "line_base":
                counts[spec.title] = len(getattr(snapshot, "line_base", []))
            elif key == "demand":
                counts[spec.title] = len({o.part_no for o in snapshot.orders})
            else:
                counts[spec.title] = len(master_rows.get(key, []))
        write_instructions(ws_ins, counts)

    wb.save(out_path)
    return out_path
