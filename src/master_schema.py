"""Canonical master-data schema for the APS data-source workbook.

The 2026-10 revision of the planner workbook (``APS_DataSource_Template.xlsx``)
uses 15 Chinese-headed sheets instead of the legacy 25 English-headed sheets.
This module is the single source of truth for:

* sheet titles (new canonical title + legacy aliases),
* column headers (new canonical header + legacy aliases),
* which columns belong to the new framework.

Columns marked ``deprecated=True`` are the gray "delete" columns of the
template.  They are ignored by the new framework (the planner removes them by
hand) and are only kept as aliases so the legacy 25-sheet workbook still loads.

Both the Excel loader (``src.excel_loader``) and the upload validator
(``src.server``) read the workbook through this schema, so a planner may
rename a sheet prefix or reorder columns without breaking the import.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import openpyxl


# --------------------------------------------------------------------------
# Normalisation helpers
# --------------------------------------------------------------------------
_WS_RE = re.compile(r"\s+")


def norm(value) -> str:
    """Normalise a sheet title or header for tolerant matching."""
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = (
        text.replace("（", "(")
        .replace("）", ")")
        .replace("：", ":")
        .replace("，", ",")
        .replace("\u3000", " ")
        .replace("\u00a0", " ")
    )
    return _WS_RE.sub("", text)


def extent_of(value) -> int:
    """Return a worksheet extent (``max_row`` / ``max_column``) as a usable int.

    openpyxl only knows a sheet's extent from the workbook's ``<dimension>``
    element when the sheet is opened read-only.  Workbooks produced by WPS
    Office, several JS exporters and some ERP exports omit that element, so
    ``ws.max_row`` / ``ws.max_column`` are ``None`` there.  Scanning code must
    never feed those values straight into ``min``/``range``: that fails with the
    opaque ``'<' not supported between instances of 'int' and 'NoneType'``.
    ``0`` means "unknown" to every caller.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value > 0 else 0


@dataclass(frozen=True)
class Column:
    """One logical column of a master-data sheet."""

    key: str
    header: str
    aliases: Tuple[str, ...] = ()
    required: bool = False
    kind: str = "text"  # text | number | int | date | bool
    note: str = ""
    deprecated: bool = False  # gray column: not part of the new framework

    def candidates(self) -> Tuple[str, ...]:
        return (self.header,) + tuple(a for a in self.aliases if a and a != self.header)


@dataclass(frozen=True)
class SheetSpec:
    """One logical sheet of the master-data workbook."""

    key: str
    title: str
    columns: Tuple[Column, ...]
    legacy_titles: Tuple[str, ...] = ()
    required: bool = True
    date_axis: bool = False
    description: str = ""

    def match_terms(self) -> Tuple[str, ...]:
        return (self.title,) + tuple(self.legacy_titles)

    def active_columns(self) -> Tuple[Column, ...]:
        """Columns that belong to the new framework (gray ones excluded)."""
        return tuple(c for c in self.columns if not c.deprecated)


# --------------------------------------------------------------------------
# The 15 sheets
# --------------------------------------------------------------------------
INSTRUCTIONS = SheetSpec(
    key="instructions",
    title="填写说明 Instructions",
    legacy_titles=("Instructions",),
    required=False,
    columns=(),
    description="填写说明，不是排产输入",
)

DEMAND = SheetSpec(
    key="demand",
    title="客户需求订单清单 Customer Demand",
    legacy_titles=("Customer Demand", "Customer-Demand"),
    date_axis=True,
    columns=(
        Column("site", "站点", ("Site",), required=True),
        Column("part_no", "料号 (PN)", ("PN", "Part No.", "Part No"), required=True),
        Column("description", "品名描述", ("PN Des", "Description")),
    ),
    description="日期轴需求宽表；日期列动态识别",
)

STOCK = SheetSpec(
    key="stock",
    title="期初可用库存 Initial Stock",
    legacy_titles=("Initial Stock", "Initial-Stock"),
    columns=(
        Column("site", "站点", ("Site",), required=True),
        Column("material_type", "物料类型", ("Material Type",)),
        Column("part_no", "物料料号 (PN)", ("PN", "Part No.", "Part No"), required=True),
        Column("description", "品名描述", ("PN Des", "Description")),
        Column("quantity", "期初库存 (件)",
               ("Initial Stock", "Initial Stock(10/13)", "Initial Stock (pcs)"),
               required=True, kind="number"),
    ),
)

MATERIAL_ARRIVAL = SheetSpec(
    key="material_arrival",
    title="关键物料到货 Material-Arrival-ETA",
    legacy_titles=("Material-Arrival-ETA", "Material-Arrivals"),
    columns=(
        Column("site", "站点", ("Site",), required=True),
        Column("supplier", "合格供应商", ("Supplier",), required=True),
        Column("part_no", "物料料号 (PN)", ("Part No.", "Part No", "PO Number"), required=True),
        Column("description", "物料描述", ("Description",)),
        Column("eta_date", "预计入库日期", ("ETA Date",), required=True, kind="date"),
        Column("eta_shift", "预计入库班次", ("ETA Shift",)),
        Column("eta_time", "预计入库时间", (), note="新增列"),
        Column("quantity", "送达数量 (件)", ("Quantity",), required=True, kind="number"),
        # gray columns of the new template (planner deletes them)
        Column("qc_release_hours", "放行工时 (小时)", ("QC Release Hours",), kind="number", deprecated=True),
        Column("associated_part", "关联排产零件", ("Associated Part",), deprecated=True),
        Column("status", "状态", ("Status",), deprecated=True),
    ),
)

PARAMETERS_FG = SheetSpec(
    key="parameters_fg",
    title="产线与成品参数 Parameters-FG",
    legacy_titles=("Parameters-FG", "Parameters-FG "),
    columns=(
        Column("site", "站点", ("Site",), required=True),
        Column("line", "产线", ("Production Line",), deprecated=True,
               note="灰色列，已确认删除；产线由 Routing 主候选线给出"),
        Column("trp", "效率 TRP", ("Line TRP",), kind="number", deprecated=True,
               note="灰色列，已确认删除；TRP 由产线基础数据给出"),
        Column("part_no", "成品料号 (PN)", ("Part No.", "Part No"), required=True),
        Column("description", "品名描述", ("Description",), required=True),
        Column("mpq", "最小包装量 MPQ", ("Mini Production QTY(MPQ)", "MPQ"), required=True, kind="number"),
        Column("hours_per_shift", "单班工时 (小时)", ("No. of hours/shift",), kind="number"),
        Column("shifts_per_day", "每天班次数", ("No. of shift/day",), kind="int"),
        Column("moq", "最小生产量 MOQ", ("MOQ (Minimum Order Qty)", "MOQ"), kind="number"),
        Column("product_family", "产品族名", ("Product Family",), note="新增列"),
        Column("mold_id", "模具编号 (Mold ID)", ("Mold ID",), note="新增列"),
        Column("special_shift", "特殊班次", ("Special Shift",), note="新增列"),
        Column("simultaneous_production", "同时生产", ("Simultaneous Production",), note="新增列"),
        # legacy-only columns (not in the new template)
        Column("ct_seconds", "单件节拍 CT (秒)", ("Cycle time",), kind="number", deprecated=True,
               note="旧版列；新版迁至 Routing-Machines"),
        Column("target_stock", "目标库存", ("Target Stock",), kind="number", deprecated=True,
               note="旧版列；新版迁至 Inventory-Policy"),
        Column("headcount", "人数", ("Headcount", "Head count"), kind="number", deprecated=True,
               note="旧版列；新版迁至 Routing-Machines"),
        Column("output_per_shift", "单班定额产能 (件)", ("Output per shift",), kind="number", deprecated=True,
               note="旧版列；新版自动计算"),
    ),
)

PARAMETERS_SFN = SheetSpec(
    key="parameters_sfn",
    title="半成品制造参数 Parameters-SFN",
    legacy_titles=("Parameters-SFN", "Parameters-SFN "),
    columns=(
        Column("site", "站点", ("Site",), required=True),
        Column("line", "产线", ("Production Line",), deprecated=True,
               note="灰色列，已确认删除；产线由 Routing 主候选线给出"),
        Column("trp", "效率 TRP", ("Line TRP",), kind="number", deprecated=True,
               note="灰色列，已确认删除；TRP 由产线基础数据给出"),
        Column("part_no", "半成品料号 (PN)", ("Part No.", "Part No"), required=True),
        Column("description", "品名描述", ("Description",), required=True),
        Column("mpq", "最小起投批量 MPQ", ("Mini Production QTY(MPQ)", "MPQ"), required=True, kind="number"),
        Column("scrap_rate", "报废率 (小数)", ("Scrap rate(%)", "Scrap rate"), required=True, kind="number"),
        Column("max_stock", "最大库存 (件)", ("Max stock", "Max Stock"), kind="number"),
        Column("moq", "最小起订量 MOQ", ("MOQ (Minimum Order Qty)", "MOQ"), kind="number"),
        Column("product_family", "产品族名", ("Product Family",), note="新增列"),
        Column("mold_id", "模具编号 (Mold ID)", ("Mold ID",), note="新增列"),
        Column("special_shift", "特殊班次", ("Special Shift",), note="新增列"),
        Column("simultaneous_production", "同时生产", ("Simultaneous Production",), note="新增列"),
        # legacy-only columns
        Column("ct_seconds", "单件节拍 CT (秒)", ("Cycle time(S)", "Cycle time"), kind="number", deprecated=True,
               note="旧版列；新版迁至 Routing-Machines"),
        Column("target_stock_days", "安全库存天数", ("Target Stock(days)", "Target Stock (days)"),
               kind="number", deprecated=True, note="旧版列"),
        Column("lead_time_days", "生产提前期 (days)", ("生产提前期(days）", "生产提前期(days)"),
               kind="number", deprecated=True,
               note="旧版列；新版由『工序提前期-提前准备时间』+ 转运批量推导"),
        Column("hours_per_shift", "单班工时 (小时)", ("No. of hours/shift",), kind="number", deprecated=True,
               note="旧版列；新版迁至产线基础数据"),
        Column("shifts_per_day", "每天班次数", ("No. of shift/day",), kind="int", deprecated=True,
               note="旧版列；新版迁至产线基础数据"),
        Column("headcount", "人数", ("Head count", "Headcount"), kind="number", deprecated=True,
               note="旧版列；新版迁至 Routing-Machines"),
        Column("output_per_shift", "单班定额产能 (件)", ("Output per shift",), kind="number", deprecated=True,
               note="旧版列"),
    ),
)

BOM = SheetSpec(
    key="bom",
    title="产品装配清单 BOM List",
    legacy_titles=("BOM List", "BOM-List"),
    columns=(
        Column("site", "站点", ("Site",), required=True),
        Column("fg_part_no", "母件：成品料号 (FG PN)", ("SAP总成料号", "FG Part No"), required=True),
        Column("fg_description", "母件品名描述", ("品名描述",)),
        Column("sf_part_no", "子件：半成品料号 (SF PN)", ("半成品料号", "SF Part No"), required=True),
        Column("sf_description", "子件品名描述", ("半成品品名",)),
        Column("ratio", "装配单耗比例", ("BOM单台用量", "BOM比例", "Ratio"), required=True, kind="number"),
    ),
)

ROUTING = SheetSpec(
    key="routing",
    title="工艺路线与候选机台 Routing-Machines",
    legacy_titles=("Routing-Machines",),
    required=False,
    columns=(
        Column("site", "站点", ("Site",), required=True),
        Column("part_no", "料号 (PN)", ("Part No.", "Part No"), required=True),
        Column("stage", "阶段", ("Stage",), required=True),
        Column("op_seq", "工序序号", ("Operation Seq",), kind="int"),
        Column("op_name", "工序名称", ("Operation Name",)),
        Column("primary_line", "主候选线", ("Primary Line",), required=True),
        Column("certified", "认证", ("Certified",), kind="bool"),
        Column("ct_seconds", "CT (秒)", ("CT Seconds",), required=True, kind="number"),
        Column("setup_min", "换型时间 (分钟)", ("Setup Min",), kind="number"),
        Column("priority", "优先级", ("Priority",), kind="int"),
        Column("effective_from", "生效日期", ("Effective From",), kind="date"),
        Column("effective_to", "失效日期", ("Effective To",), kind="date"),
        Column("output_per_shift", "单班定额产能 (件)", (), kind="number", note="新增列(自动计算)"),
        Column("headcount", "人数", (), kind="number", note="新增列"),
        Column("product_family", "产品族名", (), note="新增列"),
        # legacy-only columns (removed from the template in 2026-11)
        Column("candidate_line", "备选候选线", ("Candidate Line",), deprecated=True,
               note="旧版列；2026-11 已从模板删除"),
        Column("machine_id", "机台", ("Machine ID",), deprecated=True,
               note="旧版列；2026-11 已从模板删除"),
        Column("trp", "效率 TRP", ("TRP",), kind="number", deprecated=True,
               note="旧版列；2026-11 已从模板删除；TRP 由『产线基础数据』给出"),
    ),
)

SETUP = SheetSpec(
    key="setup_matrix",
    title="换模矩阵 Setup-Matrix",
    legacy_titles=("Setup-Matrix",),
    required=False,
    columns=(
        Column("site", "站点", ("Site",), required=True),
        Column("line", "产线", ("Line",), required=True),
        Column("from_mold", "起始模具", ("From Mold",), required=True),
        Column("to_mold", "目标模具", ("To Mold",), required=True),
        Column("setup_minutes", "换模工时 (分钟)", ("Setup Minutes",), required=True, kind="number"),
        Column("technicians", "所需技术员数", ("Technicians Required",), kind="int"),
        Column("cleaning_required", "是否需要清洁", ("Cleaning Required",), kind="bool"),
        Column("effective_from", "生效日期", ("Effective From",), kind="date"),
        Column("from_family", "起始产品族", ("From Family",), deprecated=True, note="旧版列(新版删除)"),
        Column("to_family", "目标产品族", ("To Family",), deprecated=True, note="旧版列(新版删除)"),
    ),
)

INVENTORY = SheetSpec(
    key="inventory_policy",
    title="库存策略 Inventory-Policy",
    legacy_titles=("Inventory-Policy",),
    required=False,
    columns=(
        Column("site", "站点", ("Site",), required=True),
        Column("part_no", "料号 (PN)", ("Part No.", "Part No"), required=True),
        Column("period_type", "周期类型", ("Period Type",), required=True),
        Column("from_day", "起始日 (Day)", ("From Day",), kind="int"),
        Column("to_day", "结束日 (Day)", ("To Day",), kind="int"),
        Column("target_days", "目标库存天数", ("Target Days",), kind="number"),
        Column("max_days", "最大库存天数", ("Max Days",), kind="number"),
        Column("safety_stock", "安全库存 (件)", ("Safety Stock",), kind="number"),
        Column("max_stock", "最大库存 (件)", ("Max Stock",), kind="number"),
        Column("effective_from", "生效日期", ("Effective From",), kind="date"),
    ),
)

LEADTIME = SheetSpec(
    key="operation_leadtime",
    title="工序提前期 Operation-LeadTime",
    legacy_titles=("Operation-LeadTime",),
    required=False,
    columns=(
        Column("site", "站点", ("Site",), required=True),
        Column("part_no", "料号 (PN)", ("Part No.", "Part No"), required=True),
        Column("stage", "阶段", ("Stage",), required=True),
        Column("op_seq", "工序序号", ("Operation Seq",), kind="int"),
        Column("op_name", "工序名称", ("Operation Name",)),
        Column("prep_minutes", "提前准备时间（分钟）", ("Setup Min",), kind="number", note="新增列"),
        Column("transfer_batch", "转运批量 (件)", ("Transfer Batch Qty", "Transfer Batch"),
               kind="number", required=True),
        Column("container", "容器", ("Container",)),
        Column("run_ct_seconds", "运行节拍 CT (秒)", ("Run CT Seconds",), kind="number", deprecated=True),
        Column("queue_min", "排队时间 (分钟)", ("Queue Min",), kind="number", deprecated=True),
        Column("inspection_min", "检验时间 (分钟)", ("Inspection Min",), kind="number", deprecated=True),
        Column("move_min", "搬运时间 (分钟)", ("Move Min",), kind="number", deprecated=True),
    ),
)

TOOLING = SheetSpec(
    key="tooling",
    title="工装模具约束 Tooling-Mold",
    legacy_titles=("Tooling-Mold", "Tooling-Molds"),
    columns=(
        Column("site", "站点", ("Site",), required=True),
        Column("mold_id", "模具编号 (Mold ID)", ("Mold ID",), required=True),
        Column("mold_name", "工装模具名称", ("Mold Name",), required=True),
        Column("applicable_lines", "适用产线/设备", ("Applicable Lines",), required=True),
        Column("applicable_parts", "适用零件料号", ("Applicable Part Numbers",), required=True),
        Column("available_qty", "可用套数", ("Available Qty",), kind="int"),
        Column("changeover_time_min", "换模工时 (分钟)", ("Changeover Time (min)",), kind="number"),
        Column("max_life_strokes", "最大寿命冲次", ("Max Life Strokes",), kind="int", deprecated=True),
        Column("current_strokes", "当前冲次", ("Current Strokes",), kind="int", deprecated=True),
        Column("maintenance_cycle_strokes", "保养周期冲次",
               ("Maintenance Cycle (Strokes)",), kind="int", deprecated=True),
        Column("status", "状态", ("Status",), deprecated=True),
    ),
)

LINE_BASE = SheetSpec(
    key="line_base",
    title="产线基础数据",
    legacy_titles=("Line-Base", "LineBase"),
    required=False,
    columns=(
        Column("line", "产线", ("Line", "Production Line"), required=True),
        Column("trp", "效率 TRP", ("Line TRP", "TRP"), required=True, kind="number"),
        Column("hours_per_shift", "单班工时 (小时)", ("No. of hours/shift",), required=True, kind="number"),
        Column("shifts_per_day", "每天班次数", ("No. of shift/day",), required=True, kind="int"),
    ),
    description="2026-10 新增：产线级 TRP / 工时 / 班次，从 FG/SFN 参数表拆出",
)

PREVENTIVE = SheetSpec(
    key="pm",
    title="设备预防维护 Preventive-Maintenance",
    legacy_titles=("Preventive-Maintenance", "PM-Schedules"),
    columns=(
        Column("site", "站点", ("Site",), required=True),
        Column("line", "产线", ("Production Line",), required=True),
        Column("pm_item", "维保项目试制内容", ("PM Type",), required=True,
               note="旧版为『维保等级/类型』；表头“试制”疑似笔误"),
        Column("start_date", "开始日期", ("Start Date",), required=True, kind="date"),
        Column("start_time", "开始时间", ("Start Shift",), note="旧版为班次"),
        Column("end_date", "结束日期", ("End Date",), required=True, kind="date"),
        Column("end_time", "结束时间", ("End Shift",), note="旧版为班次"),
        Column("downtime_hours", "停机工时 (小时)", ("Downtime Hours",), required=True, kind="number"),
        Column("capacity_rate", "期间产能 (%)", ("Capacity Rate (%)", "Capacity Rate"), kind="number"),
        Column("handling_strategy", "工单处置策略", ("Handling Strategy",), required=True),
        Column("pm_plan_no", "PM计划号", ("PM Plan No",), deprecated=True),
        Column("equipment_name", "设备名称", ("Equipment Name",), deprecated=True),
        Column("responsible_team", "责任班组", ("Responsible Team",), deprecated=True),
        Column("status", "状态", ("Status",), deprecated=True),
    ),
)

CUSTOMER = SheetSpec(
    key="customer",
    title="客户战略评级 Customer-Priority",
    legacy_titles=("Customer-Priority", "Customer-Tiers"),
    columns=(
        Column("site", "站点", ("Site",), required=True),
        Column("customer_code", "客户代码 (Code)", ("Customer Code",), required=True),
        Column("customer_name", "客户全称", ("Customer Name",), required=True),
        Column("tier", "战略评级 (Tier)", ("Strategic Tier",), required=True),
        Column("priority_weight", "保供加权系数", ("Priority Weight",), kind="number"),
        Column("otd_target_rate", "OTD 准时交付目标 (%)", ("OTD Target Rate (%)", "OTD Target Rate"),
               kind="number"),
        Column("delivery_window", "交期浮动容差 (天)", ("Delivery Window (Days)",)),
        Column("subcontract_allowed", "产能紧张外协策略", ("Subcontract Allowed",)),
        Column("associated_products", "代表配装成品", ("Associated Products",)),
    ),
)


SHEETS: Tuple[SheetSpec, ...] = (
    INSTRUCTIONS,
    DEMAND,
    STOCK,
    MATERIAL_ARRIVAL,
    PARAMETERS_FG,
    PARAMETERS_SFN,
    BOM,
    ROUTING,
    SETUP,
    INVENTORY,
    LEADTIME,
    TOOLING,
    LINE_BASE,
    PREVENTIVE,
    CUSTOMER,
)

SHEET_BY_KEY: Dict[str, SheetSpec] = {s.key: s for s in SHEETS}

#: Sheets that carry production inputs (everything except the instructions page).
INPUT_SHEETS: Tuple[SheetSpec, ...] = tuple(s for s in SHEETS if s.key != "instructions")

#: Sheets every upload must contain (optional ones may be omitted).
REQUIRED_SHEET_KEYS: Tuple[str, ...] = (
    "demand",
    "stock",
    "material_arrival",
    "parameters_fg",
    "parameters_sfn",
    "bom",
    "tooling",
    "pm",
    "customer",
)

#: New-template sheet count (instructions + 14 input tables).
TEMPLATE_SHEET_COUNT = len(SHEETS)


def resolve_sheet_key(title: str) -> Optional[str]:
    """Map a worksheet title to a canonical sheet key (tolerates prefixes)."""
    if title is None:
        return None
    raw = str(title)
    n = norm(raw)
    if not n:
        return None
    for spec in SHEETS:
        for term in spec.match_terms():
            t = norm(term)
            if n == t or n.endswith(t) or t.endswith(n):
                return spec.key
    return None


def resolve_sheet(wb, spec: SheetSpec):
    """Return the worksheet matching ``spec`` or ``None``."""
    for name in wb.sheetnames:
        if resolve_sheet_key(name) == spec.key:
            return wb[name]
    return None


def header_row_for(ws, spec: SheetSpec) -> int:
    """Row holding the column headers (some legacy sheets use a title row).

    Reads the candidate rows through ``iter_rows`` so it also works for
    read-only sheets whose dimension is unknown (see :func:`extent_of`).
    """
    if not spec.date_axis:
        return 1
    markers = {"站点", "site", "料号(pn)", "pn", "partno.", "partno"}
    for row_idx, values in enumerate(
        ws.iter_rows(min_row=1, max_row=6, max_col=8, values_only=True), start=1
    ):
        for value in values:
            if norm(value) in markers:
                return row_idx
    return 1


def header_index(ws, row: int = 1):
    """Build ``{normalised header: column index}`` for a worksheet header row.

    Only the requested row is read, so an unknown ``max_column`` (read-only
    sheet without ``<dimension>``) cannot break the scan.
    """
    mapping: Dict[str, int] = {}
    values = next(ws.iter_rows(min_row=row, max_row=row, values_only=True), ())
    for c, value in enumerate(values, start=1):
        if value is None:
            continue
        mapping.setdefault(norm(value), c)
    return mapping


def column_positions(ws, spec: SheetSpec) -> Dict[str, int]:
    """Resolve every ``Column.key`` of ``spec`` to a 1-based column index."""
    headers = header_index(ws, header_row_for(ws, spec))
    positions: Dict[str, int] = {}
    for col in spec.columns:
        for candidate in col.candidates():
            idx = headers.get(norm(candidate))
            if idx is not None:
                positions[col.key] = idx
                break
    return positions


def missing_required_columns(ws, spec: SheetSpec) -> List[str]:
    positions = column_positions(ws, spec)
    return [c.header for c in spec.columns if c.required and c.key not in positions]


def detect_layout(wb) -> str:
    """Return ``"v2"`` (2026-10 Chinese framework) or ``"legacy"``."""
    keys = {resolve_sheet_key(name) for name in wb.sheetnames}
    if "line_base" in keys:
        return "v2"
    fg = resolve_sheet(wb, PARAMETERS_FG)
    if fg is not None:
        headers = header_index(fg)
        if norm("成品料号 (PN)") in headers:
            return "v2"
    return "legacy"


@dataclass
class SchemaReport:
    layout: str
    sheets: List[str] = field(default_factory=list)
    missing_sheets: List[str] = field(default_factory=list)
    unknown_sheets: List[str] = field(default_factory=list)
    missing_columns: Dict[str, List[str]] = field(default_factory=dict)
    optional_missing: List[str] = field(default_factory=list)
    deprecated_present: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.missing_sheets and not self.missing_columns


def validate_workbook(wb) -> SchemaReport:
    """Validate a workbook against the canonical v2 (or legacy) schema.

    Column order is irrelevant; only sheet presence and required headers are
    checked.  Gray/deprecated columns are optional: they may still be present
    in an older copy of the template and are ignored either way.
    """
    layout = detect_layout(wb)
    report = SchemaReport(layout=layout, sheets=list(wb.sheetnames))

    for spec in SHEETS:
        if spec.key == "instructions":
            continue
        ws = resolve_sheet(wb, spec)
        if ws is None:
            if spec.required:
                report.missing_sheets.append(spec.title)
            else:
                report.optional_missing.append(spec.title)
            continue
        positions = column_positions(ws, spec)
        missing = [
            c.header for c in spec.columns
            if c.required and not c.deprecated and c.key not in positions
        ]
        if missing:
            report.missing_columns[spec.title] = missing
        present_gray = [
            c.header for c in spec.columns
            if c.deprecated and c.key in positions and str(ws.cell(1, positions[c.key]).value or "").strip()
        ]
        if present_gray and layout == "v2":
            report.deprecated_present[spec.title] = present_gray
    return report


def rows_as_dicts(ws, spec: SheetSpec, site: Optional[str] = None,
                  limit: int = 5000) -> List[dict]:
    """Read a sheet into canonical-keyed dicts (used by the API for preview)."""
    positions = column_positions(ws, spec)
    site_col = positions.get("site")
    out: List[dict] = []
    last_row = extent_of(getattr(ws, "max_row", None))
    for r in range(header_row_for(ws, spec) + 1, last_row + 1):
        if site_col is not None and site:
            value = ws.cell(r, site_col).value
            if str(value or "").strip() and str(value).strip() != site:
                continue
        item: Dict[str, object] = {}
        empty = True
        for col in spec.columns:
            idx = positions.get(col.key)
            value = ws.cell(r, idx).value if idx is not None else None
            if value not in (None, ""):
                empty = False
            item[col.key] = value
        if empty:
            continue
        out.append(item)
        if len(out) >= limit:
            break
    return out
