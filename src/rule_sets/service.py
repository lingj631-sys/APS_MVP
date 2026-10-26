"""Rule-set service: validate, diff, version, activate, seed."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.kernel.scope import validate_rule_set
from src.kernel.types import RuleSet
from src.rule_sets import excel as excel_rules
from src.rule_sets import store

BASE_DIR = Path(__file__).resolve().parents[2]
RULE_SET_DIR = BASE_DIR / "config" / "rule_sets"


def payload_from_rule_set(rule_set: RuleSet) -> Dict[str, Any]:
    return rule_set.to_dict()


def validate_payload(payload: Dict[str, Any]) -> Tuple[Optional[RuleSet], List[str]]:
    warnings: List[str] = []
    try:
        rule_set = RuleSet.from_dict(payload)
        warnings.extend(validate_rule_set(rule_set))
        return rule_set, warnings
    except (ValueError, KeyError, TypeError) as exc:
        return None, [str(exc)]


def _index(items: List[Dict[str, Any]], key: str) -> Dict[str, Dict[str, Any]]:
    return {str(item.get(key)): item for item in items or []}


def diff_payloads(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> Dict[str, Any]:
    old = old or {}
    result: Dict[str, Any] = {
        "rules": {"added": [], "removed": [], "modified": []},
        "groups": {"added": [], "removed": [], "modified": []},
        "summary": {},
    }
    for section, key, fields in (
        ("rules", "id", ("template", "scope", "nature", "weight", "group", "priority", "params", "enabled", "inherit", "effective_from", "effective_to")),
        ("groups", "id", ("priority", "mode")),
    ):
        old_items = _index(old.get(section, []), key)
        new_items = _index(new.get(section, []), key)
        result[section]["added"] = sorted(set(new_items) - set(old_items))
        result[section]["removed"] = sorted(set(old_items) - set(new_items))
        for item_id in sorted(set(old_items) & set(new_items)):
            changes = {}
            for field in fields:
                before = old_items[item_id].get(field)
                after = new_items[item_id].get(field)
                if before != after:
                    changes[field] = {"old": before, "new": after}
            if changes:
                result[section]["modified"].append({"id": item_id, "changes": changes})
    result["summary"] = {
        "rules_added": len(result["rules"]["added"]),
        "rules_removed": len(result["rules"]["removed"]),
        "rules_modified": len(result["rules"]["modified"]),
        "groups_added": len(result["groups"]["added"]),
        "groups_removed": len(result["groups"]["removed"]),
        "groups_modified": len(result["groups"]["modified"]),
    }
    return result


def _save(
    site: str,
    payload: Dict[str, Any],
    note: str = "",
    activate: bool = False,
    db_path: Optional[Path] = None,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, str]], List[str]]:
    rule_set, warnings = validate_payload(payload)
    if rule_set is None:
        return None, [{"sheet": "Payload", "cell": "", "rule_id": "", "message": w} for w in warnings], []
    normalized = rule_set.to_dict()
    rule_set_id = str(payload.get("rule_set_id") or f"{site}_RULES")
    previous = store.get_latest(site, rule_set_id, db_path)
    diff = diff_payloads(previous["payload"] if previous else None, normalized)
    saved, created = store.save_version(
        site=site,
        rule_set_id=rule_set_id,
        payload=normalized,
        note=note,
        activate=activate,
        db_path=db_path,
    )
    saved = dict(saved)
    saved["diff"] = diff
    saved["created"] = created
    saved["warnings"] = warnings
    return saved, [], warnings


def save_payload(
    payload: Dict[str, Any],
    site: Optional[str] = None,
    note: str = "",
    activate: bool = False,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    target_site = site or str(payload.get("site") or "WL")
    saved, errors, warnings = _save(target_site, payload, note, activate, db_path)
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings}
    return {
        "ok": True,
        "version": saved["version"],
        "id": saved["id"],
        "status": saved["status"],
        "created": saved["created"],
        "diff": saved["diff"],
        "warnings": warnings,
        "errors": [],
    }


def import_excel(
    path: str | Path,
    site: Optional[str] = None,
    note: str = "",
    activate: bool = False,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    payload, errors, warnings = excel_rules.read_workbook(path, site=site)
    if errors or payload is None:
        return {"ok": False, "errors": errors, "warnings": warnings, "preview": payload}
    target_site = site or str(payload.get("site") or "WL")
    saved, save_errors, save_warnings = _save(target_site, payload, note or "Excel import", activate, db_path)
    if save_errors:
        return {"ok": False, "errors": save_errors, "warnings": warnings + save_warnings}
    return {
        "ok": True,
        "version": saved["version"],
        "id": saved["id"],
        "status": saved["status"],
        "created": saved["created"],
        "diff": saved["diff"],
        "warnings": warnings + save_warnings,
        "errors": [],
    }


def active_rule_set(
    site: str, db_path: Optional[Path] = None
) -> Tuple[Optional[RuleSet], List[str]]:
    record = store.get_active(site, db_path)
    if not record:
        return None, []
    rule_set, warnings = validate_payload(record["payload"])
    return rule_set, warnings


def list_versions(site: str, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    return store.list_versions(site, db_path)


def get_version(version_id: int, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    return store.get_version(version_id, db_path)


def activate(version_id: int, db_path: Optional[Path] = None) -> Dict[str, Any]:
    record = store.activate(version_id, db_path)
    return {
        "ok": True,
        "id": record["id"],
        "site": record["site"],
        "version": record["version"],
        "status": record["status"],
    }


def export_active_excel(site: str, path: str | Path, db_path: Optional[Path] = None) -> Optional[Path]:
    rule_set, _ = active_rule_set(site, db_path)
    if rule_set is None:
        return None
    return excel_rules.export_rule_set(rule_set, path)


def write_template(path: str | Path, site: str = "WL") -> Path:
    return excel_rules.write_template(path, site=site)


def ensure_seed(
    sites: Tuple[str, ...] = ("WL", "SY38"),
    db_path: Optional[Path] = None,
) -> List[str]:
    created: List[str] = []
    for site in sites:
        if store.get_active(site, db_path):
            continue
        path = RULE_SET_DIR / f"{site}_Phase1.json"
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = save_payload(
            payload,
            site=site,
            note="Phase 1 baseline seed",
            activate=True,
            db_path=db_path,
        )
        if result.get("ok"):
            created.append(site)
    return created
