"""Build solver-side master tables from the new workbook rows.

The 2026-10 framework keeps Routing / Setup / Inventory-Policy /
Operation-LeadTime inside the uploaded workbook.  These helpers convert the
canonical row dicts (``src.excel_loader.read_master_rows``) into the dataclasses
the scheduler already understands, so an upload actually drives the optional
routing / setup / inventory / lead-time features instead of silently falling
back to the checked-in CSVs.

Each ``*_from_rows`` refuses to guess: when the sheet is missing or empty it
returns ``None`` so the caller can fall back to the CSV baseline.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from src.inventory_policy import InventoryPolicy
from src.leadtime import OperationLeadTime
from src.routing import RoutingOption
from src.sequence import SetupEdge


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def routing_from_rows(
    rows: Optional[List[dict]],
    site: str,
    line_trp: Optional[Dict[str, float]] = None,
) -> Optional[Dict[Tuple[str, str, int], List[RoutingOption]]]:
    if not rows:
        return None
    out: Dict[Tuple[str, str, int], List[RoutingOption]] = {}
    for row in rows:
        row_site = _text(row.get("site")) or site
        if row_site != site:
            continue
        part = _text(row.get("part_no"))
        if not part:
            continue
        op = _int(row.get("op_seq"), 0)
        primary = _text(row.get("primary_line"))
        # A part on several lines is expressed as several rows with different
        # 主候选线; 备选候选线/机台 are legacy columns and are no longer read.
        candidate = _text(row.get("candidate_line")) or primary
        # TRP is a line-level attribute (产线基础数据); the legacy per-row
        # Routing TRP column was removed from the template.
        trp = _num(row.get("trp"), 0.0) or float((line_trp or {}).get(candidate, 0.0)) or 1.0
        option = RoutingOption(
            site=row_site,
            part_no=part,
            stage=_text(row.get("stage")),
            operation_seq=op,
            operation_name=_text(row.get("op_name")),
            primary_line=primary,
            candidate_line=candidate,
            machine_id=_text(row.get("machine_id")),
            certified=bool(row.get("certified")),
            ct_seconds=_num(row.get("ct_seconds")),
            trp=trp,
            setup_min=_num(row.get("setup_min")),
            priority=_int(row.get("priority"), 1),
            effective_from=_text(row.get("effective_from")),
            effective_to=_text(row.get("effective_to")),
        )
        out.setdefault((row_site, part, op), []).append(option)
    for options in out.values():
        options.sort(key=lambda item: (item.priority, item.candidate_line, item.machine_id))
    return out or None


def setup_matrix_from_rows(rows: Optional[List[dict]], site: str) -> Optional[Dict[Tuple[str, str, str, str], SetupEdge]]:
    if not rows:
        return None
    matrix: Dict[Tuple[str, str, str, str], SetupEdge] = {}
    for row in rows:
        row_site = _text(row.get("site")) or site
        if row_site != site:
            continue
        line = _text(row.get("line"))
        frm = _text(row.get("from_mold"))
        to = _text(row.get("to_mold"))
        if not (line and frm and to):
            continue
        matrix[(row_site, line, frm, to)] = SetupEdge(
            site=row_site,
            line=line,
            from_mold=frm,
            to_mold=to,
            minutes=_num(row.get("setup_minutes")),
            technicians=_int(row.get("technicians"), 1) or 1,
        )
    return matrix or None


def inventory_policy_from_rows(rows: Optional[List[dict]], site: str) -> Optional[Dict[Tuple[str, str, str], InventoryPolicy]]:
    if not rows:
        return None
    out: Dict[Tuple[str, str, str], InventoryPolicy] = {}
    for row in rows:
        row_site = _text(row.get("site")) or site
        if row_site != site:
            continue
        part = _text(row.get("part_no"))
        if not part:
            continue
        period = (_text(row.get("period_type")) or "NORMAL").upper()
        out[(row_site, part, period)] = InventoryPolicy(
            site=row_site,
            part_no=part,
            period_type=period,
            from_day=_int(row.get("from_day"), 1) or 1,
            to_day=_int(row.get("to_day"), 31) or 31,
            target_days=_num(row.get("target_days")),
            max_days=_num(row.get("max_days")),
            safety_stock=_num(row.get("safety_stock")),
            max_stock=_num(row.get("max_stock")),
            effective_from=_text(row.get("effective_from")),
        )
    return out or None


def operation_lead_times_from_rows(rows: Optional[List[dict]], site: str) -> Optional[Dict[Tuple[str, str, int], OperationLeadTime]]:
    if not rows:
        return None
    out: Dict[Tuple[str, str, int], OperationLeadTime] = {}
    for row in rows:
        row_site = _text(row.get("site")) or site
        if row_site != site:
            continue
        part = _text(row.get("part_no"))
        if not part:
            continue
        op = _int(row.get("op_seq"), 0)
        # 新版只保留“提前准备时间/转运批量/容器”：准备时间进入 setup_min；
        # 运行节拍回退到 Routing 的 CT，旧版的排队/检验/搬运时间视为 0。
        out[(row_site, part, op)] = OperationLeadTime(
            site=row_site,
            part_no=part,
            stage=_text(row.get("stage")),
            operation_seq=op,
            operation_name=_text(row.get("op_name")),
            setup_min=_num(row.get("prep_minutes")),
            run_ct_seconds=_num(row.get("run_ct_seconds")),
            queue_min=_num(row.get("queue_min")),
            inspection_min=_num(row.get("inspection_min")),
            transfer_batch=_num(row.get("transfer_batch")),
            move_min=_num(row.get("move_min")),
        )
    return out or None
