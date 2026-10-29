"""Tests for 32-rule kernel mapping and dynamic RuleSet parameters in validator.

Eliminates the 'three-skin' disconnect between business catalog, solver kernel, and validator.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.kernel.rule_mapping import describe_legacy_rule
from src.kernel.types import GroupMode, Nature, RuleGroup, RuleInstance, RuleSet, Scope
from src.data_loader import (
    CustomerOrder,
    ProductionInputSnapshot,
    ProductParam,
    build_requirement_plan,
)
from src.scheduler import APSScheduler
from src.validator import validate_schedule


class TestRuleMappingAndValidator(unittest.TestCase):
    def test_01_all_32_rules_have_kernel_template_mappings(self):
        """All 32 HC/SC/BS rules must map to corresponding kernel templates."""
        from src.rule_catalog import load_catalog
        catalog = load_catalog(os.path.join(BASE_DIR, "config", "rule_catalog", "aps_32_rules.json"))
        self.assertEqual(len(catalog), 32)

        # Active templates mock
        active_templates = {
            "capacity_utilization", "min_batch", "due_date", "changeover",
            "freeze", "pm_block", "tooling_exclusivity", "sequence",
            "material_ready", "material_balance", "concurrency", "customer_priority",
            "time_window", "co_product", "inventory_bound", "calendar", "contiguity",
            "labor_capacity",
        }
        for rule in catalog:
            desc = describe_legacy_rule(rule.id, active_templates)
            self.assertGreater(
                len(desc["templates"]),
                0,
                f"Rule {rule.id} ({rule.name}) has no mapped kernel templates in rule_mapping.py",
            )
            self.assertIn(
                desc["status"],
                ("active", "partial", "available", "phase2"),
                f"Rule {rule.id} status unexpected: {desc['status']}",
            )

    def test_02_validator_uses_dynamic_freeze_shifts_from_rule_set(self):
        """Validator must extract freeze_shifts from active RuleSet instead of hardcoding 3."""
        o = CustomerOrder("ORD-F1", "P1", "C1", "10/12", datetime(2026, 10, 12, 15, 0), 10.0)
        snap = ProductionInputSnapshot(
            fg_params={"P1": ProductParam("WL", "L1", "P1", "FG", 10.0, 1.0, 1.0, 28800.0)},
            sf_params={},
            bom={},
            initial_stock={},
            orders=[o],
            planning_dates=["10/12"],
        )
        plan = build_requirement_plan(snap)
        rule_set = RuleSet(
            "DYNAMIC_FREEZE",
            "WL",
            1,
            [RuleGroup("G0_PHYSICAL", 0, GroupMode.HARD)],
            [RuleInstance("FRZ", "freeze", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {"fence_shifts": 1})],
        )

        sched = APSScheduler(
            plan,
            start_datetime=datetime(2026, 10, 12, 7, 0, 0),
            planning_days=1,
            site="WL",
            rule_set=rule_set,
        )
        res = sched.solve(time_limit_seconds=5.0)

        checks = validate_schedule(plan, res, site="WL", rule_set=rule_set)
        freeze_check = next((c for c in checks if "R-WL-10" in c.rule_ids), None)
        self.assertIsNotNone(freeze_check)
        self.assertEqual(
            freeze_check.metrics.get("frozen_shifts"),
            1,
            f"Expected frozen_shifts=1 from RuleSet, got {freeze_check.metrics.get('frozen_shifts')}",
        )

    def test_03_validator_uses_dynamic_concurrency_limit_from_rule_set(self):
        """Validator must extract max_parallel from active RuleSet concurrency rule."""
        o = CustomerOrder("ORD-C1", "P1", "C1", "10/12", datetime(2026, 10, 12, 15, 0), 10.0)
        snap = ProductionInputSnapshot(
            fg_params={"P1": ProductParam("WL", "L1", "P1", "FG", 10.0, 1.0, 1.0, 28800.0)},
            sf_params={},
            bom={},
            initial_stock={},
            orders=[o],
            planning_dates=["10/12"],
        )
        plan = build_requirement_plan(snap)
        rule_set = RuleSet(
            "DYNAMIC_CONC",
            "WL",
            1,
            [RuleGroup("G3_CHANGEOVER", 30, GroupMode.WEIGHTED)],
            [RuleInstance("CC", "concurrency", Scope(), Nature.HARD, 100, "G3_CHANGEOVER", 30, {"max_models": 2})],
        )

        sched = APSScheduler(
            plan,
            start_datetime=datetime(2026, 10, 12, 7, 0, 0),
            planning_days=1,
            site="WL",
            rule_set=rule_set,
        )
        res = sched.solve(time_limit_seconds=5.0)

        checks = validate_schedule(plan, res, site="WL", rule_set=rule_set)
        conc_check = next((c for c in checks if "R-FG-13" in c.rule_ids), None)
        self.assertIsNotNone(conc_check)
        self.assertEqual(
            conc_check.metrics.get("threshold"),
            2,
            f"Expected max_parallel=2 from RuleSet, got {conc_check.metrics.get('threshold')}",
        )


if __name__ == "__main__":
    unittest.main()
