"""Lexicographic group solving.

Groups are solved in priority order. Within a group, rule weights are normalised
to a common scale (``PenaltyBook``), so a delivery minute and a changeover count
never compete on raw units. Across groups there is no weighting: the earlier
group's objective is frozen with a bound before the next one runs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ortools.sat.python import cp_model

from src.kernel.compiler import CompiledRules
from src.kernel.types import GroupMode


@dataclass
class GroupResult:
    group_id: str
    status: str
    objective: float = 0.0
    bound: Optional[int] = None


@dataclass
class LexicographicResult:
    status: str
    groups: List[GroupResult] = field(default_factory=list)
    conflict_group: Optional[str] = None
    solver: Any = None

    @property
    def feasible(self) -> bool:
        return self.status in ("OPTIMAL", "FEASIBLE")

    def group_value(self, group_id: str) -> Optional[float]:
        for group in self.groups:
            if group.group_id == group_id:
                return group.objective
        return None

    def format_text(self) -> str:
        lines = [f"字典序求解: {self.status}"]
        for group in self.groups:
            bound = "" if group.bound is None else f" (bound={group.bound})"
            lines.append(f"  {group.group_id:<20} {group.status:<10} obj={group.objective:.0f}{bound}")
        if self.conflict_group:
            lines.append(f"  冲突分组: {self.conflict_group}")
        return "\n".join(lines)


def _solver(time_limit_seconds: float, num_workers: int, seed: int) -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.5, time_limit_seconds)
    solver.parameters.num_workers = num_workers
    solver.parameters.random_seed = seed
    return solver


def solve_lexicographic(
    model: Any,
    compiled: CompiledRules,
    time_limit_seconds: float = 30.0,
    group_slack: float = 0.0,
    num_workers: int = 1,
    seed: int = 1,
) -> LexicographicResult:
    """Solve group by group, freezing each group's best value before the next.

    ``group_slack`` allows a percentage relaxation (e.g. 0.01 = the next group may
    give up 1% of the previous objective). Default 0 keeps strict priority.
    """
    groups = compiled.ordered_groups()
    soft_groups = [
        group for group in groups
        if group.mode is not GroupMode.HARD and compiled.book.terms_in(group.group_id)
    ]
    per_group_budget = time_limit_seconds / max(1, len(soft_groups))

    results: List[GroupResult] = []
    last_solver = None

    for group in groups:
        if group.mode is GroupMode.HARD:
            results.append(GroupResult(group.group_id, "hard"))
            continue
        if not compiled.book.terms_in(group.group_id):
            results.append(GroupResult(group.group_id, "no_terms"))
            continue

        expr = compiled.group_objective(group.group_id)
        model.Minimize(expr)
        solver = _solver(per_group_budget, num_workers, seed)
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return LexicographicResult("INFEASIBLE", results, group.group_id, solver)

        value = float(solver.ObjectiveValue())
        bound = int(math.ceil(value * (1.0 + max(0.0, group_slack))))
        results.append(GroupResult(group.group_id, solver.StatusName(status), value, bound))
        model.Add(expr <= bound)
        last_solver = solver

    if last_solver is None:
        model.Minimize(0)
        solver = _solver(max(0.5, time_limit_seconds), num_workers, seed)
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return LexicographicResult("INFEASIBLE", results, None, solver)
        return LexicographicResult(solver.StatusName(status), results, None, solver)

    return LexicographicResult("FEASIBLE", results, None, last_solver)
