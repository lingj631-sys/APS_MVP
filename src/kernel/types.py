"""Core types for the fixed rule kernel.

A rule is data, not code: ``template_id`` selects one of the fixed operators,
``scope`` says where it applies, ``nature``/``weight`` say how hard it is, and
``group``/``priority`` say which objective tier it belongs to.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple


class Nature(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class GroupMode(str, Enum):
    HARD = "hard"
    WEIGHTED = "weighted"
    LEXICOGRAPHIC = "lexicographic"


@dataclass(frozen=True)
class Target:
    """A scheduling target a rule can match: site / line / stage / part."""

    site: str = "*"
    line: str = "*"
    stage: str = "*"
    part: str = "*"


@dataclass(frozen=True)
class Scope:
    site: str = "*"
    line: str = "*"
    stage: str = "*"
    part: str = "*"

    def matches(self, target: Target) -> bool:
        return (
            self.site in ("*", target.site)
            and self.line in ("*", target.line)
            and self.stage in ("*", target.stage)
            and self.part in ("*", target.part)
        )

    def specificity(self) -> int:
        return sum(1 for value in (self.site, self.line, self.stage, self.part) if value != "*")

    def to_dict(self) -> Dict[str, Any]:
        return {"site": self.site, "line": self.line, "stage": self.stage, "part": self.part}

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "Scope":
        data = data or {}
        return cls(
            site=str(data.get("site", "*")),
            line=str(data.get("line", "*")),
            stage=str(data.get("stage", "*")),
            part=str(data.get("part", "*")),
        )


@dataclass(frozen=True)
class RuleInstance:
    rule_id: str
    template_id: str
    scope: Scope
    nature: Nature
    weight: float
    group_id: str
    priority: int
    params: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    inherit: str = "replace"  # replace | add
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None

    def is_effective(self, on: date) -> bool:
        if self.effective_from and on < self.effective_from:
            return False
        if self.effective_to and on > self.effective_to:
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.rule_id,
            "template": self.template_id,
            "scope": self.scope.to_dict(),
            "nature": self.nature.value,
            "weight": self.weight,
            "group": self.group_id,
            "priority": self.priority,
            "params": dict(self.params),
            "enabled": self.enabled,
            "inherit": self.inherit,
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuleInstance":
        def _date(value):
            if not value:
                return None
            if isinstance(value, date):
                return value
            return date.fromisoformat(str(value)[:10])

        return cls(
            rule_id=str(data["id"]),
            template_id=str(data["template"]),
            scope=Scope.from_dict(data.get("scope")),
            nature=Nature(str(data.get("nature", "soft"))),
            weight=float(data.get("weight", 1.0)),
            group_id=str(data.get("group", "G0")),
            priority=int(data.get("priority", 0)),
            params=dict(data.get("params") or {}),
            enabled=bool(data.get("enabled", True)),
            inherit=str(data.get("inherit", "replace")),
            effective_from=_date(data.get("effective_from")),
            effective_to=_date(data.get("effective_to")),
        )


@dataclass(frozen=True)
class RuleGroup:
    group_id: str
    priority: int
    mode: GroupMode

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.group_id, "priority": self.priority, "mode": self.mode.value}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuleGroup":
        return cls(
            group_id=str(data["id"]),
            priority=int(data.get("priority", 0)),
            mode=GroupMode(str(data.get("mode", "weighted"))),
        )


@dataclass
class RuleSet:
    rule_set_id: str
    site: str
    version: int
    groups: List[RuleGroup]
    rules: List[RuleInstance]
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None

    def group(self, group_id: str) -> RuleGroup:
        for group in self.groups:
            if group.group_id == group_id:
                return group
        raise KeyError(f"unknown rule group: {group_id}")

    def ordered_groups(self) -> List[RuleGroup]:
        return sorted(self.groups, key=lambda g: (g.priority, g.group_id))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_set_id": self.rule_set_id,
            "site": self.site,
            "version": self.version,
            "groups": [group.to_dict() for group in self.groups],
            "rules": [rule.to_dict() for rule in self.rules],
            "effective_from": self.effective_from.isoformat() if self.effective_from else None,
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuleSet":
        def _date(value):
            if not value:
                return None
            return date.fromisoformat(str(value)[:10])

        return cls(
            rule_set_id=str(data["rule_set_id"]),
            site=str(data.get("site", "*")),
            version=int(data.get("version", 1)),
            groups=[RuleGroup.from_dict(g) for g in data.get("groups", [])],
            rules=[RuleInstance.from_dict(r) for r in data.get("rules", [])],
            effective_from=_date(data.get("effective_from")),
            effective_to=_date(data.get("effective_to")),
        )
