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


def solve_routing_assignment(
    tasks: Iterable[Any],
    routing: Dict[Tuple[str, str, int], List[RoutingOption]],
    *,
    site: str,
    capacity_hours_by_line: Optional[Dict[str, float]] = None,
    time_limit_seconds: float = 5.0,
) -> List[Dict[str, Any]]:
    """C2-b-0: solve the line-assignment stage with CP-SAT.

    This is a small assignment model upstream of the scheduler. It does not yet
    couple with intra-shift sequence; schedule feasibility is still checked by the
    main APS scheduler after the selected lines are applied.
    """
    from ortools.sat.python import cp_model

    tasks = list(tasks)
    if not tasks:
        return []
    capacity = {str(k): float(v) for k, v in (capacity_hours_by_line or {}).items()}
    model = cp_model.CpModel()
    choices: Dict[Tuple[str, str], Any] = {}
    hours: Dict[str, int] = {}
    primary: Dict[str, str] = {}
    alternatives: Dict[str, List[str]] = {}
    for task in tasks:
        tid = str(getattr(task, "task_id", ""))
        part = str(getattr(task, "part_no", ""))
        stage = str(getattr(task, "stage", ""))
        op = 10 if stage == "SF" else 20
        options = candidates_for(routing, site, part, op)
        lines = []
        for option in options:
            line = str(option.candidate_line or "")
            if line and line not in lines:
                lines.append(line)
        current = str(getattr(task, "line", ""))
        if len(lines) <= 1:
            continue
        primary[tid] = lines[0]
        alternatives[tid] = lines
        qty = float(getattr(task, "quantity", 0.0))
        ct = float(getattr(task, "ct_seconds", 0.0))
        trp = max(1e-9, float(getattr(task, "trp", 1.0)))
        hours[tid] = max(1, int(round(qty * ct / trp / 3600.0 * 1000.0)))
        for line in lines:
            choices[(tid, line)] = model.NewBoolVar(f"route.{tid}.{line}")
        model.Add(sum(choices[tid, line] for line in lines) == 1)

    if not choices:
        return []

    over_terms = []
    for line in {line for _tid, line in choices}:
        terms = [
            hours[tid] * var
            for (tid, candidate), var in choices.items()
            if candidate == line
        ]
        cap = max(1, int(round(capacity.get(line, 0.0) * 1000.0)))
        over = model.NewIntVar(0, max(cap, 1), f"route_over.{line}")
        model.Add(sum(terms) <= cap + over)
        over_terms.append(over)

    penalty = []
    for (tid, line), var in choices.items():
        if line != primary[tid]:
            penalty.append(10 * var)
    for over in over_terms:
        penalty.append(1000 * over)
    model.Minimize(sum(penalty) if penalty else 0)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.5, float(time_limit_seconds))
    solver.parameters.num_search_workers = 1
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return []
    decisions = []
    by_task = {str(getattr(t, "task_id", "")): t for t in tasks}
    for task in tasks:
        tid = str(getattr(task, "task_id", ""))
        if tid not in alternatives:
            continue
        selected = next(line for line in alternatives[tid] if solver.Value(choices[tid, line]) > 0.5)
        old = str(getattr(task, "line", ""))
        if selected != old:
            setattr(task, "line", selected)
        decisions.append({"task_id": tid, "from": old, "to": selected})
    return decisions
