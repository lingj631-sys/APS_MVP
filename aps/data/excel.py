from __future__ import annotations

import datetime as dt
import os
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from aps.data.issues import IssueLog
from aps.schema.registry import REGISTRY, EntitySpec, entities_with_sheet
from aps.schema.types import ContractTier, DataType, ParseError, parse_scalar

__all__ = ["HEADER_SCAN_ROWS", "RawRow", "RawTables", "locate_header", "read_workbook"]

HEADER_SCAN_ROWS = 6

RawTables = dict[str, list["RawRow"]]

_SPLIT_RE = re.compile(r"[,，]")

_CALENDAR_PATTERNS = (
    ("CONTINUOUS", 7, 0),
    ("6ON1OFF", 6, 1),
    ("5ON2OFF", 5, 2),
    ("4ON3OFF", 4, 3),
)


@dataclass
class RawRow:
    entity: str
    sheet: str | None
    excel_row: int | None
    values: dict[str, Any]


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _looks_like_date(value: Any) -> bool:
    if isinstance(value, (dt.datetime, dt.date)):
        return True
    if not isinstance(value, str):
        return False
    try:
        parse_scalar(value, DataType.DATE)
    except ParseError:
        return False
    return True


def locate_header(ws: Worksheet, spec: EntitySpec) -> tuple[int | None, dict[str, int], set[str]]:
    best_row: int | None = None
    best_score = 0
    best_map: dict[str, int] = {}
    best_hit: set[str] = set()
    limit = min(HEADER_SCAN_ROWS, ws.max_row or 0)
    for row_index in range(1, limit + 1):
        mapping: dict[str, int] = {}
        hit: set[str] = set()
        for col_index in range(1, (ws.max_column or 0) + 1):
            raw = ws.cell(row_index, col_index).value
            if _blank(raw):
                continue
            text = str(raw).strip()
            matched = False
            for field in spec.fields:
                if text in field.source_columns:
                    if field.name not in mapping:
                        mapping[field.name] = col_index
                    matched = True
            if matched:
                hit.add(text)
        if len(mapping) > best_score:
            best_score = len(mapping)
            best_row = row_index
            best_map = mapping
            best_hit = hit
    if best_score == 0:
        return None, {}, set()
    return best_row, best_map, best_hit


def _row_is_valid(spec: EntitySpec, values: dict[str, Any]) -> bool:
    for field in spec.fields:
        if not field.source_columns:
            continue
        if (field.pk or not field.nullable) and _blank(values.get(field.name)):
            return False
    return True


def _cell_values(ws: Worksheet, spec: EntitySpec, col_of: dict[str, int],
                 row_index: int) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in spec.fields:
        if not field.source_columns:
            continue
        col = col_of.get(field.name)
        if col is None:
            continue
        values[field.name] = ws.cell(row_index, col).value
    return values


def _report_structure(ws: Worksheet, spec: EntitySpec, header_row: int | None,
                      col_of: dict[str, int], log: IssueLog) -> None:
    if header_row is None:
        log.error(entity=spec.name, sheet=spec.sheet,
                  message=f"无法在表 {spec.sheet!r} 前 {HEADER_SCAN_ROWS} 行内定位表头")
        return
    present: set[str] = set()
    for col_index in range(1, (ws.max_column or 0) + 1):
        raw = ws.cell(header_row, col_index).value
        if not _blank(raw):
            present.add(str(raw).strip())
    missing = sorted(
        field.source_columns[0]
        for field in spec.fields
        if field.tier is ContractTier.T1 and field.source_columns
        and field.name not in col_of
    )
    for column in missing:
        log.error(entity=spec.name, sheet=spec.sheet, column=column, row=header_row,
                  message="缺少必需列（T1）")
    deprecated = spec.deprecated_headers & present
    for column in sorted(deprecated):
        log.warning(entity=spec.name, sheet=spec.sheet, column=column, row=header_row,
                    message="已废弃列残留，适配器不读取（spec §3.3）")
    extra = present - set(spec.header_to_field) - deprecated
    for column in sorted(extra):
        log.warning(entity=spec.name, sheet=spec.sheet, column=column, row=header_row,
                    message="模板未定义的多余列，已忽略")


def _extract_plain(ws: Worksheet, spec: EntitySpec, col_of: dict[str, int],
                   header_row: int, site: str) -> Iterator[RawRow]:
    for row_index in range(header_row + 1, (ws.max_row or 0) + 1):
        values = _cell_values(ws, spec, col_of, row_index)
        if spec.site_injected:
            values["site_code"] = site
        if not _row_is_valid(spec, values):
            continue
        yield RawRow(spec.name, spec.sheet, row_index, values)


def _extract_demand(ws: Worksheet, spec: EntitySpec, col_of: dict[str, int],
                    header_row: int, site: str) -> Iterator[RawRow]:
    known = set(spec.header_to_field)
    date_cols: list[tuple[int, Any]] = []
    for col_index in range(1, (ws.max_column or 0) + 1):
        raw = ws.cell(header_row, col_index).value
        if _blank(raw):
            continue
        text = str(raw).strip()
        if text in known:
            continue
        if _looks_like_date(raw):
            date_cols.append((col_index, raw))
    pn_col = col_of.get("pn")
    if pn_col is None or not date_cols:
        return
    for row_index in range(header_row + 1, (ws.max_row or 0) + 1):
        base = _cell_values(ws, spec, col_of, row_index)
        base["site_code"] = site if spec.site_injected else base.get("site_code")
        if _blank(base.get("pn")):
            continue
        for col_index, header_value in date_cols:
            raw = ws.cell(row_index, col_index).value
            number = _as_number(raw)
            if number is None or number <= 0:
                continue
            values = dict(base)
            values["demand_date"] = header_value
            values["quantity"] = raw
            yield RawRow(spec.name, spec.sheet, row_index, values)


def _extract_tooling(ws: Worksheet, spec: EntitySpec, col_of: dict[str, int],
                     header_row: int, site: str, log: IssueLog) -> Iterator[RawRow]:
    line_col = col_of.get("line_code")
    for row_index in range(header_row + 1, (ws.max_row or 0) + 1):
        values = _cell_values(ws, spec, col_of, row_index)
        if _blank(values.get("mold_id")):
            continue
        raw_lines = values.get("line_code")
        lines = [p for p in (s.strip() for s in _SPLIT_RE.split(str(raw_lines or ""))) if p]
        if not lines:
            log.warning(entity=spec.name, field="line_code", sheet=spec.sheet, row=row_index,
                        column="适用产线/设备", value=raw_lines,
                        message="适用产线为空，无法确定模具-产线关系，已跳过该行")
            continue
        for line_code in lines:
            row_values = dict(values)
            row_values["line_code"] = line_code
            yield RawRow(spec.name, spec.sheet, row_index, row_values)


def _assign_arrival_seq(rows: list[RawRow]) -> None:
    counters: dict[tuple, int] = defaultdict(int)
    for row in rows:
        key = (row.values.get("site_code"), row.values.get("pn"),
               row.values.get("eta_date"), row.values.get("eta_shift"))
        counters[key] += 1
        row.values["seq"] = counters[key]


def _dedupe_molds(rows: list[RawRow], log: IssueLog) -> list[RawRow]:
    spec = REGISTRY["mold"]
    best: dict[tuple, RawRow] = {}
    for row in rows:
        key = tuple(row.values.get(name) for name in spec.pk)
        existing = best.get(key)
        if existing is None:
            best[key] = row
            continue
        differing = [
            field.name for field in spec.fields
            if field.name not in spec.pk
            and str(row.values.get(field.name)) != str(existing.values.get(field.name))
        ]
        if differing:
            log.error(entity="mold", sheet=row.sheet, row=row.excel_row,
                      message=f"同一模具在第 {existing.excel_row} 行与第 {row.excel_row} 行"
                              f"声明不一致的字段: {differing}")
    return [best[key] for key in sorted(best, key=lambda k: tuple(str(x) for x in k))]


def _build_parts(tables: RawTables, site: str, log: IssueLog) -> list[RawRow]:
    parts: dict[str, dict[str, Any]] = {}

    def offer(pn: Any, stage: Any, description: Any, family: Any, source: str) -> None:
        if _blank(pn):
            return
        key = str(pn).strip()
        current = parts.get(key)
        if current is None:
            parts[key] = {"site_code": site, "pn": key, "stage": stage,
                          "description": description, "family_code": family,
                          "uom": "件", "_source": source}
            return
        if current["_source"] in ("part_param_fg", "part_param_sf") \
                and source in ("part_param_fg", "part_param_sf") \
                and current["_source"] != source:
            log.warning(entity="part", field="stage", sheet=None, row=None, column=None,
                        value=key,
                        message=f"料号 {key} 同时出现在成品与半成品参数表，阶段歧义，取 "
                                f"{current['stage']}")
        if _blank(current["description"]) and not _blank(description):
            current["description"] = description
        if _blank(current["family_code"]) and not _blank(family):
            current["family_code"] = family

    for row in tables.get("part_param_fg", ()):
        offer(row.values.get("pn"), "FG", row.values.get("description"),
              row.values.get("family_code"), "part_param_fg")
    for row in tables.get("part_param_sf", ()):
        offer(row.values.get("pn"), "SF", row.values.get("description"),
              row.values.get("family_code"), "part_param_sf")
    for row in tables.get("routing", ()):
        pn = row.values.get("pn")
        stage = row.values.get("stage")
        key = str(pn).strip() if not _blank(pn) else ""
        existing = parts.get(key)
        if existing is not None and not _blank(stage) \
                and str(existing["stage"]) not in (str(stage),) \
                and existing["_source"] not in ("part_param_fg", "part_param_sf"):
            log.warning(entity="part", field="stage", sheet=row.sheet, row=row.excel_row,
                        value=pn,
                        message=f"料号 {pn} 的 Routing 阶段 {stage!r} 与已定阶段 "
                                f"{existing['stage']!r} 不一致")
        offer(pn, stage, None, row.values.get("family_code"), "routing")
    for row in tables.get("bom", ()):
        offer(row.values.get("parent_pn"), "FG", row.values.get("parent_description"),
              None, "bom")
        offer(row.values.get("child_pn"), "SF", row.values.get("child_description"),
              None, "bom")
    for row in tables.get("initial_stock", ()):
        offer(row.values.get("pn"), row.values.get("material_type"),
              row.values.get("description"), None, "initial_stock")
    for row in tables.get("demand", ()):
        offer(row.values.get("pn"), "FG", row.values.get("description"), None, "demand")

    out: list[RawRow] = []
    for key in sorted(parts):
        values = {k: v for k, v in parts[key].items() if not k.startswith("_")}
        if _blank(values["stage"]):
            values["stage"] = "COMP"
        out.append(RawRow("part", None, None, values))
    return out


def _build_families(tables: RawTables, site: str) -> list[RawRow]:
    codes = {
        str(row.values["family_code"]).strip()
        for row in tables.get("part", ())
        if not _blank(row.values.get("family_code"))
    }
    return [
        RawRow("product_family", None, None,
               {"site_code": site, "family_code": code, "name": code})
        for code in sorted(codes)
    ]


def read_workbook(path: str | os.PathLike[str], site: str) -> tuple[RawTables, IssueLog]:
    log = IssueLog()
    wb = openpyxl.load_workbook(os.fspath(path), data_only=True)
    sheets = {str(ws.title).strip(): ws for ws in wb.worksheets}
    tables: RawTables = {}

    for spec in entities_with_sheet():
        ws = sheets.get(spec.sheet)
        if ws is None:
            if spec.sheet_required:
                log.error(entity=spec.name, sheet=spec.sheet, message="工作表缺失")
            else:
                log.warning(entity=spec.name, sheet=spec.sheet,
                            message=f"工作表缺失（{spec.sheet}，V3 扩展表，V2 工作簿合法缺省）")
            continue
        header_row, col_of, _hit = locate_header(ws, spec)
        _report_structure(ws, spec, header_row, col_of, log)
        if header_row is None:
            continue
        if spec.unpivot == "demand_date":
            rows = list(_extract_demand(ws, spec, col_of, header_row, site))
        elif spec.name == "tooling_mold":
            rows = list(_extract_tooling(ws, spec, col_of, header_row, site, log))
        else:
            rows = list(_extract_plain(ws, spec, col_of, header_row, site))
        existing = tables.setdefault(spec.name, [])
        existing.extend(rows)

    if "material_arrival" in tables:
        _assign_arrival_seq(tables["material_arrival"])
    if "tooling_mold" in tables:
        tables["mold"] = _dedupe_molds(tables.get("mold", []), log)
    if "setup_matrix" in tables:
        known_molds = {r.values.get("mold_id") for r in tables.get("mold", [])}
        for sm in tables["setup_matrix"]:
            for m_key in ("from_mold", "to_mold"):
                m_id = sm.values.get(m_key)
                if m_id and m_id not in known_molds:
                    tables.setdefault("mold", []).append(
                        RawRow("mold", "换模矩阵 Setup-Matrix", sm.excel_row,
                               {"site_code": site, "mold_id": m_id, "name": m_id,
                                "available_sets": 1, "changeover_min": 0.0})
                    )
                    known_molds.add(m_id)

    tables["site"] = [RawRow("site", None, None, {"site_code": site})]
    tables["calendar_pattern"] = [
        RawRow("calendar_pattern", None, None,
               {"pattern_code": code, "work_days": work, "rest_days": rest})
        for code, work, rest in _CALENDAR_PATTERNS
    ]
    tables["part"] = _build_parts(tables, site, log)
    tables["product_family"] = _build_families(tables, site)
    return tables, log
