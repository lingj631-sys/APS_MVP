"""Machine-readable 32-rule catalog and per-site override resolution (C-governance)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class CatalogRule:
    id: str
    rule_class: str
    category: str
    scope: str
    name: str
    constraint: str
    weight: Optional[str]
    logic: str
    legacy_ref: str
    formula: str
    status: str
    phase: str


@dataclass
class SiteRuleOverride:
    enabled: Optional[bool] = None
    weight: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)


def load_catalog(path: str | Path) -> List[CatalogRule]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rules = [
        CatalogRule(
            id=item["id"],
            rule_class=item["class"],
            category=item.get("category", ""),
            scope=item.get("scope", ""),
            name=item.get("name", ""),
            constraint=item.get("constraint", ""),
            weight=item.get("weight"),
            logic=item.get("logic", ""),
            legacy_ref=item.get("legacy_ref", ""),
            formula=item.get("formula", ""),
            status=item.get("status", "needs_model"),
            phase=item.get("phase", "B"),
        )
        for item in data.get("rules", [])
    ]
    ids = [item.id for item in rules]
    if len(ids) != len(set(ids)):
        raise ValueError("rule catalog contains duplicate ids")
    return rules


def effective_for_site(
    catalog: Iterable[CatalogRule],
    overrides: Optional[Dict[str, SiteRuleOverride]] = None,
) -> List[Dict[str, Any]]:
    """Merge global catalog with per-site overrides; overrides never mutate catalog."""
    overrides = overrides or {}
    out: List[Dict[str, Any]] = []
    for rule in catalog:
        override = overrides.get(rule.id)
        if override and override.enabled is False:
            continue
        item = {
            "id": rule.id,
            "class": rule.rule_class,
            "name": rule.name,
            "status": rule.status,
            "phase": rule.phase,
            "weight": override.weight if override and override.weight is not None else rule.weight,
            "params": dict(override.params) if override else {},
        }
        out.append(item)
    return out
