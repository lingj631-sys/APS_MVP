"""Legacy rule catalog -> fixed kernel template mapping.

The 27 WL / 9 SY38 rules are a business catalog, not the kernel rule set. This
module tells the UI which catalog entries are already represented by a kernel
operator template, so migrated rules can be tagged instead of looking duplicated.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from src.kernel.registry import IMPLEMENTED_TEMPLATES

LEGACY_KERNEL_MAP: Dict[str, List[str]] = {
    # WL
    "R-WL-01": ["tooling_exclusivity"],
    "R-WL-06": ["calendar"],
    "R-WL-07": ["calendar"],
    "R-WL-08": ["calendar"],
    "R-WL-09": ["min_batch"],
    "R-WL-10": ["freeze"],
    "R-WL-11": ["contiguity", "changeover"],
    "R-WL-12": ["sequence"],
    "R-WL-14": ["time_window"],
    "R-WL-15": ["min_batch"],
    "R-WL-20": ["due_date"],
    "R-WL-21": ["material_ready"],
    "R-WL-22": ["inventory_bound"],
    "R-WL-23": ["inventory_bound"],
    "R-WL-25": ["calendar"],
    "R-WL-26": ["capacity_utilization"],
    "R-WL-27": ["pm_block"],
    # SY38
    "R-SY-01": ["time_window"],
    "R-SY-02": ["material_balance", "inventory_bound"],
    "R-SY-03": ["co_product"],
    "R-SY-04": ["material_balance"],
    "R-SY-05": ["concurrency"],
    "R-SY-06": ["inventory_bound"],
    "R-SY-07": ["inventory_bound"],
    "R-SY-08": ["capacity_utilization"],
    "R-SY-09": ["due_date", "inventory_bound"],
    # cross-site / legacy aliases
    "R-CR-01": ["customer_priority"],
    "R-FG-13": ["concurrency"],
    "R-TL-01": ["tooling_exclusivity"],
    "R-MT-01": ["material_ready"],
    "R-PM-01": ["pm_block"],
    "R-SF-02": ["sequence"],
    "R-FG-05": ["sequence"],
    "R-FG-06": ["changeover"],
}

STATUS_LABELS = {
    "active": "内核已接管",
    "partial": "部分接管",
    "available": "内核已实现·未启用",
    "legacy": "旧版/校验",
    "phase2": "Phase 2",
}

STATUS_ORDER = {"active": 0, "partial": 1, "available": 2, "phase2": 3, "legacy": 4}


def _active_by_template(active_rule_ids: Optional[Dict[str, str]]) -> Dict[str, str]:
    return dict(active_rule_ids or {})


def describe_legacy_rule(
    rule_id: str,
    active_templates: Iterable[str],
    active_rule_ids: Optional[Dict[str, str]] = None,
) -> Dict[str, object]:
    """Return status/templates/active kernel rule for one catalog rule."""
    templates = LEGACY_KERNEL_MAP.get(rule_id, [])
    active_set = set(active_templates or [])
    active_ids = _active_by_template(active_rule_ids)
    if not templates:
        return {
            "status": "legacy",
            "label": STATUS_LABELS["legacy"],
            "templates": [],
            "rule_ids": [],
        }

    implemented = [template for template in templates if template in IMPLEMENTED_TEMPLATES]
    if not implemented:
        return {
            "status": "phase2",
            "label": STATUS_LABELS["phase2"],
            "templates": templates,
            "rule_ids": [],
        }

    active = [template for template in implemented if template in active_set]
    if len(active) == len(implemented):
        status = "active"
    elif active:
        status = "partial"
    else:
        status = "available"
    return {
        "status": status,
        "label": STATUS_LABELS[status],
        "templates": templates,
        "rule_ids": [active_ids[t] for t in active if t in active_ids],
    }


def status_rank(status: str) -> int:
    return STATUS_ORDER.get(str(status), 9)
