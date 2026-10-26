"""CLI for ACT-28 sensitivity and ACT-29 infeasibility explanation.

Examples::

    python -m src.rule_sets.cli sensitivity --site WL --budget 20 --pct 0.2
    python -m src.rule_sets.cli diagnose --site WL --budget 20 --version-id 3

These run real CP-SAT solves; sensitivity is (1 + 2 × soft groups) solves.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.data_loader import build_requirement_plan, load_aps_database
from src.kernel.registry import IMPLEMENTED_TEMPLATES
from src.kernel.types import RuleSet
from src.rule_sets import diagnose as diagnose_mod
from src.rule_sets import service
from src.rule_sets import sensitivity
from src.scheduler import APSScheduler
from src.shift_manager import CalendarMode
from src.validator import validate_schedule

DATA_DIR = BASE_DIR / "data"
START_DT = datetime(2026, 10, 12, 7, 0, 0)


def build_plan(site: str, horizon_days: int = 10):
    snapshot = load_aps_database(str(DATA_DIR), site_filter=site)
    horizon_end = START_DT + timedelta(days=horizon_days)
    snapshot.orders = [order for order in snapshot.orders if order.due_datetime <= horizon_end]
    return build_requirement_plan(snapshot)


def _kpis(plan, result) -> Dict[str, Any]:
    checks = validate_schedule(
        plan,
        result,
        allow_105_fg=result.allow_105_fg,
        allow_105_sf=result.allow_105_sf,
    )
    blocks = next((c.metrics.get("total_blocks", 0) for c in checks if "total_blocks" in c.metrics), 0)
    below = next((c.metrics.get("violating_shifts", 0) for c in checks if "violating_shifts" in c.metrics), 0)
    fg = [task for task in result.tasks if task.stage == "FG"]
    on_time = sum(1 for task in fg if task.due_datetime is None or task.end_dt <= task.due_datetime)
    open_shifts = {
        (task.line, date_str, shift_name)
        for task in result.tasks
        for (date_str, shift_name) in task.shift_breakdown
    }
    return {
        "feasible": True,
        "otd_pct": round(on_time / max(1, len(fg)) * 100.0, 1),
        "blocks": int(blocks),
        "below_95_shifts": int(below),
        "open_shifts": len(open_shifts),
        "objective": round(float(result.objective_value), 1),
    }


def make_solve_fn(site: str, budget: float, horizon_days: int = 10):
    plan = build_plan(site, horizon_days)

    def solve(payload: Dict[str, Any]) -> Dict[str, Any]:
        rule_set = RuleSet.from_dict(payload)
        templates = {
            rule.template_id for rule in rule_set.rules
            if rule.enabled and rule.template_id in IMPLEMENTED_TEMPLATES
        }
        scheduler = APSScheduler(
            plan,
            start_datetime=START_DT,
            planning_days=horizon_days,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site=site,
            rule_set=rule_set,
            kernel_templates=templates,
        )
        try:
            result = scheduler.solve(time_limit_seconds=budget)
        except RuntimeError as exc:
            return {"feasible": False, "error": str(exc)}
        return _kpis(plan, result)

    return solve


def _payload(args) -> Dict[str, Any]:
    if args.version_id:
        record = service.get_version(args.version_id)
        if record is None:
            raise SystemExit(f"rule set version {args.version_id} not found")
        return record["payload"]
    rule_set, warnings = service.active_rule_set(args.site)
    if rule_set is None:
        raise SystemExit(f"no active rule set for {args.site}")
    for warning in warnings:
        print(f"[warn] {warning}", file=sys.stderr)
    return rule_set.to_dict()


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Rule-set sensitivity and infeasibility diagnosis")
    parser.add_argument("command", choices=("sensitivity", "diagnose"))
    parser.add_argument("--site", default="WL")
    parser.add_argument("--version-id", type=int, default=None)
    parser.add_argument("--budget", type=float, default=20.0, help="seconds per solve")
    parser.add_argument("--horizon", type=int, default=10)
    parser.add_argument("--pct", type=float, default=0.2)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    payload = _payload(args)
    solve_fn = make_solve_fn(args.site, args.budget, args.horizon)

    if args.command == "sensitivity":
        report = sensitivity.run_sensitivity(payload, solve_fn, pct=args.pct)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(sensitivity.format_report(report))
        return 0

    report = diagnose_mod.diagnose(payload, solve_fn)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(report["message"])
        print(f"  feasible={report['feasible']}  groups={report['conflict_groups']}  rules={report['conflict_rules']}  pairs={report['conflict_pairs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
