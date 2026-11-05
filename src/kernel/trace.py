"""Rule-set observability: the parameters the solver will actually consume.

The editor stores whatever the planner typed; the compiler validates it against
the template schema and fills in defaults.  Reading the workbook alone therefore
does not answer "is this rule running, and with which values", so this module
renders the validated view for the solve trace log.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from src.kernel.registry import IMPLEMENTED_TEMPLATES, get_template
from src.kernel.types import RuleInstance, RuleSet

LOGGER_NAME = "aps.rules"


def rule_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def effective_params(rule: RuleInstance) -> Dict[str, Any]:
    """Schema-defaulted parameter values, or the validation error for this rule."""
    try:
        return get_template(rule.template_id).validate(rule.params)
    except (KeyError, ValueError) as exc:
        return {"__error__": str(exc)}


def describe_rule(rule: RuleInstance) -> Dict[str, Any]:
    return {
        "id": rule.rule_id,
        "template": rule.template_id,
        "implemented": rule.template_id in IMPLEMENTED_TEMPLATES,
        "enabled": rule.enabled,
        "nature": rule.nature.value,
        "weight": rule.weight,
        "group": rule.group_id,
        "priority": rule.priority,
        "scope": rule.scope.to_dict(),
        "effective_from": rule.effective_from.isoformat() if rule.effective_from else None,
        "effective_to": rule.effective_to.isoformat() if rule.effective_to else None,
        "params": effective_params(rule),
    }


def describe_rule_set(rule_set: RuleSet) -> Dict[str, Any]:
    return {
        "rule_set_id": rule_set.rule_set_id,
        "site": rule_set.site,
        "version": rule_set.version,
        "groups": [group.to_dict() for group in rule_set.ordered_groups()],
        "rules": [describe_rule(rule) for rule in rule_set.rules],
    }


def format_rule_line(rule: RuleInstance) -> str:
    described = describe_rule(rule)
    state = "ON " if described["enabled"] else "OFF"
    compiler = "kernel" if described["implemented"] else "NO-COMPILER"
    scope = ",".join(
        f"{key}={value}" for key, value in described["scope"].items() if value != "*"
    ) or "site-wide"
    params = json.dumps(described["params"], ensure_ascii=False, sort_keys=True)
    return (
        f"{state} {described['id']} | template={described['template']} ({compiler}) | "
        f"{described['nature']} w={described['weight']:g} "
        f"{described['group']}#{described['priority']} | scope[{scope}] | params={params}"
    )


def log_rule_set(rule_set: RuleSet, logger: logging.Logger | None = None) -> List[str]:
    """Emit one line per rule and return them (useful for tests and API replies)."""
    target = logger or rule_logger()
    lines = [
        f"[rule-set] {rule_set.rule_set_id} v{rule_set.version} "
        f"site={rule_set.site} rules={len(rule_set.rules)} "
        f"groups={[group.group_id for group in rule_set.ordered_groups()]}"
    ]
    for rule in rule_set.rules:
        lines.append(f"[rule-set]   {format_rule_line(rule)}")
    for line in lines:
        target.info(line)
    return lines
