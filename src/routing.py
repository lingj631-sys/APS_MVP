"""C2-a data layer for routing / certified-machine candidates.

The scheduler still binds each task to one line; this module makes the routing
table usable first, so C2-b can add the assignment variables without reworking
the data model again.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class RoutingOption:
    site: str
    part_no: str
    stage: str
    operation_seq: int
    operation_name: str
    primary_line: str
    candidate_line: str
    machine_id: str
    certified: bool
    ct_seconds: float
    trp: float
    setup_min: float
    priority: int
    effective_from: str = ""
    effective_to: str = ""


def load_routing(path: str | Path) -> Dict[Tuple[str, str, int], List[RoutingOption]]:
    """Load data/master/Routing-Machines.csv keyed by (site, part, operation)."""
    out: Dict[Tuple[str, str, int], List[RoutingOption]] = {}
    p = Path(path)
    if not p.exists():
        return out
    with p.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            site = (row.get("Site") or "").strip()
            part = (row.get("Part No.") or "").strip()
            if not site or not part:
                continue
            try:
                op = int(float(row.get("Operation Seq") or 0))
            except ValueError:
                op = 0
            try:
                ct = float(row.get("CT Seconds") or 0.0)
                trp = float(row.get("TRP") or 1.0)
                setup = float(row.get("Setup Min") or 0.0)
                priority = int(float(row.get("Priority") or 1))
            except ValueError:
                continue
            option = RoutingOption(
                site=site,
                part_no=part,
                stage=(row.get("Stage") or "").strip(),
                operation_seq=op,
                operation_name=(row.get("Operation Name") or "").strip(),
                primary_line=(row.get("Primary Line") or "").strip(),
                candidate_line=(row.get("Candidate Line") or "").strip(),
                machine_id=(row.get("Machine ID") or "").strip(),
                certified=str(row.get("Certified") or "").strip().upper() in ("TRUE", "1", "YES", "Y"),
                ct_seconds=ct,
                trp=trp,
                setup_min=setup,
                priority=priority,
                effective_from=(row.get("Effective From") or "").strip(),
                effective_to=(row.get("Effective To") or "").strip(),
            )
            out.setdefault((site, part, op), []).append(option)
    for options in out.values():
        options.sort(key=lambda item: (item.priority, item.candidate_line, item.machine_id))
    return out


def candidates_for(
    routing: Dict[Tuple[str, str, int], List[RoutingOption]],
    site: str,
    part_no: str,
    operation_seq: Optional[int] = None,
) -> List[RoutingOption]:
    if operation_seq is not None:
        return list(routing.get((site, part_no, int(operation_seq)), []))
    options: List[RoutingOption] = []
    for (s, p, _op), rows in routing.items():
        if s == site and p == part_no:
            options.extend(rows)
    return sorted(options, key=lambda item: (item.operation_seq, item.priority, item.candidate_line))


def primary_line(routing: Dict[Tuple[str, str, int], List[RoutingOption]], site: str, part_no: str, operation_seq: int = 10) -> str:
    options = candidates_for(routing, site, part_no, operation_seq)
    return options[0].candidate_line if options else ""


def assign_routing_lines(
    tasks: Iterable[Any],
    routing: Dict[Tuple[str, str, int], List[RoutingOption]],
    *,
    site: str,
    capacity_hours_by_line: Optional[Dict[str, float]] = None,
    max_ratio: float = 0.95,
) -> List[Dict[str, Any]]:
    """C2-b-a heuristic: move a task to a lower-load certified candidate line.

    This is a preprocessor, not a CP-SAT assignment. It is intentionally opt-in
    until routing has its own assignment variables.
    """
    tasks = list(tasks)
    load: Dict[str, float] = {}
    for task in tasks:
        task_hours = float(
            getattr(task, "quantity", 0.0) * getattr(task, "ct_seconds", 0.0)
            / max(1e-9, getattr(task, "trp", 1.0)) / 3600.0
        )
        load[str(getattr(task, "line", ""))] = load.get(str(getattr(task, "line", "")), 0.0) + task_hours
    decisions: List[Dict[str, Any]] = []
    for task in tasks:
        part = str(getattr(task, "part_no", ""))
        stage = str(getattr(task, "stage", ""))
        operation = 10 if stage == "SF" else 20
        options = candidates_for(routing, site, part, operation)
        if len(options) <= 1:
            continue
        current = str(getattr(task, "line", ""))
        primary = options[0].candidate_line
        target = primary
        if capacity_hours_by_line:
            cap = capacity_hours_by_line.get(primary, 0.0)
            if cap > 0 and load.get(primary, 0.0) > cap * max_ratio:
                alternates = [o for o in options[1:] if o.certified]
                if alternates:
                    target = min(alternates, key=lambda o: load.get(o.candidate_line, 0.0)).candidate_line
        if target and target != current:
            task_hours = float(
                getattr(task, "quantity", 0.0) * getattr(task, "ct_seconds", 0.0)
                / max(1e-9, getattr(task, "trp", 1.0)) / 3600.0
            )
            load[current] = max(0.0, load.get(current, 0.0) - task_hours)
            load[target] = load.get(target, 0.0) + task_hours
            setattr(task, "line", target)
            decisions.append({"task_id": getattr(task, "task_id", ""), "from": current, "to": target})
    return decisions
