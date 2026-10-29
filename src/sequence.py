"""Intra-shift sequencing primitives for the Phase C time/sequence model.

This module is intentionally side-effect free: it turns a set of already-decided
shift quantities into a recommended product order and prices sequence-dependent
setup time. It does not yet feed back into the CP-SAT quantities; that is the
next C1 integration step.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class SetupEdge:
    site: str
    line: str
    from_mold: str
    to_mold: str
    minutes: float
    technicians: int = 1


@dataclass
class SequenceSegment:
    task_id: str
    line: str
    part_no: str
    mold_id: str
    qty: float
    shift_key: str
    due_rank: float = 0.0
    processing_minutes: float = 0.0


@dataclass
class SequenceStep:
    shift_key: str
    line: str
    sequence: int
    task_id: str
    part_no: str
    mold_id: str
    qty: float
    setup_minutes: float


@dataclass
class SequencePlan:
    steps: List[SequenceStep] = field(default_factory=list)
    setup_minutes_by_shift: Dict[str, float] = field(default_factory=dict)
    production_minutes_by_shift: Dict[str, float] = field(default_factory=dict)
    total_minutes_by_shift: Dict[str, float] = field(default_factory=dict)
    overload_shifts: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def load_setup_matrix(path: str | Path) -> Dict[Tuple[str, str, str, str], SetupEdge]:
    """Read data/master/Setup-Matrix.csv into a (site,line,from,to) lookup."""
    matrix: Dict[Tuple[str, str, str, str], SetupEdge] = {}
    p = Path(path)
    if not p.exists():
        return matrix
    with p.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            site = (row.get("Site") or "").strip()
            line = (row.get("Line") or "").strip()
            frm = (row.get("From Mold") or "").strip()
            to = (row.get("To Mold") or "").strip()
            if not (site and line and frm and to):
                continue
            try:
                minutes = float(row.get("Setup Minutes") or 0.0)
            except ValueError:
                minutes = 0.0
            try:
                techs = int(float(row.get("Technicians Required") or 1))
            except ValueError:
                techs = 1
            matrix[(site, line, frm, to)] = SetupEdge(site, line, frm, to, minutes, techs)
    return matrix


def _edge_minutes(
    matrix: Dict[Tuple[str, str, str, str], SetupEdge],
    site: str,
    line: str,
    frm: str,
    to: str,
    default_minutes: float,
) -> Tuple[float, int]:
    if not frm or not to or frm == to:
        return 0.0, 1
    edge = matrix.get((site, line, frm, to))
    if edge is None:
        edge = matrix.get((site, line, to, frm))
    if edge is None:
        return default_minutes, 1
    return edge.minutes, edge.technicians


def _order_edges(
    segments: Sequence[SequenceSegment],
    matrix: Dict[Tuple[str, str, str, str], SetupEdge],
    site: str,
    prev_mold: str,
    default_minutes: float,
) -> Tuple[List[SequenceSegment], List[float], List[int]]:
    """Exact DP for small n; nearest-neighbour fallback for larger n."""
    items = list(segments)
    if not items:
        return [], [], []
    n = len(items)
    if n > 10:
        ordered: List[SequenceSegment] = []
        remaining = items[:]
        last = prev_mold
        setups: List[float] = []
        techs: List[int] = []
        while remaining:
            best = min(
                remaining,
                key=lambda s: (
                    _edge_minutes(matrix, site, s.line, last, s.mold_id, default_minutes)[0],
                    s.due_rank,
                    s.task_id,
                ),
            )
            minutes, technicians = _edge_minutes(matrix, site, best.line, last, best.mold_id, default_minutes)
            ordered.append(best)
            setups.append(minutes)
            techs.append(technicians)
            last = best.mold_id
            remaining.remove(best)
        return ordered, setups, techs

    # dp[mask][last] = minimum setup; parent for reconstruction
    size = 1 << n
    inf = float("inf")
    dp = [[inf] * n for _ in range(size)]
    parent = [[-1] * n for _ in range(size)]
    for i, item in enumerate(items):
        dp[1 << i][i] = _edge_minutes(matrix, site, item.line, prev_mold, item.mold_id, default_minutes)[0]
    for mask in range(size):
        for last in range(n):
            if dp[mask][last] == inf:
                continue
            for nxt in range(n):
                if mask & (1 << nxt):
                    continue
                minutes = _edge_minutes(
                    matrix, site, items[nxt].line, items[last].mold_id, items[nxt].mold_id, default_minutes
                )[0]
                new_mask = mask | (1 << nxt)
                value = dp[mask][last] + minutes
                if value < dp[new_mask][nxt]:
                    dp[new_mask][nxt] = value
                    parent[new_mask][nxt] = last
    full = size - 1
    last = min(range(n), key=lambda i: dp[full][i])
    order_idx: List[int] = []
    mask = full
    while last != -1:
        order_idx.append(last)
        prev = parent[mask][last]
        mask ^= 1 << last
        last = prev
    order_idx.reverse()
    ordered = [items[i] for i in order_idx]
    setups: List[float] = []
    techs: List[int] = []
    last_mold = prev_mold
    for item in ordered:
        minutes, technicians = _edge_minutes(matrix, site, item.line, last_mold, item.mold_id, default_minutes)
        setups.append(minutes)
        techs.append(technicians)
        last_mold = item.mold_id
    return ordered, setups, techs


def sequence_segments(
    segments_by_shift: Dict[str, List[SequenceSegment]],
    matrix: Dict[Tuple[str, str, str, str], SetupEdge],
    *,
    site: str,
    line: str,
    default_minutes: float = 60.0,
    capacity_minutes_by_shift: Optional[Dict[str, float]] = None,
) -> SequencePlan:
    """Sequence every shift for one line, carrying the mold across shifts."""
    plan = SequencePlan()
    previous_mold = ""
    for shift_key in sorted(segments_by_shift):
        segments = [s for s in segments_by_shift[shift_key] if s.qty > 0]
        ordered, setups, _techs = _order_edges(segments, matrix, site, previous_mold, default_minutes)
        total = 0.0
        for index, (segment, minutes) in enumerate(zip(ordered, setups), start=1):
            total += minutes
            plan.steps.append(
                SequenceStep(
                    shift_key=shift_key,
                    line=line,
                    sequence=index,
                    task_id=segment.task_id,
                    part_no=segment.part_no,
                    mold_id=segment.mold_id,
                    qty=segment.qty,
                    setup_minutes=minutes,
                )
            )
        if ordered:
            previous_mold = ordered[-1].mold_id
        plan.setup_minutes_by_shift[shift_key] = total
        production = sum(seg.processing_minutes for seg in ordered)
        plan.production_minutes_by_shift[shift_key] = production
        plan.total_minutes_by_shift[shift_key] = production + total
        if capacity_minutes_by_shift and shift_key in capacity_minutes_by_shift:
            if production + total > capacity_minutes_by_shift[shift_key] + 1e-6:
                plan.overload_shifts.append(shift_key)
    return plan


def segments_from_schedule(result, index_of: Dict[Tuple[str, str], int]) -> Dict[str, Dict[str, List[SequenceSegment]]]:
    """Convert a ScheduleResult into {line: {shift_key: [segments]}}."""
    by_line: Dict[str, Dict[str, List[SequenceSegment]]] = {}
    for task in getattr(result, "tasks", []):
        for (date_str, shift_name), qty in getattr(task, "shift_breakdown", {}).items():
            if qty <= 0:
                continue
            key = f"{date_str} {shift_name}"
            by_line.setdefault(task.line, {}).setdefault(key, []).append(
                SequenceSegment(
                    task_id=task.task_id,
                    line=task.line,
                    part_no=task.part_no,
                    mold_id=str(getattr(task, "mold_id", "") or task.part_no),
                    qty=float(qty),
                    shift_key=key,
                    due_rank=float(index_of.get((date_str, shift_name), 0)),
                    processing_minutes=float(getattr(task, "shift_seconds", {}).get((date_str, shift_name), 0.0)) / 60.0,
                )
            )
    return by_line
