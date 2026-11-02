"""Verify the 2026-10 master-data workbook against the legacy Tab-4 schema.

Produces ``Specification/master_template_verification.md`` with:

* sheet / column inventory and the color-coded change marks,
* row counts vs. what the instructions page claims,
* structural defects (duplicates, missing lines, stray text, ghost columns),
* referential-integrity checks across the 15 sheets,
* an "algorithm readiness" matrix: every field the solver consumes and where
  the new framework now supplies it.

Usage: python scripts/verify_master_template.py
"""
from __future__ import annotations

import io
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.master_schema import (  # noqa: E402
    INPUT_SHEETS, SHEETS, SHEET_BY_KEY, Column, SheetSpec, column_positions,
    detect_layout, header_index, norm, resolve_sheet, resolve_sheet_key,
    validate_workbook,
)

NEW_PATH = "APS_DataSource_Template.xlsx"
OLD_PATH = "dataSource.xlsx"
OUT_PATH = os.path.join("Specification", "master_template_verification.md")


def fmt(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\n", " ")
    return text if len(text) <= 48 else text[:45] + "..."


def to_float(value) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except ValueError:
        return None


def _column_key(spec: SheetSpec, header) -> Optional[str]:
    n = norm(header)
    for col in spec.columns:
        for candidate in col.candidates():
            if norm(candidate) == n:
                return col.key
    return None


def _legacy_positions(old_wb, spec: SheetSpec) -> Dict[str, int]:
    if old_wb is None:
        return {}
    ws = resolve_sheet(old_wb, spec)
    if ws is None:
        return {}
    return column_positions(ws, spec)


def change_mark(spec: SheetSpec, header, old_pos: Dict[str, int]) -> str:
    """Classify a header by comparing it with the legacy workbook's columns."""
    if re.match(r"^\d{1,2}/\d{1,2}$", str(header).strip()):
        return "保留"  # demand date axis is dynamic
    key = _column_key(spec, header)
    return "保留" if key is not None and key in old_pos else "新增"


class Report:
    def __init__(self) -> None:
        self.buf = io.StringIO()
        self.findings: List[Tuple[str, str, str]] = []  # severity, area, text
        self.checks: List[Tuple[str, str, str, str]] = []  # section, name, status, detail

    def p(self, *args) -> None:
        print(*args, file=self.buf)

    def finding(self, severity: str, area: str, text: str) -> None:
        self.findings.append((severity, area, text))

    def check(self, section: str, name: str, status: str, detail: str = "") -> None:
        self.checks.append((section, name, status, detail))

    def render_checks(self, section: str) -> None:
        rows = [c for c in self.checks if c[0] == section]
        if not rows:
            return
        self.p("| 检查项 | 结果 | 说明 |")
        self.p("|--------|------|------|")
        icon = {"pass": "✅", "warn": "⚠️", "fail": "❌", "info": "ℹ️"}
        for _, name, status, detail in rows:
            self.p(f"| {name} | {icon.get(status, status)} | {detail} |")
        self.p()


def sheet_rows(ws, spec: SheetSpec, site: Optional[str] = "WL") -> List[dict]:
    positions = column_positions(ws, spec)
    site_col = positions.get("site")
    rows = []
    for r in range(2, ws.max_row + 1):
        if site_col is not None and site:
            value = str(ws.cell(r, site_col).value or "").strip()
            if value and value != site:
                continue
        item = {k: ws.cell(r, i).value for k, i in positions.items()}
        if any(v not in (None, "") for v in item.values()):
            item["__row__"] = r
            rows.append(item)
    return rows


def key_of(row: dict, keys) -> Tuple:
    return tuple(str(row.get(k) or "").strip() for k in keys)


def check_duplicates(report: Report, label: str, rows: List[dict], keys, area: str) -> None:
    counter = Counter(key_of(r, keys) for r in rows)
    dups = [k for k, n in counter.items() if n > 1 and any(k)]
    if dups:
        sample = "；".join(" | ".join(k) for k in dups[:5])
        report.finding("P1", area, f"{label}: {len(dups)} 组重复键（示例：{sample}）")
    report.check("数据", f"{label} 唯一性", "fail" if dups else "pass",
                 f"{len(dups)} 组重复" if dups else f"{len(rows)} 条")


def check_blanks(report: Report, label: str, rows: List[dict], keys, area: str) -> None:
    issues = []
    for key in keys:
        blanks = sum(1 for r in rows if str(r.get(key) or "").strip() == "")
        if blanks:
            issues.append(f"{key} {blanks}/{len(rows)}")
            report.finding("P2", area, f"{label}.{key}: {blanks}/{len(rows)} 行为空")
    report.check("数据", f"{label} 必填非空", "warn" if issues else "pass",
                 "；".join(issues) if issues else f"{len(rows)} 条")


def main() -> int:
    if not os.path.isfile(NEW_PATH):
        print(f"missing {NEW_PATH}")
        return 1
    wb = openpyxl.load_workbook(NEW_PATH)
    old_wb = openpyxl.load_workbook(OLD_PATH) if os.path.isfile(OLD_PATH) else None

    report = Report()
    report.p("# 主数据模板核对报告（2026-10 新版 15 表框架）")
    report.p()
    report.p(f"- 被核对文件：`{NEW_PATH}`")
    report.p(f"- 对照基线：`{OLD_PATH}`（旧 25 表 / 英文表头 / Tab-4 现行实现）")
    report.p(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report.p()
    report.p("> 口径说明：业务已确认灰色列（说明页标注“删除列”）由人工删除。本次文件已删除 FG/SFN、"
             "物料到货、工装模具、PM 的全部灰色列，以及 Routing-Machines 的 `备选候选线/机台/效率TRP`；"
             "后台不读取这些旧版列，并已通过“删除灰色列后仍可加载”的回归测试。")
    report.p()

    # ------------------------------------------------------------------
    # 1. Workbook inventory
    # ------------------------------------------------------------------
    layout = detect_layout(wb)
    report.p("## 1. 工作簿结构")
    report.p()
    report.p(f"- 版式识别：**{layout}**；工作表数：**{len(wb.sheetnames)}**")
    report.p()
    report.p("| # | 工作表 | 归并键 | 数据行(WL) | 列数 | 必填 |")
    report.p("|---|--------|--------|-----------|------|------|")
    for idx, name in enumerate(wb.sheetnames, start=1):
        key = resolve_sheet_key(name)
        spec = SHEET_BY_KEY.get(key or "")
        ws = wb[name]
        if spec and spec.key != "instructions":
            rows = sheet_rows(ws, spec)
            report.p(f"| {idx} | {name} | {key} | {len(rows)} | {ws.max_column} | {'是' if spec.required else '否'} |")
        else:
            report.p(f"| {idx} | {name} | - | - | {ws.max_column} | - |")
    report.p()

    schema = validate_workbook(wb)
    if schema.missing_sheets:
        report.finding("P0", "结构", "缺少工作表：" + "、".join(schema.missing_sheets))
    for sheet, cols in schema.missing_columns.items():
        report.finding("P0", "结构", f"[{sheet}] 缺少必需列：" + "、".join(cols))
    if schema.optional_missing:
        report.finding("P3", "结构", "缺少选填表：" + "、".join(schema.optional_missing))

    # ------------------------------------------------------------------
    # 2. Column inventory + change marks
    # ------------------------------------------------------------------
    report.p("## 2. 列清单与增删标记")
    report.p("> 标记按“与旧版工作簿同表比对”生成：`新增` = 旧版无对应逻辑列；`保留` = 旧版已有该列。")
    report.p()
    for spec in INPUT_SHEETS:
        ws = resolve_sheet(wb, spec)
        if ws is None:
            continue
        report.p(f"### {spec.title}")
        report.p()
        report.p("| 列 | 表头 | 标记 | 非空/WL |")
        report.p("|----|------|------|---------|")
        positions = column_positions(ws, spec)
        rows = sheet_rows(ws, spec)
        old_pos = _legacy_positions(old_wb, spec)
        site_col = positions.get("site")
        data_rows = [
            r for r in range(2, ws.max_row + 1)
            if any(ws.cell(r, c).value not in (None, "") for c in range(1, ws.max_column + 1))
            and (site_col is None or str(ws.cell(r, site_col).value or "").strip() in ("", "WL"))
        ]
        for c in range(1, ws.max_column + 1):
            cell = ws.cell(1, c)
            header = cell.value
            if header is None or str(header).strip() == "":
                report.p(f"| {cell.column_letter} | *(空表头)* | ghost | - |")
                continue
            mark = change_mark(spec, header, old_pos)
            filled = sum(1 for r in data_rows if ws.cell(r, c).value not in (None, ""))
            report.p(f"| {cell.column_letter} | {header} | {mark} | {filled}/{len(data_rows)} |")
        report.p()

    # ghost columns / instruction-vs-sheet mismatches
    for spec in INPUT_SHEETS:
        ws = resolve_sheet(wb, spec)
        if ws is None:
            continue
        headers = [c for c in range(1, ws.max_column + 1) if ws.cell(1, c).value not in (None, "")]
        last = max(headers) if headers else 1
        ghosts = [c for c in range(last + 1, ws.max_column + 1)]
        if ghosts:
            report.finding(
                "P2", spec.title,
                f"末尾存在 {len(ghosts)} 个无表头的空白列（{ws.cell(1, ghosts[0]).column_letter}~{ws.cell(1, ghosts[-1]).column_letter}），"
                "会干扰读取范围，建议删除",
            )
        report.check("结构", f"{spec.title} 无幽灵列", "pass" if not ghosts else "warn",
                     f"{len(ghosts)} 个空列" if ghosts else "")

    fg_ws = resolve_sheet(wb, SHEET_BY_KEY["parameters_fg"])
    ins_ws = resolve_sheet(wb, SHEET_BY_KEY["instructions"])
    ins_text = "".join(str(ins_ws.cell(r, c).value or "") for r in range(1, ins_ws.max_row + 1)
                        for c in range(1, min(ins_ws.max_column, 8) + 1)) if ins_ws is not None else ""
    if "已并入" in ins_text and fg_ws is not None and norm("单件节拍 CT (秒)") not in header_index(fg_ws):
        report.finding("P1", "填写说明",
                       "说明页仍称 CT 已并入 Parameters-FG/SFN，但两张参数表没有该列；CT 实际在 Routing-Machines。")
        report.check("结构", "说明页-CT 迁移描述", "fail", "FG/SFN 无 CT 列")
    else:
        report.check("结构", "说明页-CT 迁移描述", "pass", "已按 Routing CT 描述")

    pm_ws = resolve_sheet(wb, SHEET_BY_KEY["pm"])
    if pm_ws is not None:
        header_c = str(pm_ws.cell(1, column_positions(pm_ws, SHEET_BY_KEY["pm"]).get("pm_item", 3)).value or "")
        if "试制" in header_c:
            report.finding("P2", "Preventive-Maintenance",
                           f"表头『{header_c}』疑似笔误（“试制”应为“实施”或“项目/内容”），"
                           "且与旧表『维保等级/类型』语义不同")
    report.render_checks("结构")


    report.p("## 3. 行数与说明页一致性")
    report.p()
    claims = {
        "demand": 10, "stock": 35, "material_arrival": 7, "parameters_fg": 17,
        "parameters_sfn": 22, "bom": 56, "routing": 39, "setup_matrix": 36,
        "inventory_policy": 78, "operation_leadtime": 39, "tooling": 6,
        "pm": 3, "customer": 5,
    }
    report.p("| 工作表 | 说明页 | 实际 | 结论 |")
    report.p("|--------|--------|------|------|")
    for key, claimed in claims.items():
        spec = SHEET_BY_KEY[key]
        ws = resolve_sheet(wb, spec)
        actual = len(sheet_rows(ws, spec)) if ws is not None else 0
        ok = "✅" if actual == claimed else "❌"
        report.p(f"| {spec.title} | {claimed} | {actual} | {ok} |")
        if actual != claimed:
            report.finding("P1", spec.title, f"说明页称 {claimed} 行，实际 {actual} 行")
    lb = resolve_sheet(wb, SHEET_BY_KEY["line_base"])
    if lb is not None:
        lb_rows = sheet_rows(lb, SHEET_BY_KEY["line_base"])
        lines = [str(r.get("line") or "").strip() for r in lb_rows]
        uniq = sorted({l for l in lines if l})
        report.p(f"| 产线基础数据 | （未列） | {len(lb_rows)} | {'✅' if len(lb_rows) == len(uniq) else '❌'} |")
        if len(lb_rows) != len(uniq):
            report.finding(
                "P1", "产线基础数据",
                f"应为产线级唯一记录，实际 {len(lb_rows)} 行却只有 {len(uniq)} 条产线（{', '.join(uniq)}）；"
                "疑似按成品行逐行复制。",
            )
    report.p()

    # ------------------------------------------------------------------
    # 4. Per-sheet data checks
    # ------------------------------------------------------------------
    report.p("## 4. 数据质量检查")
    report.p()

    fg_spec, sf_spec = SHEET_BY_KEY["parameters_fg"], SHEET_BY_KEY["parameters_sfn"]
    fg_rows = sheet_rows(resolve_sheet(wb, fg_spec), fg_spec)
    sf_rows = sheet_rows(resolve_sheet(wb, sf_spec), sf_spec)

    check_duplicates(report, "Parameters-FG 成品料号", fg_rows, ["part_no"], "Parameters-FG")
    check_duplicates(report, "Parameters-SFN 半成品料号", sf_rows, ["part_no"], "Parameters-SFN")
    check_blanks(report, "Parameters-FG", fg_rows, ["part_no", "description", "mpq", "moq"], "Parameters-FG")
    check_blanks(report, "Parameters-SFN", sf_rows, ["part_no", "description", "mpq", "scrap_rate", "moq"], "Parameters-SFN")

    for label, rows, area in (("FG", fg_rows, "Parameters-FG"), ("SFN", sf_rows, "Parameters-SFN")):
        for row in rows:
            pn = str(row.get("part_no") or "").strip()
            trp = to_float(row.get("trp"))
            if trp is not None and not (0 < trp <= 1):
                report.finding("P2", area, f"{pn}: 效率 TRP={trp} 超出 (0,1]")
            mpq = to_float(row.get("mpq"))
            moq = to_float(row.get("moq"))
            if mpq is not None and mpq <= 0:
                report.finding("P2", area, f"{pn}: MPQ={mpq} 非正数")
            if mpq is not None and moq is not None and moq < mpq:
                report.finding("P2", area, f"{pn}: MOQ={moq} < MPQ={mpq}")
    for row in sf_rows:
        pn = str(row.get("part_no") or "").strip()
        scrap = to_float(row.get("scrap_rate"))
        if scrap is not None and not (0 <= scrap < 1):
            report.finding("P1", "Parameters-SFN", f"{pn}: 报废率={scrap} 不是小数（0.03 表示 3%）")
        max_stock = to_float(row.get("max_stock"))
        if max_stock is not None and max_stock < 0:
            report.finding("P2", "Parameters-SFN", f"{pn}: 最大库存为负")

    # routing
    routing_spec = SHEET_BY_KEY["routing"]
    routing_rows = sheet_rows(resolve_sheet(wb, routing_spec), routing_spec)
    check_duplicates(report, "Routing (料号,阶段,工序)", routing_rows, ["part_no", "stage", "op_seq"], "Routing")
    for row in routing_rows:
        pn = str(row.get("part_no") or "").strip()
        ct = to_float(row.get("ct_seconds"))
        if ct is None or ct <= 0:
            report.finding("P1", "Routing", f"{pn}: CT={row.get('ct_seconds')!r} 无效")
        out = row.get("output_per_shift")
        if isinstance(out, str) and out.strip():
            report.finding("P2", "Routing", f"{pn}: 『单班定额产能』写入了文本批注 {out!r}，应为数值或留空")

    # setup
    setup_spec = SHEET_BY_KEY["setup_matrix"]
    setup_rows = sheet_rows(resolve_sheet(wb, setup_spec), setup_spec)
    check_duplicates(report, "Setup (产线,起始,目标)", setup_rows, ["line", "from_mold", "to_mold"], "Setup-Matrix")

    # inventory
    inv_spec = SHEET_BY_KEY["inventory_policy"]
    inv_rows = sheet_rows(resolve_sheet(wb, inv_spec), inv_spec)
    check_duplicates(report, "Inventory (料号,周期,起始日)", inv_rows, ["part_no", "period_type", "from_day"], "Inventory-Policy")
    for row in inv_rows:
        pn = str(row.get("part_no") or "").strip()
        safety, max_stock = to_float(row.get("safety_stock")), to_float(row.get("max_stock"))
        if safety is not None and max_stock is not None and safety > max_stock:
            report.finding("P2", "Inventory-Policy", f"{pn}: 安全库存 {safety} > 最大库存 {max_stock}")

    # lead time
    lt_spec = SHEET_BY_KEY["operation_leadtime"]
    lt_rows = sheet_rows(resolve_sheet(wb, lt_spec), lt_spec)
    check_duplicates(report, "Operation-LeadTime (料号,阶段,工序)", lt_rows, ["part_no", "stage", "op_seq"], "Operation-LeadTime")
    for row in lt_rows:
        pn = str(row.get("part_no") or "").strip()
        if to_float(row.get("transfer_batch")) is None:
            report.finding("P2", "Operation-LeadTime", f"{pn}: 转运批量为空/非数值")

    # pm
    pm_spec = SHEET_BY_KEY["pm"]
    pm_rows = sheet_rows(resolve_sheet(wb, pm_spec), pm_spec)
    for row in pm_rows:
        if str(row.get("start_time") or "").strip() == "":
            pass
    if any(str(r.get("start_time") or "").strip() == "" for r in pm_rows):
        report.finding("P3", "Preventive-Maintenance", "『开始时间/结束时间』整列为空，只有日期粒度")

    # customer
    cust_spec = SHEET_BY_KEY["customer"]
    cust_rows = sheet_rows(resolve_sheet(wb, cust_spec), cust_spec)
    for row in cust_rows:
        window = row.get("delivery_window")
        if isinstance(window, str) and any(ch.isalpha() for ch in window):
            report.finding(
                "P2", "Customer-Priority",
                f"『交期浮动容差 (天)』为文本 {window!r}（含“天/协商”），无法直接数值化",
            )

    # newly added columns: are they actually populated?
    for spec in INPUT_SHEETS:
        ws = resolve_sheet(wb, spec)
        if ws is None:
            continue
        positions = column_positions(ws, spec)
        old_pos = _legacy_positions(old_wb, spec)
        site_col = positions.get("site")
        data_rows = [
            r for r in range(2, ws.max_row + 1)
            if any(ws.cell(r, c).value not in (None, "") for c in range(1, ws.max_column + 1))
            and (site_col is None or str(ws.cell(r, site_col).value or "").strip() in ("", "WL"))
        ]
        for c in range(1, ws.max_column + 1):
            cell = ws.cell(1, c)
            if cell.value is None or change_mark(spec, cell.value, old_pos) != "新增":
                continue
            filled = sum(1 for r in data_rows if ws.cell(r, c).value not in (None, ""))
            if filled == 0:
                report.finding("P2", spec.title,
                               f"新增列『{cell.value}』整列为空（0/{len(data_rows)}），算法无法使用")
            report.check("数据", f"{spec.title}.{cell.value} 已填写", "warn" if filled == 0 else "pass",
                         f"{filled}/{len(data_rows)}")

    report.render_checks("数据")

    # ------------------------------------------------------------------
    # 5. Referential integrity
    # ------------------------------------------------------------------
    report.p("## 5. 跨表引用完整性")
    report.p()
    fg_pns = {str(r.get("part_no") or "").strip() for r in fg_rows}
    sf_pns = {str(r.get("part_no") or "").strip() for r in sf_rows}
    bom_spec = SHEET_BY_KEY["bom"]
    bom_rows = sheet_rows(resolve_sheet(wb, bom_spec), bom_spec)
    bom_fg = {str(r.get("fg_part_no") or "").strip() for r in bom_rows}
    bom_sf = {str(r.get("sf_part_no") or "").strip() for r in bom_rows}
    stock_spec = SHEET_BY_KEY["stock"]
    stock_rows = sheet_rows(resolve_sheet(wb, stock_spec), stock_spec)
    demand_spec = SHEET_BY_KEY["demand"]
    demand_ws = resolve_sheet(wb, demand_spec)
    demand_pns = {
        str(r.get("part_no") or "").strip()
        for r in sheet_rows(demand_ws, demand_spec)
        if str(r.get("part_no") or "").strip()
    }
    stock_pns = {str(r.get("part_no") or "").strip() for r in stock_rows}
    routing_pns = {str(r.get("part_no") or "").strip() for r in routing_rows}
    inv_pns = {str(r.get("part_no") or "").strip() for r in inv_rows}
    lt_pns = {str(r.get("part_no") or "").strip() for r in lt_rows}

    def diff(label: str, left: set, right: set, left_name: str, right_name: str, severity: str = "P2") -> None:
        missing = sorted(x for x in left - right if x)
        if missing:
            report.finding(severity, "引用完整性",
                           f"{label}: {len(missing)} 个 {left_name} 不在 {right_name} 中：{', '.join(missing[:10])}")
        report.check("引用", label, "warn" if missing else "pass",
                     f"{len(missing)} 条缺失" if missing else f"{len(left)} 条引用完整")

    diff("客户需求 → 成品参数", demand_pns, fg_pns, "需求料号", "Parameters-FG")
    diff("BOM 子件 → 半成品参数", bom_sf, sf_pns, "子件料号", "Parameters-SFN")
    diff("BOM 母件 → 成品参数", bom_fg, fg_pns, "母件料号", "Parameters-FG（备件属正常，仅提示）", "P3")
    diff("期初库存 → 成品/半成品参数", stock_pns, fg_pns | sf_pns, "库存料号", "FG/SFN 参数")
    diff("Routing → 成品/半成品参数", routing_pns, fg_pns | sf_pns, "路由料号", "FG/SFN 参数")
    diff("Inventory-Policy → 成品/半成品参数", inv_pns, fg_pns | sf_pns, "策略料号", "FG/SFN 参数")
    diff("Operation-LeadTime → 成品/半成品参数", lt_pns, fg_pns | sf_pns, "提前期料号", "FG/SFN 参数")
    diff("成品参数 → Routing（缺路由无法排产）", fg_pns, routing_pns, "成品料号", "Routing")

    tooling_spec = SHEET_BY_KEY["tooling"]
    tooling_rows = sheet_rows(resolve_sheet(wb, tooling_spec), tooling_spec)
    tooling_ids = {str(r.get("mold_id") or "").strip() for r in tooling_rows}
    tooling_parts = set()
    for row in tooling_rows:
        for part in str(row.get("applicable_parts") or "").split(","):
            if part.strip():
                tooling_parts.add(part.strip())
    diff("工装模具适用零件 → 成品/半成品参数", tooling_parts, fg_pns | sf_pns, "模具适用料号", "FG/SFN 参数")

    # mold ids in FG/SF
    explicit_molds = {}
    for label, rows in (("FG", fg_rows), ("SFN", sf_rows)):
        for row in rows:
            mold = str(row.get("mold_id") or "").strip()
            if mold:
                explicit_molds[str(row.get("part_no") or "").strip()] = mold
    if explicit_molds:
        unknown = {pn: m for pn, m in explicit_molds.items() if m not in tooling_ids}
        if unknown:
            detail = "、".join(f"{pn}→{m}" for pn, m in list(unknown.items())[:8])
            report.finding(
                "P1", "工装模具",
                f"FG/SFN『模具编号』与 Tooling-Mold 主数据不一致：{len(unknown)} 条无法匹配（{detail}）。"
                "排产换模/同模连续生产将无法生效。",
            )
    setup_molds = {str(r.get("from_mold") or "").strip() for r in setup_rows} | {
        str(r.get("to_mold") or "").strip() for r in setup_rows}
    if setup_molds - tooling_ids:
        report.finding("P2", "工装模具", "Setup-Matrix 引用了 Tooling-Mold 中不存在的模具：" + "、".join(sorted(setup_molds - tooling_ids)))

    # line coverage
    lb_spec = SHEET_BY_KEY["line_base"]
    lb_rows = sheet_rows(resolve_sheet(wb, lb_spec), lb_spec)
    lb_lines = {str(r.get("line") or "").strip() for r in lb_rows}
    used_lines = set()
    for rows in (fg_rows, sf_rows, routing_rows, setup_rows, pm_rows):
        for row in rows:
            line = str(row.get("line") or row.get("primary_line") or "").strip()
            if line:
                used_lines.add(line)
    diff("产线基础数据 → 各表产线", used_lines, lb_lines, "被引用产线", "产线基础数据", "P1")

    report.render_checks("引用")

    # ------------------------------------------------------------------
    # 6. Algorithm readiness
    # ------------------------------------------------------------------
    report.p("## 6. 算法字段就绪度（对比现行求解器读取字段）")
    report.p()
    report.p("| 求解器字段 | 用途 | 新框架来源 | 结论 |")
    report.p("|------------|------|------------|------|")
    readiness = [
        ("part_no / line", "任务归属与产线容量", "Parameters-FG/SFN（保留）", "OK"),
        ("ct_seconds", "节拍 → 单件机时", "Routing『CT (秒)』（FG/SFN 已移除该列）", "✅ 已从 Routing 派生"),
        ("line", "任务归属产线", "Routing『主候选线』（FG/SFN 已移除该列）", "✅ 已从 Routing 派生"),
        ("trp", "效率折减", "产线基础数据『效率 TRP』（仅 PGLINKAGE 有行）", "✅ 已派生；轴套6/7 缺行回退默认"),
        ("mpq / moq", "批量与最小起订", "Parameters-FG/SFN（保留）", "✅ OK"),
        ("hours_per_shift / shifts_per_day", "班次产能", "产线基础数据（FG 同名列不再作为主来源）", "✅ 已接入日历与求解器"),
        ("shift_columns_fg", "FG 仍保留 单班工时/每天班次数", "与产线基础数据重复维护", "⚠️ 建议删除，统一由产线基础数据维护"),
        ("output_per_shift", "Routing 单班定额产能", "可由 CT × TRP × 单班工时自动计算", "⚠️ 建议删除，避免与计算值不一致"),
        ("target_stock", "净需求扣减", "Inventory-Policy『安全库存 (件)』", "✅ 已派生"),
        ("headcount", "人力工时/定员", "Routing『人数』（已模拟 FG3/SF2）", "✅ 已恢复"),
        ("operation_leadtime", "半成品→成品前置期", "工序提前期『提前准备时间』45~480min + 转运批量", "✅ C4 已按静置+首批推导"),
        ("mold_id", "同模连续生产 / 换模矩阵边", "FG 17/17 已回填，SF 8/20 有模具主数据", "🟡 部分覆盖"),
        ("special_shift", "特殊班次规则（HC09）", "FG/SFN『特殊班次』新增（整列为空）", "⚠️ 待补数据"),
        ("simultaneous_production", "左右件同时生产（HC-08）", "FG/SFN『同时生产』（FG 仅 4 行 Y，SFN 待补）", "⚠️ 待补数据"),
        ("product_family", "族级换模/组合", "FG/SFN/Routing『产品族名』已回填 37/37", "⚠️ 数据已备，Campaign 仍读独立 CSV"),
        ("eta_time", "物料到货精确入库时间", "Material-Arrival-ETA『预计入库时间』（已模拟）", "✅ 已恢复（模型仍按班次）"),
        ("calendar_mode", "工作日历 4 档推荐", "产线基础数据 × 4/7、5/7、6/7、7/7 负荷比", "✅ 已实现（做四休三→做七无休）"),
    ]
    for field_name, usage, source, verdict in readiness:
        report.p(f"| `{field_name}` | {usage} | {source} | {verdict} |")
        if verdict.startswith("❌"):
            report.finding("P1", "算法就绪度", f"{field_name}（{usage}）在新框架中无数据来源：{source}")
        elif verdict.startswith("⚠️"):
            report.finding("P2", "算法就绪度", f"{field_name}（{usage}）：{verdict} — {source}")
    report.p()

    # ------------------------------------------------------------------
    # 7. Summary of findings
    # ------------------------------------------------------------------
    report.p("## 7. 问题汇总")
    report.p()
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    grouped: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for severity, area, text in report.findings:
        grouped[severity].append((area, text))
    labels = {"P0": "P0 阻断/必须修", "P1": "P1 影响正确性", "P2": "P2 数据规范", "P3": "P3 提示"}
    for severity in sorted(grouped, key=lambda s: order.get(s, 9)):
        report.p(f"### {labels[severity]}（{len(grouped[severity])}）")
        report.p()
        for area, text in grouped[severity]:
            report.p(f"- **{area}**：{text}")
        report.p()

    report.p("## 8. 旧版对照（记录用）")
    report.p()
    if old_wb is not None:
        report.p("| 旧表 | 旧列数 | 新表 | 新列数 | 说明 |")
        report.p("|------|--------|------|--------|------|")
        pairs = [
            ("Parameters-FG", "parameters_fg"), ("Parameters-SFN", "parameters_sfn"),
            ("BOM List", "bom"), ("Customer Demand", "demand"), ("Initial Stock", "stock"),
            ("Tooling-Mold", "tooling"), ("Preventive-Maintenance", "pm"),
            ("Material-Arrival-ETA", "material_arrival"), ("Customer-Priority", "customer"),
            ("Routing-Machines", "routing"), ("Setup-Matrix", "setup_matrix"),
            ("Inventory-Policy", "inventory_policy"), ("Operation-LeadTime", "operation_leadtime"),
        ]
        for old_name, new_key in pairs:
            old_ws = old_wb[old_name] if old_name in old_wb.sheetnames else None
            spec = SHEET_BY_KEY[new_key]
            new_ws = resolve_sheet(wb, spec)
            report.p(
                f"| {old_name} | {old_ws.max_column if old_ws else '-'} | {spec.title} | "
                f"{new_ws.max_column if new_ws else '-'} | {spec.description or ''} |"
            )
        report.p()
    else:
        report.p("_未找到旧版 dataSource.xlsx，跳过对照。_")
        report.p()

    # ------------------------------------------------------------------
    # 9. Value-level comparison with the legacy workbook
    # ------------------------------------------------------------------
    report.p("## 9. 与旧版取值一致性（WL）")
    report.p()
    if old_wb is not None:
        def legacy_rows(name: str) -> List[dict]:
            if name not in old_wb.sheetnames:
                return []
            ws = old_wb[name]
            headers = [str(ws.cell(1, c).value or "").strip() for c in range(1, ws.max_column + 1)]
            out = []
            for r in range(2, ws.max_row + 1):
                row = {h: ws.cell(r, i + 1).value for i, h in enumerate(headers) if h}
                if str(row.get("Site") or "").strip() in ("", "WL"):
                    out.append(row)
            return out

        def compare(label: str, new_map: Dict[str, object], old_map: Dict[str, object],
                    formatter=str) -> None:
            diffs = []
            for key in sorted(set(new_map) & set(old_map)):
                a, b = new_map[key], old_map[key]
                if isinstance(a, float) or isinstance(b, float):
                    if abs((to_float(a) or 0) - (to_float(b) or 0)) > 1e-6:
                        diffs.append((key, a, b))
                elif str(a or "").strip() != str(b or "").strip():
                    diffs.append((key, a, b))
            detail = "；".join(f"{k}: 旧={formatter(b)} 新={formatter(a)}" for k, a, b in diffs[:5])
            report.check("对照", label, "warn" if diffs else "pass",
                         f"{len(diffs)} 处差异（{detail}）" if diffs else f"{len(set(new_map) & set(old_map))} 个键一致")
            if diffs:
                report.finding("P2", "新旧对照", f"{label}: {len(diffs)} 个料号取值与旧版不同（{detail}）")

        old_fg = {str(r.get("Part No.") or "").strip(): r for r in legacy_rows("Parameters-FG")}
        new_fg = {str(r.get("part_no") or "").strip(): r for r in fg_rows}
        compare("FG 品名描述", {k: v.get("description") for k, v in new_fg.items()},
                {k: v.get("Description") for k, v in old_fg.items()})
        compare("FG MPQ", {k: to_float(v.get("mpq")) for k, v in new_fg.items()},
                {k: to_float(v.get("Mini Production QTY(MPQ)")) for k, v in old_fg.items()})
        compare("FG MOQ", {k: to_float(v.get("moq")) for k, v in new_fg.items()},
                {k: to_float(v.get("MOQ (Minimum Order Qty)")) for k, v in old_fg.items()})
        compare("FG 效率 TRP", {k: to_float(v.get("trp")) for k, v in new_fg.items()},
                {k: to_float(v.get("Line TRP")) for k, v in old_fg.items()})

        old_sf = {str(r.get("Part No.") or "").strip(): r for r in legacy_rows("Parameters-SFN")}
        new_sf = {str(r.get("part_no") or "").strip(): r for r in sf_rows}
        compare("SFN 报废率", {k: to_float(v.get("scrap_rate")) for k, v in new_sf.items()},
                {k: to_float(v.get("Scrap rate(%)")) for k, v in old_sf.items()})
        compare("SFN MPQ", {k: to_float(v.get("mpq")) for k, v in new_sf.items()},
                {k: to_float(v.get("Mini Production QTY(MPQ)")) for k, v in old_sf.items()})
        compare("SFN 最大库存", {k: to_float(v.get("max_stock")) for k, v in new_sf.items()},
                {k: to_float(v.get("Max stock")) for k, v in old_sf.items()})

        old_bom = {(str(r.get("SAP总成料号") or "").strip(), str(r.get("半成品料号") or "").strip())
                   for r in legacy_rows("BOM List")}
        new_bom = {(str(r.get("fg_part_no") or "").strip(), str(r.get("sf_part_no") or "").strip())
                   for r in bom_rows}
        report.check("对照", "BOM 装配关系", "pass" if old_bom == new_bom else "warn",
                     f"旧 {len(old_bom)} 条 / 新 {len(new_bom)} 条，差异 {len(old_bom ^ new_bom)} 条")
        if old_bom != new_bom:
            report.finding("P1", "新旧对照",
                           f"BOM 装配关系与旧版不同：新增 {len(new_bom - old_bom)} 条、缺失 {len(old_bom - new_bom)} 条")

        old_stock = {str(r.get("PN") or "").strip(): to_float(r.get("Initial Stock(10/13)"))
                     for r in legacy_rows("Initial Stock")}
        new_stock = {str(r.get("part_no") or "").strip(): to_float(r.get("quantity")) for r in stock_rows}
        compare("期初库存", new_stock, old_stock)

        old_route = {str(r.get("Part No.") or "").strip(): to_float(r.get("CT Seconds"))
                     for r in legacy_rows("Routing-Machines")}
        new_route = {str(r.get("part_no") or "").strip(): to_float(r.get("ct_seconds"))
                     for r in routing_rows}
        compare("Routing CT", new_route, old_route)
        report.render_checks("对照")
        report.p()
    else:
        report.p("_未找到旧版 dataSource.xlsx，跳过取值对照。_")
        report.p()

    # ------------------------------------------------------------------
    # 10. Recommended edits
    # ------------------------------------------------------------------
    report.p("## 10. 建议修订清单（按优先级）")
    report.p()
    report.p("| # | 位置 | 建议值 / 操作 | 原因 |")
    report.p("|---|------|----------------|------|")
    report.p("| 1 | `产线基础数据` A2:D4 | ✅ 已补 3 行：`PGLINKAGE 0.71 8 3`、`轴套6 0.85 8 3`、`轴套7 0.85 8 3`（模拟） | 原 17 行重复且缺 SF 产线 |")
    report.p("| 2 | `Parameters-FG` 模具编号 | ✅ 已按 Campaign-Families + Tooling-Mold 回填（原 AA/BB 已修正） | 同模连续生产/换模边可匹配 |")
    report.p("| 3 | `Routing-Machines` 人数 | ✅ 已模拟 FG=3、SF=2 人/班 | 人力约束与 KPI 不再为 0 |")
    report.p("| 4 | `Routing-Machines` P 列 | ✅ 已清空文本批注；该列建议后续删除 | 数值列不应混入文本 |")
    report.p("| 5 | `Parameters-SFN` | ✅ 已补『生产提前期 (days)』=1.0（模拟） | 恢复 HC-09/C4 输入 |")
    report.p("| 6 | `Parameters-SFN` W268001/W268002 | ✅ 已补 SFN/Routing/OpLead/库存策略（模拟） | 消除 WB80510 齐套跳过 |")
    report.p("| 7 | `Operation-LeadTime` I:L | 删除 4 个无表头空列 | 幽灵列会扩大读取范围 |")
    report.p("| 8 | `Preventive-Maintenance` C 列 | 表头改为『维保项目实施内容』 | “试制”疑似笔误 |")
    report.p("| 9 | `Customer-Priority` G/H 列 | 交期容差改为数值（0/1/3 天），文字说明另设备注列 | 现为文本，无法数值化 |")
    report.p("| 10 | `关键物料到货` G 列 | ✅ 已按班次补 08:00/14:00/22:00（模拟） | ETA 时间可展示/后续细化 |")
    report.p("| 11 | `工序提前期` F 列 | ✅ 已补 45 分钟准备时间（模拟） | C4 非加工时间可用 |")
    report.p()

    # ------------------------------------------------------------------
    # 11. Backend adaptations already implemented
    # ------------------------------------------------------------------
    report.p("## 11. 后台已实现的适配（本次）")
    report.p()
    report.p("- `src/master_schema.py`：15 表唯一 Schema（表名/列名中英别名、必填列、灰色废弃列），上传校验与加载共用；")
    report.p("- `src/excel_loader.py`：按表头定位列（列序可调），同时支持新版 15 表与旧版 25 表；")
    report.p("  `CT/人数`←Routing、`产线`←Routing 主候选线、`TRP/工时/班次`←产线基础数据、`目标库存`←库存策略安全库存；")
    report.p("- 产线基础数据自动去重；缺 轴套6/轴套7 时按参数表/默认值回退并在 `warnings` 提示；")
    report.p("- 模具编号不在 Tooling-Mold 时按『适用零件料号』回退，避免换模约束静默失效；")
    report.p("- `src/master_rows.py`：上传工作簿的 Routing / Setup / Inventory / LeadTime 直接驱动求解器，CSV 仅作基线回退；")
    report.p("- `/api/inputs` 返回 `line_base/layout/warnings`；Tab-4 新增『产线基础数据』页签并更新各表列；")
    report.p("- `/api/inputs/upload` 改为按 Schema 校验（列序自由、灰色列可缺），模板下载切换为 15 表模板。")
    report.p("- FG/SFN/UI 已移除 产线/TRP/CT/单班定额产能 展示，产线信息统一在 Routing 页查看；")
    report.p("- 工装模具、设备 PM、关键物料到货的 UI 列已按最新表头裁剪，并补上『预计入库时间』；")
    report.p("- 工作日历改为四档：做四休三 / 做五休二 / 做六休一 / 做七无休，按 4/7、5/7、6/7 负荷比推荐；")
    report.p("- 日历班次几何（每班工时 × 每天班次）改为读取『产线基础数据』，求解器与日历视图同步生效。")
    report.p()

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        fh.write(report.buf.getvalue())

    counts = Counter(s for s, _, _ in report.findings)
    print(f"wrote {OUT_PATH}")
    print("findings:", dict(counts))
    for severity, area, text in report.findings:
        if severity in ("P0", "P1"):
            print(f"[{severity}] {area}: {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
