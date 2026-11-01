"""Build the planner-facing Instructions sheet for the 15-sheet template.

The template is a shared standard for WL / SY38 / future sites, so the first
sheet must explain two things explicitly:

1. what each table is for and how its rows are keyed;
2. what every field means, its unit/type and its limits.

The sheet is generated from the canonical schema, so headers can never drift
from the parser.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from src.master_schema import INPUT_SHEETS, SHEET_BY_KEY, column_positions, resolve_sheet

NAVY = "FF1E3A5F"
BLUE = "FF0B5394"
LIGHT = "FFEAF1F8"
GREY = "FFF2F2F2"
WHITE = "FFFFFFFF"
ORANGE = "FFFCE4D6"

THIN = Side(style="thin", color="FFBFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(wrap_text=True, vertical="center", horizontal="center")

TITLE_FONT = Font(name="微软雅黑", size=16, bold=True, color=NAVY)
SUB_FONT = Font(name="微软雅黑", size=10, color="FF595959")
SECTION_FONT = Font(name="微软雅黑", size=11, bold=True, color=WHITE)
HEAD_FONT = Font(name="微软雅黑", size=9, bold=True, color=WHITE)
BODY_FONT = Font(name="微软雅黑", size=9, color="FF262626")
TINY_FONT = Font(name="微软雅黑", size=8, color="FF595959")


TABLE_GUIDE: List[dict] = [
    {
        "key": "demand",
        "purpose": "客户订单需求。系统按日期轴汇总需求，扣减期初库存/安全库存后生成 FG/SF 排产任务。",
        "grain": "一个料号一行；每个日期一列（宽表）",
        "required": "必填",
        "note": "日期轴是排产共用日历，不要删除整列；新增日期向右侧追加。",
    },
    {
        "key": "stock",
        "purpose": "计划开始时的可用库存。用于净需求计算与库存投影。",
        "grain": "一个物料一行",
        "required": "必填",
        "note": "物料类型仅作展示，系统按料号自动判定 FG/SF。",
    },
    {
        "key": "material_arrival",
        "purpose": "关键物料/外购件的到货计划，用于齐套开工约束（到货前不得开工）。",
        "grain": "一条到货记录一行",
        "required": "必填",
        "note": "未填写的物料默认视为已齐套；填写后按预计入库日期+班次限制开工。",
    },
    {
        "key": "parameters_fg",
        "purpose": "成品（总装）参数：批量、班次、模具、产品族、特殊班次等。",
        "grain": "一个成品料号一行",
        "required": "必填",
        "note": "产线不在此表维护，系统从 Routing 的主候选线派生。",
    },
    {
        "key": "parameters_sfn",
        "purpose": "半成品（冲压/注塑）参数：起投批量、报废率、最大库存、模具等。",
        "grain": "一个半成品料号一行",
        "required": "必填",
        "note": "半成品前置期不在此表维护，由工序提前期的“提前准备时间”推导。",
    },
    {
        "key": "bom",
        "purpose": "成品与半成品的装配清单，用于 BOM 展开与齐套。",
        "grain": "一个父件+一个子件一行",
        "required": "必填",
        "note": "母件/子件料号必须分别存在于 FG/SFN 参数表。",
    },
    {
        "key": "routing",
        "purpose": "工艺路线、主候选线、CT、认证机台、换型时间、人数。算法依赖此表取 CT、产线和人数。",
        "grain": "一个料号×阶段×工序一行；同一料号多条产线写多行",
        "required": "必填（算法强依赖）",
        "note": "主候选线必须存在于“产线基础数据”；备选候选线/机台/TRP 为灰色废弃列。",
    },
    {
        "key": "setup_matrix",
        "purpose": "换模矩阵：产线上从某模具切到另一模具所需时间与技术员数，用于 SDST/换型优化。",
        "grain": "一个产线×起始模具×目标模具一行",
        "required": "选填（建议填）",
        "note": "首行同模具切换通常写 0 分钟；换型时间会进入 sequence 与容量。",
    },
    {
        "key": "inventory_policy",
        "purpose": "分时段库存策略：NORMAL/MONTH_END 的目标/最大库存天数、安全库存与最大库存。",
        "grain": "一个料号×周期类型一行",
        "required": "选填（建议填）",
        "note": "安全库存同时作为 FG 的目标库存来源；最大库存用于库存投影/软硬上限。",
    },
    {
        "key": "operation_leadtime",
        "purpose": "工序提前期：提前准备/静置时间、转运批量、容器。C4 用它推导 SF→FG 前置期。",
        "grain": "一个料号×阶段×工序一行",
        "required": "选填（启用 C4 时必填）",
        "note": "前置 = 静置时间 + min(工单量, 转运批量)×CT/TRP；首批完成并静置后才可转运。",
    },
    {
        "key": "tooling",
        "purpose": "工装模具主数据：模具编号、适用产线/零件、套数、换模时间。",
        "grain": "一个模具编号一行",
        "required": "必填",
        "note": "FG/SFN 的模具编号必须能在此表找到，否则按适用零件回退。",
    },
    {
        "key": "line_base",
        "purpose": "产线基础数据：TRP、单班工时、每天班次数。日历与求解器容量都以此为准。",
        "grain": "一个产线一行（必须唯一）",
        "required": "必填（算法强依赖）",
        "note": "每天班次数支持 2 班（12h）或 3 班（8h）；同一产线不要重复多行。",
    },
    {
        "key": "pm",
        "purpose": "设备预防性维护计划：维护窗口内产线产能为 0，任务需避让/切分/顺延。",
        "grain": "一条维保计划一行",
        "required": "必填",
        "note": "开始/结束时间可填时刻或班次；为空时按停机工时自动推导封锁班次。",
    },
    {
        "key": "customer",
        "purpose": "客户战略评级：客户等级、保供权重、OTD 目标、代表产品。",
        "grain": "一个客户一行",
        "required": "必填",
        "note": "代表配装成品用英文逗号分隔；客户等级影响优先级权重。",
    },
]

# (sheet_key, column_key) -> (meaning, type/unit, required, limits, example)
FIELD_META: Dict[Tuple[str, str], Tuple[str, str, str, str, str]] = {
    # common
    ("demand", "site"): ("站点代码", "文本", "是", "WL / SY38（大写，必须与上传时当前站点一致）", "WL"),
    ("demand", "part_no"): ("成品料号", "文本", "是", "必须存在于 Parameters-FG", "WAB6404"),
    ("demand", "description"): ("品名描述", "文本", "否", "≤100 字，便于人工核对", "E371 A1 传动杆总成"),
    ("stock", "site"): ("站点代码", "文本", "是", "WL / SY38", "WL"),
    ("stock", "material_type"): ("物料类型", "文本", "否", "产成品 / 半成品；仅展示", "产成品"),
    ("stock", "part_no"): ("物料料号", "文本", "是", "必须存在于 FG/SFN 参数表", "WAB6404"),
    ("stock", "description"): ("品名描述", "文本", "否", "≤100 字", "E371 A1 传动杆总成"),
    ("stock", "quantity"): ("期初库存", "整数/件", "是", "≥0；无库存填 0，不要留空", "838"),
    ("material_arrival", "site"): ("站点代码", "文本", "是", "WL / SY38", "WL"),
    ("material_arrival", "supplier"): ("合格供应商", "文本", "是", "供应商全称或简称", "宝武特钢管业"),
    ("material_arrival", "part_no"): ("物料料号", "文本", "是", "外购件料号，建议与关联零件可区分", "WAB6404-PIN"),
    ("material_arrival", "description"): ("物料描述", "文本", "否", "≤100 字", "E371 传动杆球铰轴"),
    ("material_arrival", "eta_date"): ("预计入库日期", "日期 YYYY-MM-DD", "是", "不早于计划开始日；不得晚于需要日否则可能缺料", "2026-10-14"),
    ("material_arrival", "eta_shift"): ("预计入库班次", "枚举", "是", "早班/中班/晚班；2 班制工厂用 早班/晚班", "中班"),
    ("material_arrival", "eta_time"): ("预计入库时间", "时刻 HH:MM", "否", "班次内的具体时刻；为空按班次开始", "14:00"),
    ("material_arrival", "quantity"): ("送达数量", "整数/件", "是", ">0", "6500"),
    ("parameters_fg", "site"): ("站点代码", "文本", "是", "WL / SY38", "WL"),
    ("parameters_fg", "part_no"): ("成品料号", "文本", "是", "唯一；与需求/BOM/Routing 一致", "WA14208"),
    ("parameters_fg", "description"): ("品名描述", "文本", "是", "≤100 字", "DX11电机连杆总成"),
    ("parameters_fg", "mpq"): ("最小包装量 MPQ", "整数/件", "是", ">0；成品排产按 MPQ 整批", "48"),
    ("parameters_fg", "hours_per_shift"): ("单班工时", "小数/小时", "否", ">0；建议与产线基础数据一致（8/12）", "8"),
    ("parameters_fg", "shifts_per_day"): ("每天班次数", "整数", "否", "1~3；建议与产线基础数据一致", "3"),
    ("parameters_fg", "moq"): ("最小生产量 MOQ", "整数/件", "否", "≥0；一般 ≥ MPQ；当前规则按 MPQ 执行", "48"),
    ("parameters_fg", "product_family"): ("产品族名", "文本", "否", "同族产品共享换模/合批；建议统一命名", "DX11"),
    ("parameters_fg", "mold_id"): ("模具编号", "文本", "否", "必须存在于 Tooling-Mold；否则按适用零件回退", "MOLD_WL_LINK_01"),
    ("parameters_fg", "special_shift"): ("特殊班次", "文本", "否", "如 HC09；留空表示常规班次", "HC09"),
    ("parameters_fg", "simultaneous_production"): ("同时生产", "Y/N", "否", "Y=左右件/共模同步产出；留空=N", "Y"),
    ("parameters_sfn", "site"): ("站点代码", "文本", "是", "WL / SY38", "WL"),
    ("parameters_sfn", "part_no"): ("半成品料号", "文本", "是", "唯一；与 BOM 子件/Routing 一致", "W000061536"),
    ("parameters_sfn", "description"): ("品名描述", "文本", "是", "≤100 字", "G38 DS轴套分总成"),
    ("parameters_sfn", "mpq"): ("最小起投批量 MPQ", "整数/件", "是", ">0；半成品总量 ≥ MPQ 后按件排产", "100"),
    ("parameters_sfn", "scrap_rate"): ("报废率", "小数", "是", "0~0.99；0.03 表示 3%，不要填 3", "0.03"),
    ("parameters_sfn", "max_stock"): ("最大库存", "整数/件", "否", "≥0；用于库存上限参考", "1000"),
    ("parameters_sfn", "moq"): ("最小起订量 MOQ", "整数/件", "否", "≥0；一般 ≥ MPQ", "100"),
    ("parameters_sfn", "product_family"): ("产品族名", "文本", "否", "同族共享换模/合批", "G38"),
    ("parameters_sfn", "mold_id"): ("模具编号", "文本", "否", "必须存在于 Tooling-Mold", "MOLD_WL_AXLE_01"),
    ("parameters_sfn", "special_shift"): ("特殊班次", "文本", "否", "如 HC09；留空=常规", "HC09"),
    ("bom", "site"): ("站点代码", "文本", "是", "WL / SY38", "WL"),
    ("bom", "fg_part_no"): ("母件：成品料号", "文本", "是", "必须存在于 Parameters-FG", "WAB6404"),
    ("bom", "fg_description"): ("母件品名", "文本", "否", "≤100 字", "E371 A1 传动杆总成"),
    ("bom", "sf_part_no"): ("子件：半成品料号", "文本", "是", "必须存在于 Parameters-SFN", "WAB6407"),
    ("bom", "sf_description"): ("子件品名", "文本", "否", "≤100 字", "E371 A1 DS 轴套分总成"),
    ("bom", "ratio"): ("装配单耗比例", "小数", "是", ">0；1 表示 1:1", "1"),
    ("routing", "site"): ("站点代码", "文本", "是", "WL / SY38", "WL"),
    ("routing", "part_no"): ("料号", "文本", "是", "必须存在于 FG/SFN 参数表", "WA14208"),
    ("routing", "stage"): ("阶段", "枚举", "是", "FG（成品）/ SF（半成品）", "FG"),
    ("routing", "op_seq"): ("工序序号", "整数", "否", ">0；同一料号按序号升序", "20"),
    ("routing", "op_name"): ("工序名称", "文本", "否", "如 总装 / 冲压/注塑", "总装"),
    ("routing", "primary_line"): ("主候选线", "文本", "是", "必须存在于“产线基础数据”；多线写多行", "PGLINKAGE"),
    ("routing", "certified"): ("认证", "TRUE/FALSE", "否", "TRUE=已认证机台；审计用", "TRUE"),
    ("routing", "ct_seconds"): ("单件节拍 CT", "小数/秒", "是", ">0；算法按 CT/TRP 折算机时", "38.0"),
    ("routing", "setup_min"): ("换型时间", "整数/分钟", "否", "≥0；优先以 Setup-Matrix 为准", "30"),
    ("routing", "priority"): ("优先级", "整数", "否", "1~9，数字越小越优先", "1"),
    ("routing", "effective_from"): ("生效日期", "日期 YYYY-MM-DD", "否", "不晚于计划开始日", "2026-01-01"),
    ("routing", "effective_to"): ("失效日期", "日期 YYYY-MM-DD", "否", "不早于计划结束日", "2026-12-31"),
    ("routing", "output_per_shift"): ("单班定额产能", "整数/件", "否", "建议留空，由 CT×TRP×工时自动计算", ""),
    ("routing", "headcount"): ("人数", "整数/人", "是*", ">0；每班定员，用于人力约束与 KPI", "3"),
    ("routing", "product_family"): ("产品族名", "文本", "否", "与 FG/SFN 保持一致", "DX11"),
    ("setup_matrix", "site"): ("站点代码", "文本", "是", "WL / SY38", "WL"),
    ("setup_matrix", "line"): ("产线", "文本", "是", "必须存在于“产线基础数据”", "轴套6"),
    ("setup_matrix", "from_mold"): ("起始模具", "文本", "是", "必须存在于 Tooling-Mold", "MOLD_WL_AXLE_01"),
    ("setup_matrix", "to_mold"): ("目标模具", "文本", "是", "必须存在于 Tooling-Mold", "MOLD_WL_AXLE_02"),
    ("setup_matrix", "setup_minutes"): ("换模工时", "整数/分钟", "是", "≥0；同模具写 0", "50"),
    ("setup_matrix", "technicians"): ("所需技术员数", "整数/人", "否", "≥1；用于换模技术员容量", "2"),
    ("setup_matrix", "cleaning_required"): ("是否需要清洁", "Y/N", "否", "Y/N；工艺要求", "Y"),
    ("setup_matrix", "effective_from"): ("生效日期", "日期 YYYY-MM-DD", "否", "不晚于计划开始日", "2026-01-01"),
    ("inventory_policy", "site"): ("站点代码", "文本", "是", "WL / SY38", "WL"),
    ("inventory_policy", "part_no"): ("料号", "文本", "是", "必须存在于 FG/SFN 参数表", "WA14208"),
    ("inventory_policy", "period_type"): ("周期类型", "枚举", "是", "NORMAL / MONTH_END", "NORMAL"),
    ("inventory_policy", "from_day"): ("起始日", "整数/日", "是", "1~31；含当日", "1"),
    ("inventory_policy", "to_day"): ("结束日", "整数/日", "是", "1~31；含当日，且 ≥ 起始日", "25"),
    ("inventory_policy", "target_days"): ("目标库存天数", "小数/天", "否", "≥0", "2.0"),
    ("inventory_policy", "max_days"): ("最大库存天数", "小数/天", "否", "≥目标天数", "5.0"),
    ("inventory_policy", "safety_stock"): ("安全库存", "整数/件", "否", "≥0；同时作为 FG 目标库存", "24"),
    ("inventory_policy", "max_stock"): ("最大库存", "整数/件", "否", "≥安全库存", "480"),
    ("inventory_policy", "effective_from"): ("生效日期", "日期 YYYY-MM-DD", "否", "不晚于计划开始日", "2026-10-01"),
    ("operation_leadtime", "site"): ("站点代码", "文本", "是", "WL / SY38", "WL"),
    ("operation_leadtime", "part_no"): ("料号", "文本", "是", "必须存在于 FG/SFN 参数表", "W000061536"),
    ("operation_leadtime", "stage"): ("阶段", "枚举", "是", "FG / SF", "SF"),
    ("operation_leadtime", "op_seq"): ("工序序号", "整数", "否", "与 Routing 一致", "10"),
    ("operation_leadtime", "op_name"): ("工序名称", "文本", "否", "与 Routing 一致", "冲压/注塑"),
    ("operation_leadtime", "prep_minutes"): ("提前准备/静置时间", "整数/分钟", "是*", "45~720；45=可极限转运，240~720=静置 4~12h", "360"),
    ("operation_leadtime", "transfer_batch"): ("转运批量", "整数/件", "是", ">0；首批全部完成并静置后才可转运", "100"),
    ("operation_leadtime", "container"): ("容器", "文本", "否", "周转箱/托盘等", "标准周转箱"),
    ("tooling", "site"): ("站点代码", "文本", "是", "WL / SY38", "WL"),
    ("tooling", "mold_id"): ("模具编号", "文本", "是", "唯一；被 FG/SFN/Setup-Matrix 引用", "MOLD_WL_LINK_01"),
    ("tooling", "mold_name"): ("工装模具名称", "文本", "是", "≤100 字", "PGLINKAGE 总装定位夹具"),
    ("tooling", "applicable_lines"): ("适用产线/设备", "文本", "是", "英文逗号分隔；需存在于产线基础数据", "PGLINKAGE"),
    ("tooling", "applicable_parts"): ("适用零件料号", "文本", "是", "英文逗号分隔；需存在于 FG/SFN", "WA14208, WAB6404"),
    ("tooling", "available_qty"): ("可用套数", "整数/套", "否", "≥1；多套可并行", "1"),
    ("tooling", "changeover_time_min"): ("换模工时", "整数/分钟", "否", "≥0", "30"),
    ("line_base", "line"): ("产线", "文本", "是", "唯一；与 Routing/Setup/PM 一致", "PGLINKAGE"),
    ("line_base", "trp"): ("效率 TRP", "小数", "是", "0<TRP≤1；0.71=71%", "0.71"),
    ("line_base", "hours_per_shift"): ("单班工时", "小数/小时", "是", ">0；2 班制常用 12，3 班制常用 8", "8"),
    ("line_base", "shifts_per_day"): ("每天班次数", "整数", "是", "2 或 3（也支持 1/4）", "3"),
    ("pm", "site"): ("站点代码", "文本", "是", "WL / SY38", "WL"),
    ("pm", "line"): ("产线", "文本", "是", "必须存在于“产线基础数据”", "PGLINKAGE"),
    ("pm", "pm_item"): ("维保项目/内容", "文本", "是", "项目名称+级别，≤100 字", "Level-2 周度气电与伺服定检"),
    ("pm", "start_date"): ("开始日期", "日期 YYYY-MM-DD", "是", "不早于计划开始日", "2026-10-15"),
    ("pm", "start_time"): ("开始时间", "时刻 HH:MM 或班次", "否", "为空时按停机工时推导；填时刻则按班次取整", "08:00"),
    ("pm", "end_date"): ("结束日期", "日期 YYYY-MM-DD", "是", "≥开始日期", "2026-10-15"),
    ("pm", "end_time"): ("结束时间", "时刻 HH:MM 或班次", "否", "为空时按停机工时推导", "16:00"),
    ("pm", "downtime_hours"): ("停机工时", "小数/小时", "是", ">0；决定封锁班次数", "8"),
    ("pm", "capacity_rate"): ("期间产能", "百分比 0~100", "否", "0=完全停机；100=不受影响", "0"),
    ("pm", "handling_strategy"): ("工单处置策略", "枚举", "是", "避让顺延 (Shift_Delay) / 跨班切分 (Batch_Split) / 暂停挂起 (Suspend)", "避让顺延 (Shift_Delay)"),
    ("customer", "site"): ("站点代码", "文本", "是", "WL / SY38", "WL"),
    ("customer", "customer_code"): ("客户代码", "文本", "是", "唯一；建议 CUST-XXX", "CUST-GEELY"),
    ("customer", "customer_name"): ("客户全称", "文本", "是", "≤100 字", "浙江吉利控股集团"),
    ("customer", "tier"): ("战略评级", "枚举", "是", "STRATEGIC / TIER_A / TIER_B", "STRATEGIC (战略级)"),
    ("customer", "priority_weight"): ("保供加权系数", "整数", "否", "≥1；战略级 10、A 级 3、B 级 1", "10"),
    ("customer", "otd_target_rate"): ("OTD 目标", "百分比 0~100", "否", "0~100", "100"),
    ("customer", "delivery_window"): ("交期浮动容差", "文本", "否", "展示用；建议写“0天/±1天/+3天”", "0天严格保供"),
    ("customer", "subcontract_allowed"): ("外协策略", "文本", "否", "严禁外协/协商外协/允许外协", "严禁外协 (No)"),
    ("customer", "associated_products"): ("代表配装成品", "文本", "否", "英文逗号分隔的 FG 料号", "WA14208, WAB6404"),
}


def build_counts(wb) -> Dict[str, int]:
    """Count data rows per input sheet (used in the table catalog)."""
    counts: Dict[str, int] = {}
    for spec in INPUT_SHEETS:
        ws = resolve_sheet(wb, spec)
        if ws is None:
            counts[spec.title] = 0
            continue
        positions = column_positions(ws, spec)
        keys = [positions[c.key] for c in spec.active_columns() if c.key in positions]
        n = 0
        for r in range(2, ws.max_row + 1):
            if any(ws.cell(r, c).value not in (None, "") for c in keys):
                n += 1
        counts[spec.title] = n
    return counts


def _cell(ws, row: int, col: int, value, *, font=None, fill=None, align=None,
          border=True, number_format: Optional[str] = None):
    cell = ws.cell(row, col)
    cell.value = value
    cell.font = font or BODY_FONT
    if fill:
        cell.fill = PatternFill("solid", fgColor=fill)
    cell.alignment = align or WRAP
    if border:
        cell.border = BORDER
    if number_format:
        cell.number_format = number_format
    return cell


def _title(ws, row: int, text: str, *, size: int = 11, fill: Optional[str] = None,
           color: str = WHITE, height: Optional[float] = None) -> int:
    ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=7)
    cell = _cell(ws, row, 2, text,
                 font=Font(name="微软雅黑", size=size, bold=True, color=color),
                 fill=fill)
    cell.alignment = Alignment(vertical="center", wrap_text=True)
    if height:
        ws.row_dimensions[row].height = height
    return row + 1


def _paragraph(ws, row: int, text: str, *, fill: Optional[str] = None, height: Optional[float] = None,
               font=None) -> int:
    ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=7)
    cell = _cell(ws, row, 2, text, font=font or BODY_FONT, fill=fill)
    if height:
        ws.row_dimensions[row].height = height
    return row + 1


def write_instructions(ws, counts: Optional[Dict[str, int]] = None) -> None:
    """Replace the Instructions sheet with the two-dimension guide."""
    for rng in list(ws.merged_cells.ranges):
        ws.unmerge_cells(str(rng))
    ws.delete_rows(1, ws.max_row)
    ws.sheet_view.showGridLines = False
    widths = {"A": 5.5, "B": 28, "C": 40, "D": 18, "E": 13, "F": 58, "G": 32}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width

    counts = counts or {}
    row = 1
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=7)
    cell = _cell(ws, 1, 1, "APS 高级排产 · 主数据填写说明（2026-10 统一模板）",
                 font=TITLE_FONT, border=False)
    cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 30
    row = 2
    row = _paragraph(ws, row,
                     "适用：温岭 WL / 沈阳 SY38 / 后续多站点统一标准　|　版本：2026-10-31　|　"
                     "共 15 张表（1 张说明 + 14 张输入）",
                     font=SUB_FONT)
    row = _paragraph(ws, row,
                     "本模板是各工厂共用的同一套标准：表名、表头、字段口径必须保持一致。"
                     "计划员只需按本厂数据填写，不要新增/删除工作表，也不要改第 1 行表头文字（列顺序可调整）。",
                     fill=LIGHT)
    row += 1

    # 1. workflow
    row = _title(ws, row, "一、使用流程", fill=NAVY, height=20)
    steps = [
        "1. 在系统第 6 个 Tab 选择目标工厂（WL / SY38），下载该站点模板；模板已预填本厂当前数据。",
        "2. 按“表清单”逐张核对；只在数据行修改，不要改表头、工作表名。",
        "3. 按“字段说明”填写单位/格式；料号、产线、模具编号必须能在对应主数据表中找到。",
        "4. 保存为 .xlsx（≤25MB），回到同一站点上传；系统会先校验 15 张表与必需列。",
        "5. 上传成功后核对提示条数（成品/半成品/订单），再执行排产。",
    ]
    for text in steps:
        row = _paragraph(ws, row, text, height=18)
    row += 1

    # 2. global rules
    row = _title(ws, row, "二、全局填写规则（多站点一致性）", fill=NAVY, height=20)
    rules = [
        "• 站点：必须填 WL 或 SY38（大写）；同一文件建议只放一个站点。上传时只会加载“当前选中站点”的行。",
        "• 表头：不要改第 1 行文字；列顺序可以调整；不要新增同义列。灰色列已被业务删除，系统不读取，可留可删。",
        "• 料号/产线/模具：跨表必须完全一致（区分大小写与后缀）；料号不要带空格。",
        "• 单位：数量=件；时间=分钟/小时（表头已注明）；CT=秒；TRP/报废率=小数（0.03 表示 3%）。",
        "• 日期：统一 YYYY-MM-DD；客户需求表的日期列用 MM/DD，且必须连续。",
        "• 空值：必填列不能空；无业务值时填 0 或按说明留空，不要用“/”“-”“待定”。",
        "• 公式：数据区不要写公式，上传按“值”读取；需要计算的值请粘贴为数值。",
        "• 重复行：同一唯一键（如一个料号、一个模具）只能出现一行；多产线/多工序请分行。",
        "• 文件：.xlsx；单文件 ≤25MB；不要合并单元格（说明页除外）。",
    ]
    for text in rules:
        row = _paragraph(ws, row, text, height=30)
    row += 1

    # 3. table catalog
    row = _title(ws, row, "三、表清单：每张表是做什么的", fill=NAVY, height=20)
    headers = ["#", "工作表", "用途", "数据粒度 / 唯一键", "本文件行数", "必填", "依赖 / 注意事项"]
    for idx, text in enumerate(headers, start=1):
        _cell(ws, row, idx, text, font=HEAD_FONT, fill=BLUE, align=CENTER)
    ws.row_dimensions[row].height = 22
    row += 1
    for i, guide in enumerate(TABLE_GUIDE, start=1):
        spec = SHEET_BY_KEY[guide["key"]]
        values = [i, spec.title, guide["purpose"], guide["grain"],
                  counts.get(spec.title, "—"), guide["required"], guide["note"]]
        for idx, value in enumerate(values, start=1):
            _cell(ws, row, idx, value, align=CENTER if idx in (1, 5) else WRAP)
        ws.row_dimensions[row].height = 42
        row += 1
    row += 1

    # 4. field dictionary
    row = _title(ws, row, "四、字段说明：每个字段的含义、单位与限制", fill=NAVY, height=20)
    row = _paragraph(ws, row,
                     "“必填=是”表示该列必须有表头，且关键字段为空会影响排产准确性（系统会提示）；"
                     "“是*”表示算法强依赖，虽然上传校验只强制表头，但留空会明显降低排产质量。字段名必须与下表完全一致。",
                     fill=LIGHT, height=30)
    for seq, guide in enumerate(TABLE_GUIDE, start=1):
        spec = SHEET_BY_KEY[guide["key"]]
        row = _title(ws, row, f"4.{seq}  {spec.title}　—　{guide['purpose']}",
                     size=10, fill=BLUE, height=34)
        headers = ["#", "字段（表头）", "含义", "类型 / 单位", "必填", "取值 / 限制", "示例 / 备注"]
        for idx, text in enumerate(headers, start=1):
            _cell(ws, row, idx, text, font=HEAD_FONT, fill=GREY, align=CENTER)
        # grey header uses dark font for contrast
        for idx in range(1, 8):
            ws.cell(row, idx).font = Font(name="微软雅黑", size=9, bold=True, color="FF262626")
        ws.row_dimensions[row].height = 20
        row += 1
        # date-axis special row
        seq_no = 1
        if spec.key == "demand":
            values = [str(seq_no), "日期列（10/13、10/14 …）", "各日期的客户需求量",
                      "整数/件", "否", "列头 MM/DD 连续；0 或空=当天无需求", "122"]
            for idx, value in enumerate(values, start=1):
                _cell(ws, row, idx, value, align=CENTER if idx in (1, 5) else WRAP)
            ws.row_dimensions[row].height = 30
            row += 1
            seq_no += 1
        for col in spec.active_columns():
            meta = FIELD_META.get((spec.key, col.key))
            if meta is None:
                meta = (col.header, "文本", "是" if col.required else "否", "", "")
            meaning, type_unit, required, limits, example = meta
            values = [str(seq_no), col.header, meaning, type_unit, required, limits, example]
            for idx, value in enumerate(values, start=1):
                _cell(ws, row, idx, value, align=CENTER if idx in (1, 5) else WRAP)
            ws.row_dimensions[row].height = 28
            row += 1
            seq_no += 1
        row += 1

    # 5. cross-table rules
    row = _title(ws, row, "五、跨表一致性规则（最容易出错的地方）", fill=NAVY, height=20)
    cross = [
        "1. 客户需求/期初库存/BOM 子件/工艺路线/库存策略/工序提前期中的料号，必须能在 Parameters-FG 或 Parameters-SFN 找到。",
        "2. Routing 的“主候选线”、Setup-Matrix/PM 的“产线”，必须出现在“产线基础数据”中。",
        "3. FG/SFN 的“模具编号”、Setup-Matrix 的起止模具，必须出现在 Tooling-Mold 中；找不到时按适用零件回退并提示。",
        "4. BOM 母件必须是 FG，子件必须是 SF；装配比例 >0。",
        "5. 同一料号/产线的唯一键不能重复；多产线、多工序要拆成多行。",
        "6. 班次几何（单班工时×每天班次数）由“产线基础数据”统一决定；Routing/PM 中的班次名称要与之一致。",
        "7. 安全库存同时作为 FG 目标库存来源；库存策略的最大库存用于上限审计。",
        "8. 半成品前置期由“提前准备时间 + 首批转运批量×CT/TRP”推导，不再单独填生产提前期。",
    ]
    for text in cross:
        row = _paragraph(ws, row, text, height=30)
    row += 1

    # 6. validation
    row = _title(ws, row, "六、系统校验与常见拒绝原因", fill=NAVY, height=20)
    checks = [
        "• 缺工作表：15 张表必须齐全（说明页也必须保留）。",
        "• 缺必需列/表头改名：按字段说明恢复第 1 行；列顺序可变。",
        "• 当前站点无数据：检查“站点”列是否全部是 WL 或 SY38，且与上传时选择的工厂一致。",
        "• 重复键：同一料号/模具/产线出现多行时，系统取最后一行或提示；请先清理。",
        "• 数值格式：不要把 0.03 写成 3；不要把 8 小时写成 08:00；不要把件数写成“100件”。",
        "• 日期格式：统一 YYYY-MM-DD；需求日期列 MM/DD 必须连续。",
        "• 文件问题：只接受 .xlsx；文件为空/超过 25MB 会被拒绝。",
    ]
    for text in checks:
        row = _paragraph(ws, row, text, height=30)
    row += 1

    # 7. changelog
    row = _title(ws, row, "七、版本与变更记录", fill=NAVY, height=20)
    logs = [
        ("2026-10-31", "统一 15 表标准；FG/SFN 移除产线/TRP；新增产线基础数据；生产提前期改为工序提前期的“提前准备/静置时间”。"),
        ("2026-10-31", "工装模具/PM/物料到货字段精简；新增预计入库时间；工作日历支持做四休三/做五休二/做六休一/做七无休。"),
        ("2026-10-31", "本说明页重构：新增表用途、字段含义/单位/限制、跨表一致性规则与上传校验说明。"),
    ]
    for date_text, text in logs:
        _cell(ws, row, 2, date_text, align=CENTER)
        ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=7)
        _cell(ws, row, 3, text)
        ws.row_dimensions[row].height = 34
        row += 1

    ws.freeze_panes = "A4"
    ws.sheet_view.zoomScale = 90
