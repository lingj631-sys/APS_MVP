"""ACT-28: weight sensitivity report.

The solve callable is injected so the report can run against the real scheduler
(slow) or a test double (fast). Each soft group/rule is scaled ±pct and the KPI
delta is reported; the base solve is not repeated.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

SolveFn = Callable[[Dict[str, Any]], Dict[str, Any]]


def soft_targets(payload: Dict[str, Any]) -> List[Tuple[str, str]]:
    rules = payload.get("rules", [])
    groups = []
    for group in payload.get("groups", []):
        if any(r.get("group") == group.get("id") and r.get("nature") == "soft" for r in rules):
            groups.append(("group", str(group["id"])))
    return groups


def perturb(payload: Dict[str, Any], kind: str, target_id: str, pct: float) -> Dict[str, Any]:
    data = copy.deepcopy(payload)
    factor = 1.0 + float(pct)
    for rule in data.get("rules", []):
        match = rule.get("group") == target_id if kind == "group" else rule.get("id") == target_id
        if not match:
            continue
        weight = float(rule.get("weight", 1.0))
        rule["weight"] = round(max(0.0, min(1000.0, weight * factor)), 4)
    return data


def _delta(base: Dict[str, Any], candidate: Dict[str, Any], keys: Iterable[str]) -> Dict[str, float]:
    result = {}
    for key in keys:
        before = base.get(key)
        after = candidate.get(key)
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            result[key] = round(float(after) - float(before), 4)
    return result


def run_sensitivity(
    payload: Dict[str, Any],
    solve_fn: SolveFn,
    pct: float = 0.2,
    targets: Optional[List[Tuple[str, str]]] = None,
    kpi_keys: Optional[List[str]] = None,
) -> Dict[str, Any]:
    keys = kpi_keys or ["otd_pct", "blocks", "below_95_shifts", "open_shifts", "objective"]
    base = solve_fn(copy.deepcopy(payload))
    if not base.get("feasible", False):
        return {"feasible": False, "base": base, "rows": [], "pct": pct}
    selected = targets or soft_targets(payload)
    rows = []
    for kind, target_id in selected:
        for direction, factor in (("+", 1.0 + pct), ("-", 1.0 - pct)):
            candidate_payload = perturb(payload, kind, target_id, factor - 1.0)
            candidate = solve_fn(candidate_payload)
            rows.append(
                {
                    "target": target_id,
                    "kind": kind,
                    "direction": f"{direction}{int(round(pct * 100))}%",
                    "factor": round(factor, 4),
                    "feasible": bool(candidate.get("feasible", False)),
                    "kpis": candidate,
                    "delta": _delta(base, candidate, keys),
                }
            )
    return {"feasible": True, "base": base, "rows": rows, "pct": pct, "kpi_keys": keys}


def format_report(report: Dict[str, Any]) -> str:
    if not report.get("feasible"):
        return "敏感度报告: 基准方案不可行，无法生成。"
    lines = [f"敏感度报告（±{int(round(report.get('pct', 0.2) * 100))}%）", f"基准: {report['base']}"]
    for row in report.get("rows", []):
        delta = ", ".join(f"{key}{value:+g}" for key, value in row.get("delta", {}).items())
        lines.append(f"  {row['target']:<24} {row['direction']:<5} {delta or '无变化'}")
    return "\n".join(lines)
