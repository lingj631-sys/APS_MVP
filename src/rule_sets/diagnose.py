"""ACT-29: rule-set infeasibility explanation.

Strategy: relax one group at a time, then one rule at a time inside the first
conflicting group, then rule pairs. The solve callable is injected, so the same
logic works against CP-SAT (slow) or a test double (fast). This is a v1
group/rule-level explanation, not a proof of the minimal unsatisfiable core.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Optional

SolveFn = Callable[[Dict[str, Any]], Dict[str, Any]]


def _without_group(payload: Dict[str, Any], group_id: str) -> Dict[str, Any]:
    data = copy.deepcopy(payload)
    data["rules"] = [r for r in data.get("rules", []) if r.get("group") != group_id]
    return data


def _without_rule(payload: Dict[str, Any], rule_id: str) -> Dict[str, Any]:
    data = copy.deepcopy(payload)
    data["rules"] = [r for r in data.get("rules", []) if r.get("id") != rule_id]
    return data


def _rule_ids(payload: Dict[str, Any], group_id: str) -> List[str]:
    return [str(r.get("id")) for r in payload.get("rules", []) if r.get("group") == group_id]


def diagnose(
    payload: Dict[str, Any],
    solve_fn: SolveFn,
    max_pairs: int = 20,
) -> Dict[str, Any]:
    base = solve_fn(copy.deepcopy(payload))
    if base.get("feasible", False):
        return {
            "feasible": True,
            "base": base,
            "message": "基准规则集可行，无需诊断。",
            "conflict_groups": [],
            "conflict_rules": [],
            "conflict_pairs": [],
        }

    groups = sorted({str(r.get("group")) for r in payload.get("rules", []) if r.get("group")})
    group_conflicts: List[str] = []
    for group_id in groups:
        reduced = solve_fn(_without_group(payload, group_id))
        if reduced.get("feasible", False):
            group_conflicts.append(group_id)

    rule_conflicts: List[str] = []
    for group_id in group_conflicts:
        for rule_id in _rule_ids(payload, group_id):
            reduced = solve_fn(_without_rule(payload, rule_id))
            if reduced.get("feasible", False):
                rule_conflicts.append(rule_id)

    pairs: List[List[str]] = []
    if group_conflicts and not rule_conflicts:
        for group_id in group_conflicts:
            ids = _rule_ids(payload, group_id)
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    reduced = _without_rule(_without_rule(payload, ids[i]), ids[j])
                    if solve_fn(reduced).get("feasible", False):
                        pairs.append([ids[i], ids[j]])
                    if len(pairs) >= max_pairs:
                        break
                if len(pairs) >= max_pairs:
                    break

    if rule_conflicts:
        message = "去掉以下规则后可行，冲突规则为: " + ", ".join(rule_conflicts)
    elif pairs:
        message = "需要同时去掉以下规则对才可行: " + "; ".join(" + ".join(pair) for pair in pairs)
    elif group_conflicts:
        message = "去掉以下分组后可行，冲突分组为: " + ", ".join(group_conflicts) + "（组内单条规则不足以解释）"
    else:
        message = "未定位到单个分组；可能是跨组组合冲突，需要人工检查产能/交期/物料上限。"

    return {
        "feasible": False,
        "base": base,
        "message": message,
        "conflict_groups": group_conflicts,
        "conflict_rules": rule_conflicts,
        "conflict_pairs": pairs,
    }
