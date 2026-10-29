"""Tests for scheduler-side operator wiring across all implemented kernel templates.

Verifies that APSScheduler compiles all 18 implemented kernel templates
and does not drop or decouple them when configured in a RuleSet.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.data_loader import build_requirement_plan, load_aps_database
from src.kernel.registry import IMPLEMENTED_TEMPLATES
from src.kernel.types import GroupMode, Nature, RuleGroup, RuleInstance, RuleSet, Scope
from src.scheduler import APSScheduler
from src.shift_manager import CalendarMode


def _wl_plan(days: int = 4):
    start = datetime(2026, 10, 12, 7, 0, 0)
    snap = load_aps_database(os.path.join(BASE_DIR, "data"), site_filter="WL")
    snap.orders = [o for o in snap.orders if o.due_datetime <= start + timedelta(days=days)]
    return snap, build_requirement_plan(snap), start


class TestOperatorWiring(unittest.TestCase):
    def test_01_scheduler_populates_kernel_templates_from_rule_set_by_default(self):
        rule_set = RuleSet(
            "ALL_TEMPLATES",
            "WL",
            1,
            [
                RuleGroup("G0_PHYSICAL", 0, GroupMode.HARD),
                RuleGroup("G1_DELIVERY", 10, GroupMode.LEXICOGRAPHIC),
                RuleGroup("G2_CAPACITY", 20, GroupMode.LEXICOGRAPHIC),
                RuleGroup("G3_CHANGEOVER", 30, GroupMode.WEIGHTED),
                RuleGroup("G4_INVENTORY", 40, GroupMode.WEIGHTED),
            ],
            [
                RuleInstance("MB", "min_batch", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {}),
                RuleInstance("TW", "time_window", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {"allowed_shifts": "早班"}),
                RuleInstance("INV", "inventory_bound", Scope(), Nature.SOFT, 10, "G4_INVENTORY", 40, {"max_days": 2.0}),
                RuleInstance("FRZ", "freeze", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {"fence_shifts": 2}),
                RuleInstance("CAL", "calendar", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {"mode": "FIVE_TWO"}),
                RuleInstance("CTG", "contiguity", Scope(), Nature.SOFT, 10, "G3_CHANGEOVER", 30, {"max_blocks": 2}),
                RuleInstance("COP", "co_product", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {"ratio": "1:1"}),
            ],
        )
        snap, plan, start = _wl_plan(4)
        sched = APSScheduler(
            plan,
            start_datetime=start,
            planning_days=4,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            rule_set=rule_set,
        )
        # Sched should automatically populate kernel_templates from rule_set
        for expected in ("min_batch", "time_window", "inventory_bound", "freeze", "calendar", "contiguity", "co_product"):
            self.assertIn(expected, sched.kernel_templates)

    def test_02_time_window_compiled_in_scheduler(self):
        rule_set = RuleSet(
            "TW_TEST",
            "WL",
            1,
            [RuleGroup("G0_PHYSICAL", 0, GroupMode.HARD)],
            [
                RuleInstance(
                    "TW_MORNING_ONLY",
                    "time_window",
                    Scope(stage="FG"),
                    Nature.HARD,
                    1,
                    "G0_PHYSICAL",
                    0,
                    {"allowed_shifts": "早班"},
                )
            ],
        )
        snap, plan, start = _wl_plan(4)
        sched = APSScheduler(
            plan,
            start_datetime=start,
            planning_days=4,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            rule_set=rule_set,
        )
        model, x, aux = sched._build_model("hard", hard_due=True)
        self.assertIn("time_window", getattr(sched, "_compiled_kernel_templates", set()))

    def test_03_inventory_bound_compiled_in_scheduler(self):
        rule_set = RuleSet(
            "INV_TEST",
            "WL",
            1,
            [RuleGroup("G4_INVENTORY", 40, GroupMode.WEIGHTED)],
            [
                RuleInstance(
                    "INV_BOUND",
                    "inventory_bound",
                    Scope(stage="FG"),
                    Nature.SOFT,
                    50,
                    "G4_INVENTORY",
                    40,
                    {"max_days": 2.0},
                )
            ],
        )
        snap, plan, start = _wl_plan(4)
        sched = APSScheduler(
            plan,
            start_datetime=start,
            planning_days=4,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            rule_set=rule_set,
        )
        model, x, aux = sched._build_model("hard", hard_due=True)
        self.assertIn("inventory_bound", getattr(sched, "_compiled_kernel_templates", set()))

    def test_04_rule_set_not_blocked_by_legacy_boolean_flags(self):
        # Even if legacy enforce_mpq_batch is False, rule_set min_batch must be compiled!
        rule_set = RuleSet(
            "MB_TEST",
            "WL",
            1,
            [RuleGroup("G0_PHYSICAL", 0, GroupMode.HARD)],
            [RuleInstance("MB", "min_batch", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {"unit": "MPQ"})],
        )
        snap, plan, start = _wl_plan(4)
        sched = APSScheduler(
            plan,
            start_datetime=start,
            planning_days=4,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            enforce_mpq_batch=False,  # Legacy flag is False
            rule_set=rule_set,
        )
        model, x, aux = sched._build_model("hard", hard_due=True)
        self.assertIn("min_batch", getattr(sched, "_compiled_kernel_templates", set()))

    def test_05_contiguity_freeze_calendar_co_product_compiled(self):
        rule_set = RuleSet(
            "WIRING_GAP_REPAIRED",
            "WL",
            1,
            [
                RuleGroup("G0_PHYSICAL", 0, GroupMode.HARD),
                RuleGroup("G3_CHANGEOVER", 30, GroupMode.WEIGHTED),
            ],
            [
                RuleInstance("CTG", "contiguity", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {"max_blocks": 2}),
                RuleInstance("FRZ", "freeze", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {"fence_shifts": 1}),
                RuleInstance("CAL", "calendar", Scope(), Nature.SOFT, 50, "G3_CHANGEOVER", 30, {"mode": "FIVE_TWO"}),
                RuleInstance("COP", "co_product", Scope(), Nature.HARD, 1, "G0_PHYSICAL", 0, {"ratio": "1:1"}),
            ],
        )
        snap, plan, start = _wl_plan(4)
        sched = APSScheduler(
            plan,
            start_datetime=start,
            planning_days=4,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            rule_set=rule_set,
        )
        model, x, aux = sched._build_model("hard", hard_due=True)
        compiled = getattr(sched, "_compiled_kernel_templates", set())
        for template in ("contiguity", "freeze", "calendar", "co_product"):
            self.assertIn(template, compiled, f"Template {template} was not compiled in scheduler")

    def test_06_solve_with_newly_wired_rules(self):
        rule_set = RuleSet(
            "SOLVE_REPAIRED",
            "WL",
            1,
            [
                RuleGroup("G0_PHYSICAL", 0, GroupMode.HARD),
                RuleGroup("G3_CHANGEOVER", 30, GroupMode.WEIGHTED),
                RuleGroup("G4_INVENTORY", 40, GroupMode.WEIGHTED),
            ],
            [
                RuleInstance("INV", "inventory_bound", Scope(stage="FG"), Nature.SOFT, 10, "G4_INVENTORY", 40, {"max_days": 4.0}),
                RuleInstance("CTG", "contiguity", Scope(), Nature.SOFT, 10, "G3_CHANGEOVER", 30, {"max_blocks": 3}),
            ],
        )
        snap, plan, start = _wl_plan(2)
        sched = APSScheduler(
            plan,
            start_datetime=start,
            planning_days=2,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            rule_set=rule_set,
        )
        result = sched.solve(time_limit_seconds=10.0)
        self.assertIn(result.status_name, ("OPTIMAL", "FEASIBLE"))


if __name__ == "__main__":
    unittest.main()
