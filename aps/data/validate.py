from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field as dc_field
from typing import Any

from aps.data.excel import RawRow, RawTables
from aps.data.issues import Issue, IssueLog, Severity
from aps.schema.registry import REGISTRY, STAGE_NORMALIZE, EntitySpec, FieldSpec
from aps.schema.types import (
    ContractTier,
    DataType,
    DupPolicy,
    ParseError,
    parse_enum_code,
    parse_scalar,
    parse_shift_index,
)

__all__ = [
    "CandidateRow",
    "ParsedTables",
    "ValidationReport",
    "detect_gaps",
    "gate_business",
    "gate_references",
    "gate_types",
    "resolve_duplicates",
    "validate",
]

ParsedTables = dict[str, list["CandidateRow"]]

_RANGE_RULES: tuple[tuple[str, str, str, float | None, float | None], ...] = (
    ("line", "trp_efficiency", "gt_le", 0.0, 1.0),
    ("line", "shift_hours", "gt", 0.0, None),
    ("line", "shifts_per_day", "ge", 1.0, None),
    ("part_param_fg", "mpq", "gt", 0.0, None),
    ("part_param_sf", "mpq", "gt", 0.0, None),
    ("part_param_sf", "scrap_rate", "ge_lt", 0.0, 1.0),
    ("routing", "ct_sec", "gt", 0.0, None),
    ("routing", "op_seq", "ge", 0.0, None),
    ("bom", "ratio", "gt", 0.0, None),
    ("initial_stock", "quantity", "ge", 0.0, None),
    ("demand", "quantity", "gt", 0.0, None),
    ("material_arrival", "quantity", "gt", 0.0, None),
    ("material_arrival", "eta_shift", "ge", 0.0, None),
    ("setup_matrix", "changeover_min", "ge", 0.0, None),
    ("tooling_mold", "available_sets", "ge", 1.0, None),
    ("preventive_maintenance", "downtime_hours", "ge", 0.0, None),
    ("preventive_maintenance", "capacity_pct", "ge_le", 0.0, 100.0),
    ("operation_leadtime", "prep_minutes", "ge", 0.0, None),
    ("operation_leadtime", "transfer_batch", "ge", 1.0, None),
)

_NULLABLE_FLOOR_RULES: tuple[tuple[str, str, float], ...] = (
    ("inventory_policy", "safety_stock", 0.0),
    ("inventory_policy", "max_stock", 0.0),
    ("mold", "available_sets", 1.0),
)


@dataclass
class CandidateRow:
    entity: str
    excel_row: int | None
    values: dict[str, Any] = dc_field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        return dict(self.values)


@dataclass(frozen=True)
class ValidationReport:
    errors: tuple[Issue, ...]
    warnings: tuple[Issue, ...]
    gaps: tuple[Issue, ...]

    @property
    def ok(self) -> bool:
        return not self.errors

    def counts(self) -> dict[str, int]:
        return {"error": len(self.errors), "warning": len(self.warnings),
                "gap": len(self.gaps)}

    def render(self, limit: int = 40) -> str:
        lines = [
            f"error={len(self.errors)}  warning={len(self.warnings)}  gap={len(self.gaps)}",
        ]
        for label, group in (("ERROR", self.errors), ("WARNING", self.warnings),
                             ("GAP", self.gaps)):
            for issue in group[:limit]:
                lines.append(
                    f"[{label}] {issue.entity or '-'}.{issue.field or '-'} "
                    f"@ {issue.sheet or '-'}"
                    + (f" R{issue.row}" if issue.row is not None else "")
                    + (f" C[{issue.column}]" if issue.column else "")
                    + f" :: {issue.message}"
                    + (f" (原值={issue.value!r})" if issue.value is not None else "")
                )
            if len(group) > limit:
                lines.append(f"[{label}] ... 另有 {len(group) - limit} 条未显示")
        return "\n".join(lines)


def _apply_parser(field: FieldSpec, value: Any) -> Any:
    parser = field.resolved_parser
    if parser == "enum_code":
        return parse_enum_code(value)
    if parser == "shift_index":
        index = parse_shift_index(value)
        if index is None:
            raise ParseError(f"无法解析班次: {value!r}", value)
        return index
    if parser == "stage":
        text = parse_scalar(value, DataType.TEXT)
        if text is None:
            return None
        return STAGE_NORMALIZE.get(text.upper(), text.upper())
    return parse_scalar(value, field.dtype)


def _source_column(spec: EntitySpec, field: FieldSpec) -> str | None:
    return field.source_columns[0] if field.source_columns else None


def gate_types(tables: RawTables, log: IssueLog) -> ParsedTables:
    parsed: ParsedTables = {}
    for entity in sorted(REGISTRY):
        spec = REGISTRY[entity]
        raws = tables.get(entity, [])
        rows: list[CandidateRow] = []
        for raw_row in raws:
            values: dict[str, Any] = {}
            row_failed = False
            for field in spec.fields:
                if field.name not in raw_row.values and field.provenance == "derived" \
                        and not field.pk:
                    values[field.name] = None
                    continue
                if field.name not in raw_row.values:
                    if field.nullable:
                        values[field.name] = None
                        continue
                    log.error(entity=entity, field=field.name, sheet=raw_row.sheet,
                              row=raw_row.excel_row,
                              column=_source_column(spec, field),
                              message="缺少必需值（T1，非空字段）",
                              value=None)
                    row_failed = True
                    continue
                cell = raw_row.values[field.name]
                try:
                    value = _apply_parser(field, cell)
                except ParseError as exc:
                    log.error(entity=entity, field=field.name, sheet=raw_row.sheet,
                              row=raw_row.excel_row,
                              column=_source_column(spec, field),
                              message=f"类型解析失败: {exc}", value=cell)
                    row_failed = True
                    continue
                if value is None and not field.nullable:
                    log.error(entity=entity, field=field.name, sheet=raw_row.sheet,
                              row=raw_row.excel_row,
                              column=_source_column(spec, field),
                              message="非空字段解析结果为空", value=cell)
                    row_failed = True
                    continue
                if value is not None and field.enum_codes is not None \
                        and value not in field.enum_codes:
                    log.error(entity=entity, field=field.name, sheet=raw_row.sheet,
                              row=raw_row.excel_row,
                              column=_source_column(spec, field),
                              message=f"取值不在允许集合 {field.enum_codes} 内", value=value)
                    row_failed = True
                    continue
                values[field.name] = value
            if not row_failed:
                rows.append(CandidateRow(entity, raw_row.excel_row, values))
        parsed[entity] = rows
    detect_gaps(parsed, log)
    return parsed


def detect_gaps(parsed: ParsedTables, log: IssueLog) -> None:
    for entity in sorted(parsed):
        spec = REGISTRY[entity]
        if spec.sheet is None:
            continue
        rows = parsed[entity]
        if not rows:
            continue
        for field in spec.fields:
            if field.tier is not ContractTier.T2:
                continue
            if any(row.values.get(field.name) is not None for row in rows):
                continue
            log.gap(entity=entity, field=field.name, sheet=spec.sheet,
                    column=_source_column(spec, field),
                    message=f"T2 字段整列为空（{len(rows)} 行），依赖它的规则将标记 degraded")


def _fk_value_sets(parsed: ParsedTables) -> dict[str, set[Any]]:
    out: dict[str, set[Any]] = {}
    for entity, rows in parsed.items():
        spec = REGISTRY[entity]
        for field in spec.fields:
            if not field.pk:
                continue
            bucket = out.setdefault(f"{entity}.{field.name}", set())
            for row in rows:
                value = row.values.get(field.name)
                if value is not None:
                    bucket.add(value)
    return out


def gate_references(parsed: ParsedTables, log: IssueLog) -> None:
    targets = _fk_value_sets(parsed)
    for entity in sorted(parsed):
        spec = REGISTRY[entity]
        for field in spec.fields:
            if not field.fk:
                continue
            allowed = targets.get(field.fk, set())
            for row in parsed[entity]:
                value = row.values.get(field.name)
                if value is None or value in allowed:
                    continue
                severity = Severity.WARNING if field.fk_optional else Severity.ERROR
                message = (f"外键 {field.fk} 无对应记录"
                           + ("（可选引用，降级为 warning）" if field.fk_optional else ""))
                log.emit(severity, entity, field.name, spec.sheet, row.excel_row,
                         _source_column(spec, field), message, value)


def _check_range(kind: str, value: float, low: float | None, high: float | None) -> bool:
    if kind == "gt":
        return value > low  # type: ignore[operator]
    if kind == "ge":
        return value >= low  # type: ignore[operator]
    if kind == "gt_le":
        return low < value <= high  # type: ignore[operator]
    if kind == "ge_lt":
        return low <= value < high  # type: ignore[operator]
    if kind == "ge_le":
        return low <= value <= high  # type: ignore[operator]
    raise ValueError(f"未知取值域规则 {kind!r}")


def gate_business(parsed: ParsedTables, log: IssueLog) -> None:
    for entity, field_name, kind, low, high in _RANGE_RULES:
        spec = REGISTRY[entity]
        field = spec.field(field_name)
        for row in parsed.get(entity, ()):
            value = row.values.get(field_name)
            if value is None:
                if not field.nullable:
                    log.error(entity=entity, field=field_name, sheet=spec.sheet,
                              row=row.excel_row, column=_source_column(spec, field),
                              message="非空字段无值，无法校验取值域")
                continue
            if not _check_range(kind, float(value), low, high):
                if entity == "initial_stock" and field_name == "quantity" and float(value) < 0:
                    log.warning(
                        entity=entity, field=field_name, sheet=spec.sheet,
                        row=row.excel_row, column=_source_column(spec, field),
                        message="期初可用库存为负数（ERP 账面差异），已安全置为 0 件",
                        value=value,
                    )
                    row.values[field_name] = 0.0
                    continue
                log.error(entity=entity, field=field_name, sheet=spec.sheet,
                          row=row.excel_row, column=_source_column(spec, field),
                          message=f"取值域违规: 期望 {kind}({low}, {high})", value=value)

    for entity, field_name, low in _NULLABLE_FLOOR_RULES:
        spec = REGISTRY[entity]
        field = spec.field(field_name)
        for row in parsed.get(entity, ()):
            value = row.values.get(field_name)
            if value is None:
                continue
            if float(value) < low:
                log.error(entity=entity, field=field_name, sheet=spec.sheet,
                          row=row.excel_row, column=_source_column(spec, field),
                          message=f"取值域违规: 期望 >= {low}", value=value)

    _check_date_order(parsed, log)
    _check_inventory_day_range(parsed, log)
    _check_family_consistency(parsed, log)
    resolve_duplicates(parsed, log)


def _check_date_order(parsed: ParsedTables, log: IssueLog) -> None:
    spec = REGISTRY["routing"]
    for row in parsed.get("routing", ()):
        start = row.values.get("valid_from")
        end = row.values.get("valid_to")
        if start is None or end is None:
            continue
        if not start < end:
            log.error(entity="routing", field="valid_to", sheet=spec.sheet,
                      row=row.excel_row, column="失效日期",
                      message=f"生效日期 {start} 未早于失效日期 {end}", value=end)


def _check_inventory_day_range(parsed: ParsedTables, log: IssueLog) -> None:
    spec = REGISTRY["inventory_policy"]
    for row in parsed.get("inventory_policy", ()):
        start = row.values.get("start_day")
        end = row.values.get("end_day")
        if start is None or end is None:
            continue
        if start > end:
            log.error(entity="inventory_policy", field="end_day", sheet=spec.sheet,
                      row=row.excel_row, column="结束日 (Day)",
                      message=f"起始日 {start} 晚于结束日 {end}", value=end)


def _check_family_consistency(parsed: ParsedTables, log: IssueLog) -> None:
    families = {
        row.values.get("pn"): row.values.get("family_code")
        for row in parsed.get("part", ())
    }
    spec = REGISTRY["routing"]
    for row in parsed.get("routing", ()):
        declared = row.values.get("family_code")
        if declared is None:
            continue
        authoritative = families.get(row.values.get("pn"))
        if authoritative is None or authoritative == declared:
            continue
        log.warning(entity="routing", field="family_code", sheet=spec.sheet,
                    row=row.excel_row, column="产品族名",
                    message=f"产品族 {declared!r} 与 part.family_code "
                            f"{authoritative!r} 不一致，以 part 为准",
                    value=declared)


def _sort_key(spec: EntitySpec, row: CandidateRow) -> tuple:
    return tuple(_sortable(row.values.get(name)) for name in spec.pk)


def _sortable(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:030.10f}"
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, (tuple, list)):
        return ",".join(str(v) for v in value)
    return str(value)


def resolve_duplicates(parsed: ParsedTables, log: IssueLog) -> ParsedTables:
    out: ParsedTables = {}
    for entity in sorted(parsed):
        spec = REGISTRY[entity]
        grouped: dict[tuple, list[CandidateRow]] = defaultdict(list)
        for row in parsed[entity]:
            grouped[tuple(row.values.get(name) for name in spec.pk)].append(row)
        resolved: list[CandidateRow] = []
        for key in grouped:
            rows = grouped[key]
            if len(rows) == 1:
                resolved.append(rows[0])
                continue
            handler = _resolve_group(spec, key, rows, log)
            if handler is not None:
                resolved.append(handler)
        resolved.sort(key=lambda r: _sort_key(spec, r))
        out[entity] = resolved
    parsed.clear()
    parsed.update(out)
    return parsed


def _non_pk_differences(spec: EntitySpec, rows: list[CandidateRow]) -> list[str]:
    differing: list[str] = []
    for field in spec.fields:
        if field.name in spec.pk:
            continue
        values = {_sortable(row.values.get(field.name)) for row in rows}
        if len(values) > 1:
            differing.append(field.name)
    return differing


def _rows_label(rows: list[CandidateRow]) -> str:
    return ", ".join(str(r.excel_row) for r in rows if r.excel_row is not None)


def _resolve_group(spec: EntitySpec, key: tuple, rows: list[CandidateRow],
                   log: IssueLog) -> CandidateRow | None:
    if spec.dup_policy is DupPolicy.ERROR:
        log.error(entity=spec.name, sheet=spec.sheet, row=rows[0].excel_row,
                  message=f"主键 {key} 重复出现 {len(rows)} 次（Excel 行 "
                          f"{_rows_label(rows)}），该实体不允许重复")
        return rows[0]

    if spec.dup_policy is DupPolicy.DEDUPE:
        differing = _non_pk_differences(spec, rows)
        if differing:
            log.error(entity=spec.name, sheet=spec.sheet, row=rows[0].excel_row,
                      message=f"主键 {key} 重复且字段 {differing} 取值不一致"
                              f"（Excel 行 {_rows_label(rows)}），无法裁决")
            return rows[0]
        log.warning(entity=spec.name, sheet=spec.sheet, row=rows[0].excel_row,
                    message=f"主键 {key} 完全重复 {len(rows)} 次"
                            f"（Excel 行 {_rows_label(rows)}），已去重保留首行")
        return rows[0]

    merged = CandidateRow(spec.name, rows[0].excel_row, dict(rows[0].values))
    summed: dict[str, float] = {}
    for field_name in spec.sum_fields:
        total = 0
        for row in rows:
            value = row.values.get(field_name)
            total += 0 if value is None else value
        summed[field_name] = total
        merged.values[field_name] = total
    log.warning(entity=spec.name, sheet=spec.sheet, row=rows[0].excel_row,
                message=f"主键 {key} 重复 {len(rows)} 次（Excel 行 {_rows_label(rows)}），"
                        f"已按 {sorted(summed)} 求和: "
                        + ", ".join(f"{k}={v}" for k, v in sorted(summed.items())))
    return merged


def validate(tables: RawTables, structure_log: IssueLog) -> tuple[ParsedTables, ValidationReport]:
    log = IssueLog(structure_log.issues)
    parsed = gate_types(tables, log)
    gate_references(parsed, log)
    gate_business(parsed, log)
    report = ValidationReport(errors=log.errors, warnings=log.warnings, gaps=log.gaps)
    return parsed, report
