"""One-off rule-set migrations.

Phase 1 capacity semantics:
- FG may use 95%~105% only to avoid splitting an MPQ batch;
- SF stays 95%~100%;
- SF MPQ is an order-total floor, not a per-shift floor, so SF can run piece by piece.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Tuple


def split_capacity_and_sf_batch(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    data = copy.deepcopy(payload)
    rules = data.get("rules", [])
    site = str(data.get("site") or "WL")
    if any(rule.get("id") in (f"{site}_CAP_FG", f"{site}_CAP_SF") for rule in rules):
        return data, False

    mpq_index = next((i for i, rule in enumerate(rules) if rule.get("id") == f"{site}_MPQ"), None)
    cap_index = next((i for i, rule in enumerate(rules) if rule.get("id") == f"{site}_CAP"), None)
    if mpq_index is None and cap_index is None:
        return data, False

    new_rules = []
    for index, rule in enumerate(rules):
        if rule.get("id") == f"{site}_MPQ":
            new_rules.extend(
                [
                    {
                        "id": f"{site}_MPQ_FG",
                        "template": "min_batch",
                        "scope": {"stage": "FG"},
                        "nature": "hard",
                        "weight": 1,
                        "group": "G0_PHYSICAL",
                        "priority": 0,
                        "params": {"unit": "MPQ"},
                    },
                    {
                        "id": f"{site}_MPQ_SF",
                        "template": "min_batch",
                        "scope": {"stage": "SF"},
                        "nature": "hard",
                        "weight": 1,
                        "group": "G0_PHYSICAL",
                        "priority": 0,
                        "params": {"unit": "piece", "min_multiple": 1},
                    },
                ]
            )
        elif rule.get("id") == f"{site}_CAP":
            new_rules.extend(
                [
                    {
                        "id": f"{site}_CAP_FG",
                        "template": "capacity_utilization",
                        "scope": {"stage": "FG"},
                        "nature": "soft",
                        "weight": 100,
                        "group": "G2_CAPACITY",
                        "priority": 20,
                        "params": {"min_util": 0.95, "max_util": 1.05, "tail_exempt": True},
                    },
                    {
                        "id": f"{site}_CAP_SF",
                        "template": "capacity_utilization",
                        "scope": {"stage": "SF"},
                        "nature": "soft",
                        "weight": 100,
                        "group": "G2_CAPACITY",
                        "priority": 20,
                        "params": {"min_util": 0.95, "max_util": 1.0, "tail_exempt": True},
                    },
                ]
            )
        else:
            new_rules.append(rule)
    data["rules"] = new_rules
    return data, True
