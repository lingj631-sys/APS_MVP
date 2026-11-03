"""Workbook reader for the 2026-10 planner master-data framework.

The legacy loader read fixed column positions from English-headed sheets.  The
2026-10 framework renames every sheet/column, reorders columns, moves CT /
headcount / target stock to other tables and adds a line-base sheet, so this
module resolves everything through ``src.master_schema`` instead of positions.

It supports both layouts:

* ``v2`` – the 15-sheet Chinese framework (``APS_DataSource_Template.xlsx``)
* ``legacy`` – the 25-sheet English workbook (``dataSource.xlsx``)

When a v2 workbook omits a field that the solver still needs, the loader derives
it from the sheet the framework moved it to and records a warning:

===============  ==========================================================
solver field     v2 source
===============  ==========================================================
ct_seconds       Routing-Machines ``CT (秒)`` (legacy: ``WL CT`` / FG column)
headcount        Routing-Machines ``人数``
target_stock     Inventory-Policy ``安全库存 (件)`` (NORMAL period)
trp/hours/shift  产线基础数据 (falls back to the FG/SFN row)
mold_id          FG/SFN ``模具编号`` when it exists in Tooling-Mold, else the
                 Tooling-Mold ``适用零件料号`` reverse lookup
===============  ==========================================================
"""
from __future__ import annotations

import math
import re
from collections import OrderedDict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import openpyxl

from src.master_schema import (
    PARAMETERS_FG,
    PARAMETERS_SFN,
    SHEET_BY_KEY,
    SheetSpec,
    column_positions,
    detect_layout,
    extent_of,
    header_row_for,
    norm,
    resolve_sheet,
)


def _text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _number(value, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return default
    if text.endswith("%"):
        text = text[:-1]
        try:
            return float(text) / 100.0
        except ValueError:
            return default
    try:
        return float(text)
    except ValueError:
        return default


def _int(value, default: int = 0) -> int:
    try:
        return int(_number(value, float(default)))
    except (TypeError, ValueError):
        return default


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).upper() in ("Y", "YES", "TRUE", "1", "是", "√")


def _date_str(value) -> str:
    if value is None or _text(value) == "":
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)) and 20000 < float(value) < 80000:
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date().isoformat()
    text = _text(value)
    match = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        y, m, d = (int(x) for x in match.groups())
        return f"{y:04d}-{m:02d}-{d:02d}"
    return text[:10]


def _shift_from_time(value, site: str, default: str = "早班") -> str:
    """Map a shift name or clock time to the plant's shift name."""
    text = _text(value)
    if not text:
        return default
    if any(key in text for key in ("早", "中", "晚", "白", "夜")):
        return text
    match = re.match(r"^(\d{1,2})[:：]", text)
    if not match:
        return text
    hour = int(match.group(1))
    if site.upper() == "SY38":
        return "晚班" if (hour >= 20 or hour < 8) else "早班"
    if hour < 7:
        return "晚班"
    if hour < 15:
        return "早班"
    if hour < 23:
        return "中班"
    return "晚班"


def _shift_sequence(site: str) -> Tuple[str, ...]:
    return ("早班", "晚班") if site.upper() == "SY38" else ("早班", "中班", "晚班")


def _advance_shift(start_date: str, start_shift: str, count: int,
                   site: str) -> Tuple[str, str]:
    """Advance ``count`` shifts from ``start_shift``; returns (date, shift)."""
    sequence = _shift_sequence(site)
    shift = start_shift if start_shift in sequence else sequence[0]
    index = sequence.index(shift) + max(1, count) - 1
    try:
        base = datetime.strptime(start_date, "%Y-%m-%d").date()
    except ValueError:
        return start_date, shift
    end_date = base + timedelta(days=index // len(sequence))
    return end_date.isoformat(), sequence[index % len(sequence)]


class _Table:
    """Schema-aware view over one worksheet."""

    def __init__(self, ws, spec: SheetSpec):
        self.ws = ws
        self.spec = spec
        self.pos = column_positions(ws, spec)
        self.header_row = header_row_for(ws, spec)

    def has(self, key: str) -> bool:
        return key in self.pos

    def val(self, row: int, key: str):
        idx = self.pos.get(key)
        if idx is None:
            return None
        return self.ws.cell(row, idx).value

    def rows(self, site: Optional[str] = None):
        site_idx = self.pos.get("site")
        last_row = extent_of(getattr(self.ws, "max_row", None))
        if last_row:
            candidates = range(self.header_row + 1, last_row + 1)
        else:  # read-only sheet saved without <dimension>: let openpyxl walk it
            candidates = (
                row
                for row, _values in enumerate(
                    self.ws.iter_rows(min_row=self.header_row + 1),
                    start=self.header_row + 1,
                )
            )
        for row in candidates:
            if site_idx is not None:
                value = _text(self.ws.cell(row, site_idx).value)
                if value and site and value != site:
                    continue
            yield row

    def is_empty_row(self, row: int) -> bool:
        return not any(
            _text(self.ws.cell(row, idx).value)
            for idx in self.pos.values()
        )


def _table(wb, key: str) -> Optional[_Table]:
    spec = SHEET_BY_KEY[key]
    ws = resolve_sheet(wb, spec)
    return _Table(ws, spec) if ws is not None else None


# --------------------------------------------------------------------------
# Demand date axis
# --------------------------------------------------------------------------
def _demand_header_row(ws) -> Optional[int]:
    for row_idx, values in enumerate(
        ws.iter_rows(min_row=1, max_row=6, max_col=8, values_only=True), start=1
    ):
        for value in values:
            if norm(value) in ("料号(pn)", "pn", "partno.", "partno"):
                return row_idx
    return None


def _demand_dates(ws, header_row: int) -> List[Tuple[int, str]]:
    dates: List[Tuple[int, str]] = []
    header_values = next(
        ws.iter_rows(min_row=header_row, max_row=header_row, values_only=True), ()
    )
    for col, value in enumerate(header_values, start=1):
        if col < 4 or value is None:
            continue
        if isinstance(value, datetime):
            dates.append((col, f"{value.month}/{value.day}"))
            continue
        if isinstance(value, (int, float)) and 40000 < float(value) < 80000:
            parsed = datetime(1899, 12, 30) + timedelta(days=float(value))
            dates.append((col, f"{parsed.month}/{parsed.day}"))
            continue
        match = re.match(r"^(\d{1,2})/(\d{1,2})$", _text(value))
        if match:
            dates.append((col, f"{int(match.group(1))}/{int(match.group(2))}"))
    return dates


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------
def load_workbook_snapshot(excel_path: str, site_filter: str = "WL"):
    """Parse a planner workbook (v2 or legacy) into a ``ProductionInputSnapshot``."""
    # Imported lazily: data_loader imports this module from within a function.
    from src.data_loader import (
        CustomerOrder,
        CustomerTier,
        LineBase,
        MaterialArrival,
        PreventiveMaintenance,
        ProductParam,
        ProductionInputSnapshot,
        ToolingMold,
    )

    wb = openpyxl.load_workbook(excel_path, data_only=True)
    try:
        layout = detect_layout(wb)
        warnings: List[str] = []

        # ---- Routing: CT / headcount fallbacks -------------------------
        routing = _table(wb, "routing")
        routing_ct: Dict[str, float] = {}
        routing_headcount: Dict[str, float] = {}
        routing_output: Dict[str, float] = {}
        routing_line: Dict[str, str] = {}
        if routing is not None:
            for row in routing.rows(site_filter):
                pn = _text(routing.val(row, "part_no"))
                if not pn:
                    continue
                ct = _number(routing.val(row, "ct_seconds"))
                if ct > 0:
                    routing_ct[pn] = min(routing_ct.get(pn, ct), ct)
                headcount = _number(routing.val(row, "headcount"))
                if headcount > 0 and pn not in routing_headcount:
                    routing_headcount[pn] = headcount
                output = _number(routing.val(row, "output_per_shift"))
                if output > 0 and pn not in routing_output:
                    routing_output[pn] = output
                line = _text(routing.val(row, "primary_line"))
                if line and pn not in routing_line:
                    routing_line[pn] = line

        # ---- Legacy WL CT sheet (kept for the 25-sheet workbook) -------
        ct_sheet = next((name for name in wb.sheetnames if _text(name) == "WL CT"), None)
        if ct_sheet is not None:
            ws_ct = wb[ct_sheet]
            for row in range(2, ws_ct.max_row + 1):
                material = _text(ws_ct.cell(row, 1).value)
                ct_value = _number(ws_ct.cell(row, 3).value)
                if material and ct_value > 0:
                    routing_ct.setdefault(material, ct_value)

        # ---- Inventory policy: safety stock -> target stock -------------
        inventory = _table(wb, "inventory_policy")
        safety_stock: Dict[str, float] = {}
        if inventory is not None:
            for row in inventory.rows(site_filter):
                pn = _text(inventory.val(row, "part_no"))
                if not pn:
                    continue
                period = _text(inventory.val(row, "period_type")).upper()
                if period and period != "NORMAL":
                    continue
                value = _number(inventory.val(row, "safety_stock"))
                if pn not in safety_stock:
                    safety_stock[pn] = value

        # ---- Line base --------------------------------------------------
        line_base_spec = SHEET_BY_KEY["line_base"]
        line_table = _table(wb, "line_base")
        line_base: "OrderedDict[str, LineBase]" = OrderedDict()
        if line_table is not None:
            for row in line_table.rows():
                line = _text(line_table.val(row, "line"))
                if not line:
                    continue
                entry = LineBase(
                    line=line,
                    trp=_number(line_table.val(row, "trp"), 0.71),
                    hours_per_shift=_number(line_table.val(row, "hours_per_shift"), 8.0),
                    shifts_per_day=_int(line_table.val(row, "shifts_per_day"), 3),
                    source="产线基础数据",
                )
                existing = line_base.get(line)
                if existing is not None and (
                    abs(existing.trp - entry.trp) > 1e-9
                    or abs(existing.hours_per_shift - entry.hours_per_shift) > 1e-9
                    or existing.shifts_per_day != entry.shifts_per_day
                ):
                    warnings.append(
                        f"产线基础数据: 产线 {line} 存在相互冲突的多行，已取最后一行"
                        f"（TRP={entry.trp}, 工时={entry.hours_per_shift}, 班次={entry.shifts_per_day}）"
                    )
                line_base[line] = entry

        # ---- Tooling molds ---------------------------------------------
        tooling = _table(wb, "tooling")
        tooling_molds: List[ToolingMold] = []
        tooling_ids = set()
        tooling_by_part: Dict[str, str] = {}
        if tooling is not None:
            for row in tooling.rows(site_filter):
                mold_id = _text(tooling.val(row, "mold_id"))
                if not mold_id:
                    continue
                parts = [p.strip() for p in _text(tooling.val(row, "applicable_parts")).split(",") if p.strip()]
                lines = [l.strip() for l in _text(tooling.val(row, "applicable_lines")).split(",") if l.strip()]
                tooling_molds.append(
                    ToolingMold(
                        site=_text(tooling.val(row, "site")) or site_filter,
                        mold_id=mold_id,
                        mold_name=_text(tooling.val(row, "mold_name")),
                        applicable_lines=lines,
                        applicable_parts=parts,
                        available_qty=_int(tooling.val(row, "available_qty"), 1) or 1,
                        changeover_time_min=_number(tooling.val(row, "changeover_time_min"), 30.0),
                        max_life_strokes=_int(tooling.val(row, "max_life_strokes"), 500000),
                        current_strokes=_int(tooling.val(row, "current_strokes"), 0),
                        maintenance_cycle_strokes=_int(tooling.val(row, "maintenance_cycle_strokes"), 50000),
                        status=_text(tooling.val(row, "status")) or "可用 (Available)",
                    )
                )
                tooling_ids.add(mold_id)
                for part in parts:
                    tooling_by_part.setdefault(part, mold_id)

        mold_mismatch: List[str] = []

        def resolve_mold(part_no: str, explicit) -> Optional[str]:
            explicit_id = _text(explicit)
            if explicit_id and explicit_id in tooling_ids:
                return explicit_id
            derived = tooling_by_part.get(part_no)
            if derived:
                if explicit_id:
                    mold_mismatch.append(f"{part_no}→{explicit_id}")
                return derived
            if explicit_id:
                mold_mismatch.append(f"{part_no}→{explicit_id}(无适用模具)")
                return explicit_id
            return None

        # ---- Product parameters ----------------------------------------
        def load_params(table: Optional[_Table], spec: SheetSpec, *, semi: bool) -> Dict[str, ProductParam]:
            params: Dict[str, ProductParam] = {}
            if table is None:
                return params
            default_ct = 25.0 if semi else 40.0
            default_trp = 0.85 if semi else 0.71
            default_mpq = 100.0 if semi else 1.0
            for row in table.rows(site_filter):
                pn = _text(table.val(row, "part_no"))
                if not pn:
                    continue
                explicit_line = _text(table.val(row, "line"))
                explicit_trp = _number(table.val(row, "trp"), 0.0)
                if layout == "v2":
                    # The gray 产线/效率 TRP columns are gone: line comes from
                    # Routing, TRP from 产线基础数据.
                    line = routing_line.get(pn) or explicit_line
                    base = line_base.get(line)
                    trp = (base.trp if base else 0.0) or explicit_trp or default_trp
                else:
                    line = explicit_line
                    base = line_base.get(line)
                    trp = explicit_trp or (base.trp if base else default_trp)
                if not line:
                    warnings.append(f"{pn}: 无法确定生产产线（Routing 与参数表均未给出）")
                if layout == "v2":
                    # 产线基础数据 is the single source for shift geometry.
                    hours = (base.hours_per_shift if base else 0.0) or _number(
                        table.val(row, "hours_per_shift"), 0.0
                    ) or (8.0 if site_filter == "WL" else 12.0)
                    shifts = (base.shifts_per_day if base else 0) or _int(
                        table.val(row, "shifts_per_day"), 0
                    ) or (3 if site_filter == "WL" else 2)
                else:
                    hours = _number(table.val(row, "hours_per_shift"), 0.0) or (
                        base.hours_per_shift if base else (8.0 if site_filter == "WL" else 12.0)
                    )
                    shifts = _int(table.val(row, "shifts_per_day"), 0) or (
                        base.shifts_per_day if base else (3 if site_filter == "WL" else 2)
                    )
                ct = _number(table.val(row, "ct_seconds"), 0.0) or routing_ct.get(pn, default_ct)
                mpq = _number(table.val(row, "mpq"), default_mpq) or default_mpq
                moq = _number(table.val(row, "moq"), 0.0) or mpq
                output = _number(table.val(row, "output_per_shift"), 0.0) or (
                    hours * 3600.0 / ct * trp if ct > 0 else 0.0
                )
                target_stock = _number(table.val(row, "target_stock"), 0.0)
                if not target_stock and layout == "v2":
                    # FG 目标库存 was removed; the safety stock of the inventory
                    # policy is the replacement. Legacy keeps its own column.
                    target_stock = safety_stock.get(pn, 0.0)
                params[pn] = ProductParam(
                    site=site_filter,
                    line=line,
                    part_no=pn,
                    description=_text(table.val(row, "description")),
                    ct_seconds=ct,
                    trp=trp,
                    mpq=mpq,
                    output_per_shift=output,
                    hours_per_shift=hours,
                    shifts_per_day=shifts,
                    target_stock=0.0 if semi else target_stock,
                    target_stock_days=_number(table.val(row, "target_stock_days")),
                    lead_time_days=_number(table.val(row, "lead_time_days")),
                    scrap_rate=_number(table.val(row, "scrap_rate")),
                    headcount=_number(table.val(row, "headcount")) or routing_headcount.get(pn, 0.0),
                    max_stock=_number(table.val(row, "max_stock")),
                    moq=moq,
                    mold_id=resolve_mold(pn, table.val(row, "mold_id")),
                    special_shift=_text(table.val(row, "special_shift")),
                    simultaneous_production=_text(table.val(row, "simultaneous_production")),
                    product_family=_text(table.val(row, "product_family")),
                )
            return params

        fg_params = load_params(_table(wb, "parameters_fg"), PARAMETERS_FG, semi=False)
        sf_params = load_params(_table(wb, "parameters_sfn"), PARAMETERS_SFN, semi=True)

        if mold_mismatch:
            warnings.append(
                "FG/SFN『模具编号』与 Tooling-Mold 主数据不一致，已按工装模具主数据回退："
                + "、".join(mold_mismatch)
            )

        # SF→FG 前置期由『工序提前期-提前准备时间』+ 转运批量推导，见 C4 接线；
        # 这里不再为 lead_time_days=0 告警（该字段已是旧版兼容列）。

        # ---- BOM --------------------------------------------------------
        bom: Dict[str, List[Tuple[str, float]]] = {}
        bom_table = _table(wb, "bom")
        if bom_table is not None:
            for row in bom_table.rows(site_filter):
                fg = _text(bom_table.val(row, "fg_part_no"))
                sf = _text(bom_table.val(row, "sf_part_no"))
                if fg and sf:
                    bom.setdefault(fg, []).append((sf, _number(bom_table.val(row, "ratio"), 1.0) or 1.0))

        # ---- Initial stock ---------------------------------------------
        initial_stock: Dict[str, float] = {}
        stock_table = _table(wb, "stock")
        if stock_table is not None:
            for row in stock_table.rows(site_filter):
                pn = _text(stock_table.val(row, "part_no"))
                if pn:
                    initial_stock[pn] = _number(stock_table.val(row, "quantity"))

        # ---- Customer demand (date axis) --------------------------------
        orders: List[CustomerOrder] = []
        planning_dates: List[str] = []
        demand_table = _table(wb, "demand")
        if demand_table is not None:
            ws = demand_table.ws
            header_row = _demand_header_row(ws)
            if header_row:
                headers = {
                    norm(ws.cell(header_row, c).value): c
                    for c in range(1, ws.max_column + 1)
                    if ws.cell(header_row, c).value is not None
                }
                site_col = headers.get("站点") or headers.get("site")
                pn_col = headers.get("料号(pn)") or headers.get("pn") or headers.get("partno.")
                desc_col = headers.get("品名描述") or headers.get("pndes")
                due_hour = 15 if site_filter == "WL" else 20
                date_columns = _demand_dates(ws, header_row)
                planning_dates = [date_text for _, date_text in date_columns]
                for row in range(header_row + 1, ws.max_row + 1):
                    row_site = _text(ws.cell(row, site_col).value) if site_col else ""
                    if row_site and row_site != site_filter:
                        continue
                    pn = _text(ws.cell(row, pn_col).value) if pn_col else ""
                    if not pn:
                        continue
                    description = _text(ws.cell(row, desc_col).value) if desc_col else ""
                    for col, date_text in date_columns:
                        qty = _number(ws.cell(row, col).value)
                        if qty <= 0:
                            continue
                        month, day = (int(p) for p in date_text.split("/"))
                        orders.append(
                            CustomerOrder(
                                order_id=f"ORD_{site_filter}_{pn}_{month:02d}{day:02d}",
                                part_no=pn,
                                description=description,
                                due_date=date_text,
                                due_datetime=datetime(2026, month, day, due_hour, 0),
                                demand_quantity=qty,
                            )
                        )

        # ---- Preventive maintenance -------------------------------------
        pm_table = _table(wb, "pm")
        pm_schedules: List[PreventiveMaintenance] = []
        if pm_table is not None:
            for row in pm_table.rows(site_filter):
                line = _text(pm_table.val(row, "line"))
                start_date = _date_str(pm_table.val(row, "start_date"))
                if not line or not start_date:
                    continue
                downtime = _number(pm_table.val(row, "downtime_hours"), 8.0)
                start_shift = _shift_from_time(pm_table.val(row, "start_time"), site_filter, "早班")
                end_date = _date_str(pm_table.val(row, "end_date")) or start_date
                end_shift = _shift_from_time(pm_table.val(row, "end_time"), site_filter, start_shift)
                if layout == "v2" and not _text(pm_table.val(row, "end_time")):
                    # The v2 template only carries dates + downtime; derive the
                    # blocked shift range so a 8h PM blocks one shift and a 48h
                    # PM blocks six, instead of blocking whole days.
                    base = line_base.get(line)
                    shift_hours = base.hours_per_shift if base else (8.0 if site_filter == "WL" else 12.0)
                    shift_hours = shift_hours or 8.0
                    count = max(1, int(math.ceil(downtime / shift_hours))) if downtime > 0 else 1
                    end_date, end_shift = _advance_shift(start_date, start_shift, count, site_filter)
                plan_no = _text(pm_table.val(row, "pm_plan_no")) or f"PM-{site_filter}-{line}-{start_date}"
                pm_schedules.append(
                    PreventiveMaintenance(
                        site=_text(pm_table.val(row, "site")) or site_filter,
                        pm_plan_no=plan_no,
                        line=line,
                        equipment_name=_text(pm_table.val(row, "equipment_name")),
                        pm_type=_text(pm_table.val(row, "pm_item")),
                        start_date=start_date,
                        start_shift=start_shift,
                        end_date=end_date,
                        end_shift=end_shift,
                        downtime_hours=downtime,
                        capacity_rate=_number(pm_table.val(row, "capacity_rate"), 0.0),
                        handling_strategy=_text(pm_table.val(row, "handling_strategy"))
                        or "避让顺延 (Shift_Delay)",
                        responsible_team=_text(pm_table.val(row, "responsible_team")),
                        status=_text(pm_table.val(row, "status")) or "已排定 (Planned)",
                        start_time=_text(pm_table.val(row, "start_time")),
                        end_time=_text(pm_table.val(row, "end_time")),
                    )
                )

        # ---- Material arrivals ------------------------------------------
        material_table = _table(wb, "material_arrival")
        material_arrivals: List[MaterialArrival] = []
        if material_table is not None:
            for row in material_table.rows(site_filter):
                part_no = _text(material_table.val(row, "part_no"))
                if not part_no:
                    continue
                material_arrivals.append(
                    MaterialArrival(
                        site=_text(material_table.val(row, "site")) or site_filter,
                        po_number=f"PO-{part_no}-{row}",
                        supplier=_text(material_table.val(row, "supplier")),
                        part_no=part_no,
                        description=_text(material_table.val(row, "description")),
                        eta_date=_date_str(material_table.val(row, "eta_date")),
                        eta_shift=_text(material_table.val(row, "eta_shift")),
                        eta_time=_text(material_table.val(row, "eta_time")),
                        quantity=_number(material_table.val(row, "quantity")),
                        qc_release_hours=_number(material_table.val(row, "qc_release_hours"), 2.0),
                        associated_part=_text(material_table.val(row, "associated_part")) or part_no,
                        status=_text(material_table.val(row, "status")) or "质检合格 (Released)",
                    )
                )

        # ---- Customer tiers --------------------------------------------
        customer_table = _table(wb, "customer")
        customer_tiers: List[CustomerTier] = []
        if customer_table is not None:
            for row in customer_table.rows(site_filter):
                code = _text(customer_table.val(row, "customer_code"))
                if not code:
                    continue
                customer_tiers.append(
                    CustomerTier(
                        site=_text(customer_table.val(row, "site")) or site_filter,
                        customer_code=code,
                        customer_name=_text(customer_table.val(row, "customer_name")),
                        tier=_text(customer_table.val(row, "tier")) or "TIER_A",
                        priority_weight=_int(customer_table.val(row, "priority_weight"), 3),
                        otd_target_rate=_number(customer_table.val(row, "otd_target_rate"), 98.0),
                        delivery_window=_text(customer_table.val(row, "delivery_window")) or "0天严格保供",
                        subcontract_allowed=_text(customer_table.val(row, "subcontract_allowed"))
                        or "协商外协 (Negotiable)",
                        associated_products=[
                            p.strip()
                            for p in _text(customer_table.val(row, "associated_products")).split(",")
                            if p.strip()
                        ],
                    )
                )

        # Derive a line-base snapshot for the legacy workbook as well, so the UI
        # always has one line-level table to show.
        for param in list(fg_params.values()) + list(sf_params.values()):
            if param.line and param.line not in line_base:
                line_base[param.line] = LineBase(
                    line=param.line,
                    trp=param.trp,
                    hours_per_shift=param.hours_per_shift,
                    shifts_per_day=param.shifts_per_day,
                    source="参数表派生",
                )

        return ProductionInputSnapshot(
            fg_params=fg_params,
            sf_params=sf_params,
            bom=bom,
            initial_stock=initial_stock,
            orders=orders,
            planning_dates=planning_dates,
            tooling_molds=tooling_molds,
            pm_schedules=pm_schedules,
            material_arrivals=material_arrivals,
            customer_tiers=customer_tiers,
            line_base=list(line_base.values()),
            warnings=warnings,
            source_path=excel_path,
            layout=layout,
        )
    finally:
        wb.close()


def read_master_rows(excel_path: str, site: Optional[str] = None) -> Dict[str, List[dict]]:
    """Read every input sheet as canonical-keyed dicts, for the Tab-4 preview."""
    wb = openpyxl.load_workbook(excel_path, data_only=True)
    try:
        out: Dict[str, List[dict]] = {}
        for key in SHEET_BY_KEY:
            if key == "instructions":
                continue
            spec = SHEET_BY_KEY[key]
            ws = resolve_sheet(wb, spec)
            if ws is None:
                out[key] = []
                continue
            table = _Table(ws, spec)
            rows: List[dict] = []
            for row in table.rows(site):
                if table.is_empty_row(row):
                    continue
                item = {}
                for column in spec.columns:
                    value = table.val(row, column.key)
                    if value is None or _text(value) == "":
                        item[column.key] = None
                        continue
                    if column.kind == "bool":
                        item[column.key] = _bool(value)
                    elif column.kind == "int":
                        item[column.key] = _int(value)
                    elif column.kind == "number":
                        item[column.key] = _number(value)
                    elif isinstance(value, datetime):
                        item[column.key] = value.isoformat(sep=" ")
                    elif isinstance(value, date):
                        item[column.key] = value.isoformat()
                    else:
                        item[column.key] = value
                rows.append(item)
                if len(rows) >= 5000:
                    break
            out[key] = rows
        return out
    finally:
        wb.close()
