"""Regression tests for the scheduler-side capacity kernel wiring.

The 2026-10-29 HTTP 500 was caused by calling total_objective() twice in
_compile_capacity_kernel. The unit-level kernel tests did not cover the
scheduler adapter, so these tests build a real WL plan and solve it.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.data_loader import build_requirement_plan, load_aps_database
from src.inventory_policy import load_inventory_policy
from src.kernel.types import GroupMode, Nature, RuleGroup, RuleInstance, RuleSet, Scope
from src.scheduler import APSScheduler
from src.shift_manager import CalendarMode


def _wl_plan(days: int):
    start = datetime(2026, 10, 12, 7, 0, 0)
    snap = load_aps_database(os.path.join(BASE_DIR, "data"), site_filter="WL")
    snap.orders = [o for o in snap.orders if o.due_datetime <= start + timedelta(days=days)]
    return snap, build_requirement_plan(snap), start


def _soft_capacity_rule_set():
    return RuleSet(
        "TEST_CAPACITY_KERNEL",
        "WL",
        1,
        [RuleGroup("G2_CAPACITY", 20, GroupMode.WEIGHTED)],
        [
            RuleInstance(
                "CAP",
                "capacity_utilization",
                Scope(line="PGLINKAGE"),
                Nature.SOFT,
                100,
                "G2_CAPACITY",
                20,
                {"min_util": 0.95, "max_util": 1.05, "tail_exempt": True},
            )
        ],
    )


class TestCapacityKernelWiring(unittest.TestCase):
    def test_kernel_capacity_objective_is_not_double_called(self):
        snap, plan, start = _wl_plan(5)
        self.assertTrue(plan.tasks)
        sched = APSScheduler(
            plan,
            start_datetime=start,
            planning_days=5,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            rule_set=_soft_capacity_rule_set(),
        )
        result = sched.solve(time_limit_seconds=10.0)
        self.assertIn(result.status_name, ("OPTIMAL", "FEASIBLE"))
        self.assertTrue(result.tasks)

    def test_sequence_setup_constraints_solve(self):
        snap, plan, start = _wl_plan(5)
        sched = APSScheduler(
            plan,
            start_datetime=start,
            planning_days=5,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            rule_set=_soft_capacity_rule_set(),
            sequence_setup=True,
        )
        result = sched.solve(time_limit_seconds=10.0)
        self.assertIn(result.status_name, ("OPTIMAL", "FEASIBLE"))
        self.assertIsNotNone(result.sequence_plan)
        self.assertTrue(result.sequence_plan)
        self.assertTrue(all(item["end_min"] >= item["start_min"] >= 0 for item in result.sequence_plan))
        self.assertGreater(sum(item["setup_min"] for item in result.sequence_plan), 0)

    def test_sequence_technician_interval_capacity_solves(self):
        snap, plan, start = _wl_plan(5)
        sched = APSScheduler(
            plan,
            start_datetime=start,
            planning_days=5,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            rule_set=_soft_capacity_rule_set(),
            sequence_setup=True,
            setup_technicians_available=1,
        )
        result = sched.solve(time_limit_seconds=10.0)
        self.assertIn(result.status_name, ("OPTIMAL", "FEASIBLE"))
        self.assertIsNotNone(result.sequence_plan)


    def test_inventory_policy_soft_constraint_solves(self):
        snap, plan, start = _wl_plan(5)
        policies = load_inventory_policy(
            os.path.join(BASE_DIR, "data", "master", "Inventory-Policy.csv"), site="WL"
        )
        sched = APSScheduler(
            plan,
            start_datetime=start,
            planning_days=5,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            inventory_policies=policies,
        )
        result = sched.solve(time_limit_seconds=10.0)
        self.assertIn(result.status_name, ("OPTIMAL", "FEASIBLE"))



if __name__ == "__main__":
    unittest.main()
