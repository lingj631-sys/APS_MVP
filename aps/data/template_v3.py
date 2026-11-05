from __future__ import annotations

import datetime as dt
import os
from collections.abc import Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from aps.schema.registry import EntitySpec, FieldSpec, entities_with_sheet
from aps.schema.types import ContractTier, DataType

__all__ = [
    "DEMAND_DAYS",
    "DEMAND_START",
    "INSTRUCTIONS_SHEET",
    "PREAMBLE",
    "SHEET_ORDER",
    "TYPE_LABEL",
    "demand_date_columns",
    "header_for",
    "header_tier",
    "headers_for_sheet",
    "instruction_rows",
    "specs_for_sheet",
    "write_v3_template",
]

INSTRUCTIONS_SHEET = "填写说明 Instructions"
DEMAND_START = dt.date(2026, 11, 3)
DEMAND_DAYS = 34

SHEET_ORDER: tuple[str, ...] = (
    "客户需求订单清单 Customer Demand",
    "期初可用库存 Initial Stock",
    "关键物料到货 Material-Arrival-ETA",
    "产线与成品参数 Parameters-FG",
    "半成品制造参数 Parameters-SFN",
    "产品装配清单 BOM List",
    "工艺路线与候选机台 Routing-Machines",
    "换模矩阵 Setup-Matrix",
    "库存策略 Inventory-Policy",
    "工序提前期 Operation-LeadTime",
    "工装模具约束 Tooling-Mold",
    "产线基础数据",
    "设备预防维护 Preventive-Maintenance",
    "客户战略评级 Customer-Priority",
    "班次人力可用性",
    "产品配对关系",
    "工作日历",
    "换模技术员",
)

TYPE_LABEL: dict[DataType, str] = {
    DataType.TEXT: "文本",
    DataType.INT: "整数",
    DataType.REAL: "小数",
    DataType.BOOL: "是/否（true/false、1/0、yes/no、是/否）",
    DataType.DATE: "日期（yyyy-mm-dd 或 yyyy/mm/dd）",
    DataType.TIME: "时间（hh:mm 或 hh:mm:ss）",
    DataType.DATETIME: "日期时间（yyyy-mm-dd hh:mm）",
    DataType.MULTI_TEXT: "多个值，用英文逗号分隔",
}

PREAMBLE: tuple[str, ...] = (
    "APS 数据源模板 V3。本工作簿由实体登记表自动生成，不要手工增删列。",
    "列序可以自由调整——导入器按表头文字定位列，不按列号。",
    "白底加粗表头 = 契约层级 T1（必填）：缺列或整列留空会阻止导入。",
    "灰底表头 = 契约层级 T2（选填）：整列留空不阻止导入，但依赖它的规则会标记 degraded，"
    "并在绑定报告里可见。",
    "「产线基础数据」没有「站点」列：站点由导入命令的 --site 参数注入，"
    "一份工作簿只属于一个站点。",
    "「客户需求订单清单」是宽表：左侧四列是料号信息，右侧每一列表头是一个日期，"
    "单元格填该日需求量（件）。留空或填 0 表示当日无需求。",
    "V3 相对 V2 的五项扩展（spec §3.5）：",
    "  1. 「客户需求订单清单」新增列「客户代码 (Code)」——把需求行连到「客户战略评级」，"
    "T2。留空则客户保供加权规则标记 degraded。",
    "  2. 新增表「班次人力可用性」——站点 / 产线 / 日期 / 班次 / 可用人数，"
    "劳动力人数上限规则的输入，T2。",
    "  3. 新增表「产品配对关系」——站点 / 左件料号 / 右件料号 / 配对类型 / 同步容差班次，"
    "共产品同步产出的 schema 预留，本版模型不读，T2。",
    "  4. 新增表「工作日历」——站点 / 产线 / 日期 / 班次序号 / 是否开班 / 备注，"
    "用来表达「某天临时停一班」。它与工作制推导冲突时以本表为准，T2。",
    "  5. 新增表「换模技术员」——站点 / 技术员 / 日期 / 班次 / 可用产线 / 可同时换模数，"
    "换型技术员跨线互斥的 schema 预留，本版模型不读，T2。",
    "扩展 2–5 是四张新表。它们在 V2 工作簿里不存在是合法的，导入器只 warn、不 error。",
    "「工序提前期」的「提前准备时间 (分钟)」在 V3 统一为半角括号；"
    "V2 的全角写法「提前准备时间（分钟）」导入器仍然认。",
    "已废弃列（spec §3.3）在本模板中不再出现：Parameters-FG / Parameters-SFN 的「产线」"
    "「效率 TRP」、Routing-Machines 的「备选候选线」「机台」「TRP」、"
    "Material-Arrival-ETA 的「放行工时」「关联排产零件」「状态」。",
)

INSTRUCTION_HEADER: tuple[str, ...] = (
    "工作表", "列名", "类型", "契约层级", "必填", "单位", "允许值", "主键", "说明",
)

_T2_FILL = PatternFill(start_color="FFD9D9D9", end_color="FFD9D9D9", fill_type="solid")
_HEADER_FONT = Font(bold=True)
_HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)


def specs_for_sheet(sheet: str) -> tuple[EntitySpec, ...]:
    return tuple(spec for spec in entities_with_sheet() if spec.sheet == sheet)


def header_for(field: FieldSpec) -> str | None:
    if not field.source_columns:
        return None
    for header in field.source_columns:
        if "（" not in header and "）" not in header:
            return header
    return field.source_columns[0]


def headers_for_sheet(specs: Sequence[EntitySpec]) -> tuple[str, ...]:
    out: list[str] = []
    for spec in specs:
        for field in spec.fields:
            header = header_for(field)
            if header is not None and header not in out:
                out.append(header)
    return tuple(out)


def header_tier(specs: Sequence[EntitySpec], header: str) -> ContractTier:
    tiers = [
        field.tier
        for spec in specs
        for field in spec.fields
        if header_for(field) == header
    ]
    if any(tier is ContractTier.T1 for tier in tiers):
        return ContractTier.T1
    return ContractTier.T2


def demand_date_columns(start: dt.date = DEMAND_START,
                        days: int = DEMAND_DAYS) -> tuple[dt.datetime, ...]:
    return tuple(
        dt.datetime.combine(start + dt.timedelta(days=offset), dt.time())
        for offset in range(max(0, days))
    )


def _field_for_header(specs: Sequence[EntitySpec],
                      header: str) -> tuple[EntitySpec | None, FieldSpec | None]:
    for spec in specs:
        for field in spec.fields:
            if header_for(field) == header:
                return spec, field
    return None, None


def _allowed_values(field: FieldSpec) -> str:
    if field.enum_codes:
        return " / ".join(field.enum_codes)
    if field.dtype is DataType.BOOL:
        return TYPE_LABEL[DataType.BOOL]
    return ""


def _column_note(spec: EntitySpec, field: FieldSpec) -> str:
    parts: list[str] = []
    if field.pk:
        parts.append("主键列：留空的行会被整行忽略")
    if field.tier is ContractTier.T1:
        parts.append("T1 必填，缺列或留空阻止导入")
    else:
        parts.append("T2 选填，整列留空则依赖它的规则标记 degraded")
    if field.fk:
        parts.append(f"外键 → {field.fk}")
        if field.fk_optional:
            parts.append("外键找不到目标时只 warn，不阻断（修订 A-5）")
    if field.provenance == "derived":
        parts.append("由系统推导")
    if field.resolved_parser == "enum_code":
        parts.append("可填「中文标签 (代码)」，也可直接填代码")
    elif field.resolved_parser == "stage":
        parts.append("FING 自动归一为 FG，SFIN 自动归一为 SF")
    elif field.resolved_parser == "shift_index":
        parts.append("早班/白班→0，中班→1，晚班→2，夜班→1")
    return "；".join(parts)


def instruction_rows() -> tuple[tuple[str, ...], ...]:
    rows: list[tuple[str, ...]] = [INSTRUCTION_HEADER]
    for sheet in SHEET_ORDER:
        specs = specs_for_sheet(sheet)
        for header in headers_for_sheet(specs):
            spec, field = _field_for_header(specs, header)
            if spec is None or field is None:
                continue
            rows.append((
                sheet,
                header,
                TYPE_LABEL[field.dtype],
                field.tier.name,
                "是" if field.tier is ContractTier.T1 else "否",
                field.unit or "",
                _allowed_values(field),
                "是" if field.pk else "",
                _column_note(spec, field),
            ))
        if specs and specs[0].unpivot == "demand_date":
            rows.append((
                sheet, "（日期列）", TYPE_LABEL[DataType.DATE], "T1", "是", "件",
                f"{DEMAND_START.isoformat()} 起连续 {DEMAND_DAYS} 天，每日一列", "",
                "需求宽表的日期轴。单元格填该日需求量；留空或 ≤ 0 视为无需求，"
                "不会生成需求行。",
            ))
    return tuple(rows)


def _write_header(ws: Worksheet, headers: Sequence, specs: Sequence[EntitySpec]) -> None:
    for col, header in enumerate(headers, start=1):
        cell = ws.cell(1, col)
        cell.value = header
        cell.font = _HEADER_FONT
        letter = get_column_letter(col)
        if isinstance(header, dt.datetime):
            cell.number_format = "yyyy-mm-dd"
            ws.column_dimensions[letter].width = 12
            continue
        cell.alignment = _HEADER_ALIGN
        if header_tier(specs, header) is ContractTier.T2:
            cell.fill = _T2_FILL
        ws.column_dimensions[letter].width = max(12, min(30, len(header) + 6))
    ws.freeze_panes = "A2"


def _write_instructions(wb: Workbook, site: str) -> None:
    ws = wb.create_sheet(INSTRUCTIONS_SHEET)
    ws.cell(1, 1, f"填写说明 Instructions（V3 · 站点 {site}）").font = Font(bold=True, size=14)
    row = 3
    for line in PREAMBLE:
        ws.cell(row, 1, line)
        row += 1
    row += 1
    for offset, cells in enumerate(instruction_rows()):
        for col, value in enumerate(cells, start=1):
            cell = ws.cell(row + offset, col, value)
            if offset == 0:
                cell.font = _HEADER_FONT
            cell.alignment = Alignment(vertical="top", wrap_text=(col == 9))
    widths = {"A": 34, "B": 26, "C": 30, "D": 10, "E": 8, "F": 10, "G": 34, "H": 8, "I": 78}
    for letter, width in sorted(widths.items()):
        ws.column_dimensions[letter].width = width


def write_v3_template(out_path: str | os.PathLike[str], site: str,
                      demand_start: dt.date = DEMAND_START,
                      demand_days: int = DEMAND_DAYS) -> None:
    wb = Workbook()
    wb.remove(wb.active)
    _write_instructions(wb, site)
    for sheet in SHEET_ORDER:
        specs = specs_for_sheet(sheet)
        headers: list = list(headers_for_sheet(specs))
        if specs and specs[0].unpivot == "demand_date":
            headers.extend(demand_date_columns(demand_start, demand_days))
        _write_header(wb.create_sheet(sheet), headers, specs)
    wb.save(os.fspath(out_path))
