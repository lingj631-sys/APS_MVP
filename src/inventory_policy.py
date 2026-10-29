"""B4-a time-phased inventory policy data layer.

The CP-SAT model does not consume these rows yet; this module makes the policy
table queryable and validated so the inventory operator can be wired next.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class InventoryPolicy:
    site: str
    part_no: str
    period_type: str
    from_day: int
    to_day: int
    target_days: float
    max_days: float
    safety_stock: float
    max_stock: float
    effective_from: str = ""


def load_inventory_policy(path: str | Path, site: Optional[str] = None) -> Dict[Tuple[str, str, str], InventoryPolicy]:
    """Keyed by (site, part, period). Period is NORMAL or MONTH_END."""
    out: Dict[Tuple[str, str, str], InventoryPolicy] = {}
    p = Path(path)
    if not p.exists():
        return out
    with p.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            row_site = (row.get("Site") or "").strip()
            part = (row.get("Part No.") or "").strip()
            if not row_site or not part or (site and row_site != site):
                continue
            period = (row.get("Period Type") or "NORMAL").strip().upper()
            try:
                item = InventoryPolicy(
                    site=row_site,
                    part_no=part,
                    period_type=period,
                    from_day=int(float(row.get("From Day") or 1)),
                    to_day=int(float(row.get("To Day") or 31)),
                    target_days=float(row.get("Target Days") or 0.0),
                    max_days=float(row.get("Max Days") or 0.0),
                    safety_stock=float(row.get("Safety Stock") or 0.0),
                    max_stock=float(row.get("Max Stock") or 0.0),
                    effective_from=(row.get("Effective From") or "").strip(),
                )
            except ValueError:
                continue
            out[(row_site, part, period)] = item
    return out


def policy_for_day(policies: Dict[Tuple[str, str, str], InventoryPolicy], site: str, part_no: str, day: int) -> Optional[InventoryPolicy]:
    normal = policies.get((site, part_no, "NORMAL"))
    month_end = policies.get((site, part_no, "MONTH_END"))
    if month_end and month_end.from_day <= day <= month_end.to_day:
        return month_end
    if normal and normal.from_day <= day <= normal.to_day:
        return normal
    return month_end or normal


@dataclass
class InventoryPoint:
    part_no: str
    shift_key: str
    day: int
    production: float
    demand: float
    inventory: float
    policy: Optional[InventoryPolicy]
    violation: bool = False


@dataclass
class InventoryProjection:
    points: List[InventoryPoint]
    violations: List[str]
    metrics: Dict[str, float]


def project_inventory(
    tasks: Iterable[Any],
    snapshot: Any,
    shifts: Iterable[Any],
    policies: Dict[Tuple[str, str, str], InventoryPolicy],
    *,
    site: Optional[str] = None,
) -> InventoryProjection:
    """B4-b-a: forward project FG/SF inventory and compare with policy rows.

    This is an audit/projection, not a CP-SAT constraint. Demand is placed on the
    last shift of the order due date; SF demand is derived from the FG BOM.
    """
    shifts = list(shifts)
    if not shifts:
        return InventoryProjection([], [], {"inventory_healthy_rate_pct": 100.0})
    site = site or str(getattr(shifts[0], "site", "") or "")
    index_of = {(getattr(s, "date_str", ""), getattr(s, "shift_name", "")): i for i, s in enumerate(shifts)}
    start_date = min(getattr(s, "start_dt").date() for s in shifts)
    total_days = max(1, (max(getattr(s, "start_dt").date() for s in shifts) - start_date).days + 1)

    production: Dict[Tuple[str, int], float] = {}
    for task in tasks:
        part = str(getattr(task, "part_no", ""))
        for (date_str, shift_name), qty in getattr(task, "shift_breakdown", {}).items():
            i = index_of.get((date_str, shift_name))
            if i is not None and qty:
                production[(part, i)] = production.get((part, i), 0.0) + float(qty)

    demand: Dict[Tuple[str, int], float] = {}
    for order in getattr(snapshot, "orders", []):
        due = getattr(order, "due_datetime", None)
        if due is None:
            continue
        day_keys = [i for (d, _s), i in index_of.items() if d == f"{due.month}/{due.day}"]
        if not day_keys:
            continue
        i = max(day_keys)
        pn = str(order.part_no)
        demand[(pn, i)] = demand.get((pn, i), 0.0) + float(order.demand_quantity)
        for sf_pn, ratio in getattr(snapshot, "bom", {}).get(pn, []):
            demand[(sf_pn, i)] = demand.get((sf_pn, i), 0.0) + float(order.demand_quantity) * float(ratio)

    opening = {str(k): float(v) for k, v in getattr(snapshot, "initial_stock", {}).items()}
    parts = sorted({part for part, _i in production} | {part for part, _i in demand} | set(opening))
    points: List[InventoryPoint] = []
    violations: List[str] = []
    balanced = 0
    for part in parts:
        stock = opening.get(part, 0.0)
        total_consumption = sum(value for (p, _i), value in demand.items() if p == part)
        daily = total_consumption / total_days if total_consumption > 0 else 0.0
        for i, shift in enumerate(shifts):
            produced = production.get((part, i), 0.0)
            consumed = demand.get((part, i), 0.0)
            stock = stock + produced - consumed
            day = (getattr(shift, "start_dt").date() - start_date).days + 1
            policy = policy_for_day(policies, site, part, day)
            bad = False
            if policy and daily > 0:
                ceiling = policy.max_days * daily + max(0.0, policy.max_stock)
                if stock > ceiling + 1e-6:
                    bad = True
                    violations.append(f"{part} @ {getattr(shift, 'date_str', '')} {getattr(shift, 'shift_name', '')}: 库存 {stock:.0f} 超过上限 {ceiling:.0f}")
            points.append(InventoryPoint(
                part_no=part,
                shift_key=f"{getattr(shift, 'date_str', '')} {getattr(shift, 'shift_name', '')}",
                day=day,
                production=produced,
                demand=consumed,
                inventory=stock,
                policy=policy,
                violation=bad,
            ))
        if not any(p.violation for p in points if p.part_no == part):
            balanced += 1
    rate = 100.0 * balanced / max(1, len(parts))
    return InventoryProjection(
        points=points,
        violations=violations,
        metrics={
            "monitored_parts": len(parts),
            "violating_parts": len({v.split(" @ ")[0] for v in violations}),
            "inventory_healthy_rate_pct": round(rate, 1),
        },
    )
