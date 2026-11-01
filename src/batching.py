"""BS-05 SuperOrder grouping layer (no order identity change).

This module groups discrete orders that share a mold (and part) inside a
lookahead window into a ``CampaignBatch``.  It deliberately keeps the original
production tasks: the batch is a planning object used by BS-05/BS-06/HC-16,
not a replacement for the orders (which would break audit identity).
"""
from __future__ import annotations

import copy

from dataclasses import dataclass, field
from datetime import datetime
from math import ceil
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class CampaignBatch:
    batch_id: str
    site: str
    key: str
    mold_id: str
    campaign_group: str
    part_no: str
    task_ids: Tuple[str, ...]
    order_ids: Tuple[str, ...]
    total_quantity: float
    earliest_due: Optional[datetime]
    latest_due: Optional[datetime]
    lookahead_days: int
    min_moq: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "site": self.site,
            "key": self.key,
            "mold_id": self.mold_id,
            "campaign_group": self.campaign_group,
            "part_no": self.part_no,
            "task_ids": list(self.task_ids),
            "order_ids": list(self.order_ids),
            "task_count": len(self.task_ids),
            "total_quantity": self.total_quantity,
            "earliest_due": self.earliest_due.isoformat() if self.earliest_due else None,
            "latest_due": self.latest_due.isoformat() if self.latest_due else None,
            "lookahead_days": self.lookahead_days,
            "min_moq": self.min_moq,
        }


def build_campaign_batches(
    tasks: Iterable[Any],
    campaign_families: Dict[str, Any],
    *,
    site: str,
    lookahead_days: int = 30,
    min_tasks: int = 2,
) -> List[CampaignBatch]:
    """Group same-mold/same-part orders into lookahead-window batches.

    A batch spans at most ``lookahead_days`` from its earliest order due date;
    orders outside the window start a new batch.  Batches with fewer than
    ``min_tasks`` orders are not returned (nothing to combine).
    """
    groups: Dict[Tuple[str, str], List[Any]] = {}
    for task in tasks:
        due = getattr(task, "due_datetime", None)
        if due is None:
            continue
        mold = str(getattr(task, "mold_id", "") or "")
        family = campaign_families.get(str(getattr(task, "part_no", "")))
        group = str(getattr(family, "campaign_group", "") or "") if family else ""
        if not mold and not group:
            continue
        key = (mold or group, str(getattr(task, "part_no", "")))
        groups.setdefault(key, []).append(task)

    batches: List[CampaignBatch] = []
    for (mold_or_group, part_no), items in sorted(groups.items()):
        items = sorted(items, key=lambda t: (t.due_datetime, t.task_id))
        chunk: List[Any] = []
        chunks: List[List[Any]] = []
        for task in items:
            if chunk and (task.due_datetime - chunk[0].due_datetime).days > lookahead_days:
                chunks.append(chunk)
                chunk = []
            chunk.append(task)
        if chunk:
            chunks.append(chunk)

        for index, members in enumerate(chunks):
            if len(members) < min_tasks:
                continue
            mold = str(getattr(members[0], "mold_id", "") or "")
            family = campaign_families.get(str(getattr(members[0], "part_no", "")))
            campaign_group = str(getattr(family, "campaign_group", "") or "") if family else ""
            earliest = min(t.due_datetime for t in members)
            latest = max(t.due_datetime for t in members)
            batch_id = (
                f"CB::{site}::{mold_or_group}::{part_no}::"
                f"{earliest.strftime('%Y%m%d')}::{index}"
            )
            batches.append(
                CampaignBatch(
                    batch_id=batch_id,
                    site=site,
                    key=mold_or_group,
                    mold_id=mold,
                    campaign_group=campaign_group,
                    part_no=part_no,
                    task_ids=tuple(t.task_id for t in members),
                    order_ids=tuple(dict.fromkeys(str(getattr(t, "order_id", "")) for t in members)),
                    total_quantity=sum(float(getattr(t, "quantity", 0.0) or 0.0) for t in members),
                    earliest_due=earliest,
                    latest_due=latest,
                    lookahead_days=int(lookahead_days),
                    min_moq=max(
                        int(getattr(t, "moq", 0) or getattr(t, "mpq", 0) or 0)
                        for t in members
                    ),
                )
            )
    return batches


def build_truncation_suggestions(
    result_tasks: Iterable[Any],
    batches: Iterable[CampaignBatch],
    *,
    max_suggestions: int = 3,
) -> List[Dict[str, Any]]:
    """BS-06-lite: propose which tail orders to defer from a campaign batch.

    The estimate uses the late order's overrun hours converted to pieces via
    CT/TRP, then defers the latest-finishing tasks of a same-line batch until the
    estimate is covered.  This is a planner-facing suggestion; it does not alter
    the schedule.  ``feasible`` is false when preserving MOQ is impossible.
    """
    task_by_id = {t.task_id: t for t in result_tasks}
    batches = list(batches)
    late = [
        t for t in task_by_id.values()
        if getattr(t, "due_datetime", None) is not None
        and getattr(t, "end_dt", None) is not None
        and t.end_dt > t.due_datetime
    ]
    suggestions: List[Dict[str, Any]] = []
    used_batches = set()
    for late_task in sorted(late, key=lambda t: (t.due_datetime, t.task_id)):
        if len(suggestions) >= max_suggestions:
            break
        overrun_hours = (late_task.end_dt - late_task.due_datetime).total_seconds() / 3600.0
        ct = float(getattr(late_task, "ct_seconds", 0.0) or 0.0)
        trp = float(getattr(late_task, "trp", 1.0) or 1.0)
        if ct <= 0 or trp <= 0:
            continue
        need_qty = max(1, ceil(overrun_hours * 3600.0 * trp / ct))
        candidates = []
        for batch in batches:
            if batch.batch_id in used_batches:
                continue
            members = [task_by_id[tid] for tid in batch.task_ids if tid in task_by_id]
            members = [t for t in members if getattr(t, "line", None) == getattr(late_task, "line", None)]
            if len(members) < 1:
                continue
            candidates.append((batch, members))
        if not candidates:
            continue
        batch, members = max(candidates, key=lambda pair: pair[0].total_quantity)
        members = sorted(members, key=lambda t: (t.end_dt, t.task_id), reverse=True)
        split_members = []
        split_qty = 0.0
        for member in members:
            if split_qty >= need_qty:
                break
            if member.task_id == late_task.task_id:
                continue
            split_members.append(member)
            split_qty += float(getattr(member, "quantity", 0.0) or 0.0)
        if not split_members:
            continue
        keep_qty = max(0.0, batch.total_quantity - split_qty)
        feasible = batch.min_moq <= 0 or keep_qty >= batch.min_moq
        split_ids = {t.task_id for t in split_members}
        suggestions.append(
            {
                "target_order_id": getattr(late_task, "order_id", ""),
                "target_slack_hours": round(-overrun_hours, 2),
                "overrun_qty": int(need_qty),
                "batch_id": batch.batch_id,
                "part_no": batch.part_no,
                "mold_id": batch.mold_id,
                "total_qty": batch.total_quantity,
                "keep_qty": round(keep_qty, 1),
                "split_qty": round(split_qty, 1),
                "min_moq": batch.min_moq,
                "keep_task_ids": [tid for tid in batch.task_ids if tid not in split_ids],
                "split_task_ids": [t.task_id for t in split_members],
                "split_order_ids": [getattr(t, "order_id", "") for t in split_members],
                "feasible": bool(feasible),
                "note": (
                    "保留量满足 MOQ，建议顺延尾单"
                    if feasible else
                    "保留量低于 MOQ，需人工确认是否整批顺延或允许低于 MOQ"
                ),
            }
        )
        used_batches.add(batch.batch_id)
    return suggestions


def select_deferred_orders(suggestions: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Turn feasible truncation suggestions into a deduplicated deferral list."""
    result: List[Dict[str, Any]] = []
    seen = set()
    for suggestion in suggestions:
        if not suggestion.get("feasible"):
            continue
        for order_id in suggestion.get("split_order_ids") or []:
            if not order_id or order_id in seen:
                continue
            seen.add(order_id)
            result.append(
                {
                    "order_id": order_id,
                    "batch_id": suggestion.get("batch_id"),
                    "qty": suggestion.get("split_qty"),
                    "target_order_id": suggestion.get("target_order_id"),
                    "reason": suggestion.get("note"),
                }
            )
    return result


def without_orders(snapshot: Any, order_ids: Iterable[str]) -> Any:
    """Return a shallow snapshot copy with the given orders removed (BS-06)."""
    clone = copy.copy(snapshot)
    ids = {str(order_id) for order_id in order_ids if order_id}
    clone.orders = [order for order in snapshot.orders if order.order_id not in ids]
    return clone


def truncation_improves(
    late_before: int,
    min_slack_before: float,
    late_after: int,
    min_slack_after: float,
) -> bool:
    """BS-06 guard: accept only fewer late orders or equal late with better slack."""
    if late_after < late_before:
        return True
    if late_after > late_before:
        return False
    return min_slack_after >= min_slack_before - 1e-6
