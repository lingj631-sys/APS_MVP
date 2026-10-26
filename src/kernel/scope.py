"""Scope resolution and rule-set validation.

Resolution order is site -> family -> line_group -> line -> part. A more specific
``replace`` rule overrides a broader one with the same ``(template, group)``;
``add`` rules stack on top (e.g. site-wide 95% plus a line-specific 98%).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from src.kernel.registry import TEMPLATES, get_template, validate_params
from src.kernel.types import RuleInstance, RuleSet, Scope, Target


def _rule_key(rule: RuleInstance) -> Tuple[str, str]:
    return rule.template_id, rule.group_id


def resolve(rule_set: RuleSet, target: Target, on_date: Optional[date] = None) -> List[RuleInstance]:
    on_date = on_date or date.today()
    matching = [
        rule
        for rule in rule_set.rules
        if rule.enabled and rule.is_effective(on_date) and rule.scope.matches(target)
    ]
    grouped: Dict[Tuple[str, str], List[RuleInstance]] = defaultdict(list)
    for rule in matching:
        grouped[_rule_key(rule)].append(rule)

    chosen: List[RuleInstance] = []
    for rules in grouped.values():
        rules.sort(key=lambda r: (-r.scope.specificity(), r.rule_id))
        replace = next((r for r in rules if r.inherit == "replace"), None)
        adds = [r for r in rules if r.inherit == "add"]
        if replace is not None:
            chosen.append(replace)
        elif not adds:
            chosen.append(rules[0])
        chosen.extend(adds)

    seen = set()
    out: List[RuleInstance] = []
    for rule in chosen:
        if rule.rule_id in seen:
            continue
        seen.add(rule.rule_id)
        out.append(rule)
    return out


def resolve_for_targets(
    rule_set: RuleSet,
    targets: Iterable[Target],
    on_date: Optional[date] = None,
) -> Tuple[List[RuleInstance], List[str]]:
    """Resolve once per target, dedupe rules, and warn about dead/ambiguous rules."""
    on_date = on_date or date.today()
    targets = list(targets)
    used: Dict[str, RuleInstance] = {}
    warnings: List[str] = []

    for target in targets:
        for rule in resolve(rule_set, target, on_date):
            used[rule.rule_id] = rule

    for rule in rule_set.rules:
        if not rule.enabled:
            continue
        if not rule.is_effective(on_date):
            continue
        if rule.rule_id not in used:
            warnings.append(f"{rule.rule_id}: 作用域 {rule.scope} 未匹配任何对象")

    # Ambiguous replaces at the same specificity within one template/group.
    # Only warn when the two scopes can actually match the same target; an FG
    # and an SF capacity rule are both specificity 1 but never co-match.
    buckets: Dict[Tuple[str, str, int], List[RuleInstance]] = defaultdict(list)
    for rule in rule_set.rules:
        if rule.enabled and rule.is_effective(on_date) and rule.inherit == "replace":
            buckets[(rule.template_id, rule.group_id, rule.scope.specificity())].append(rule)
    for (template_id, group_id, specificity), rules in buckets.items():
        if len(rules) <= 1 or specificity <= 0:
            continue
        if any(
            _scopes_overlap(left.scope, right.scope)
            for index, left in enumerate(rules)
            for right in rules[index + 1:]
        ):
            warnings.append(
                f"{template_id}/{group_id}: 作用域层级 {specificity} 有多个 replace 规则 "
                f"{sorted(rule.rule_id for rule in rules)}，按 rule_id 取第一个"
            )

    return list(used.values()), warnings


def _scopes_overlap(left: Scope, right: Scope) -> bool:
    for attribute in ("site", "line", "stage", "part"):
        left_value = getattr(left, attribute)
        right_value = getattr(right, attribute)
        if left_value != "*" and right_value != "*" and left_value != right_value:
            return False
    return True


def targets_from_tasks(tasks: Iterable[object], site: str) -> List[Target]:
    seen = set()
    out: List[Target] = []
    for task in tasks:
        target = Target(
            site=site,
            line=str(getattr(task, "line", "*")),
            stage=str(getattr(task, "stage", "*")),
            part=str(getattr(task, "part_no", "*")),
        )
        key = (target.site, target.line, target.stage, target.part)
        if key in seen:
            continue
        seen.add(key)
        out.append(target)
    return out


def resolve_for_tasks(
    rule_set: RuleSet,
    tasks: Iterable[object],
    on_date: Optional[date] = None,
) -> Tuple[List[RuleInstance], List[str]]:
    return resolve_for_targets(rule_set, targets_from_tasks(tasks, rule_set.site), on_date)


def validate_rule_set(rule_set: RuleSet) -> List[str]:
    """Raise on structural errors, return soft warnings."""
    errors: List[str] = []
    warnings: List[str] = []
    group_ids = {group.group_id for group in rule_set.groups}
    seen_ids = set()

    for rule in rule_set.rules:
        if rule.rule_id in seen_ids:
            errors.append(f"重复的 rule_id: {rule.rule_id}")
        seen_ids.add(rule.rule_id)
        if rule.group_id not in group_ids:
            errors.append(f"{rule.rule_id}: 未知分组 {rule.group_id}")
        if rule.template_id not in TEMPLATES:
            errors.append(f"{rule.rule_id}: 未知模板 {rule.template_id}")
            continue
        if rule.weight < 0:
            errors.append(f"{rule.rule_id}: 权重不能为负")
        if rule.inherit not in ("replace", "add"):
            errors.append(f"{rule.rule_id}: inherit 只能是 replace/add")
        if rule.effective_from and rule.effective_to and rule.effective_from > rule.effective_to:
            errors.append(f"{rule.rule_id}: 生效期倒挂")
        try:
            validate_params(rule.template_id, rule.params)
        except ValueError as exc:
            errors.append(f"{rule.rule_id}: {exc}")

    if errors:
        raise ValueError("; ".join(errors))

    for group in rule_set.groups:
        members = [r for r in rule_set.rules if r.group_id == group.group_id]
        if not members:
            warnings.append(f"分组 {group.group_id} 没有任何规则")
    return warnings
