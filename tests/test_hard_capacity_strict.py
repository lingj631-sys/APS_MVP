"""Hard 95% capacity rule must not silently fall back to soft tiers."""
import os
import sys
import unittest
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.data_loader import ProductionTask, RequirementPlan
from src.kernel.types import GroupMode, Nature, RuleGroup, RuleInstance, RuleSet, Scope
from src.scheduler import APSScheduler
from src.shift_manager import CalendarMode


def _hard_capacity_rule_set():
    return RuleSet(
        "TEST_HARD_CAPACITY",
        "WL",
        1,
        [RuleGroup("G2_CAPACITY", 20, GroupMode.WEIGHTED)],
        [
            RuleInstance(
                "CAP_HARD",
                "capacity_utilization",
                Scope(line="PGLINKAGE"),
                Nature.HARD,
                100,
                "G2_CAPACITY",
                20,
                {"min_util": 0.95, "max_util": 1.0, "tail_exempt": True},
            )
        ],
    )


class TestHardCapacityStrict(unittest.TestCase):
    def test_hard_rule_does_not_fall_back_by_default(self):
        start = datetime(2026, 10, 12, 7, 0, 0)
        task = ProductionTask(
            task_id="T1", stage="FG", line="PGLINKAGE", part_no="P1",
            description="P1", ct_seconds=60, trp=1, mpq=50, lot_size=50,
            lots=2, quantity=100, lot_millis=2 * 3600 * 1000,
            order_id="O1", due_datetime=start + timedelta(days=3),
        )
        plan = RequirementPlan(fg_tasks=[task], sf_tasks=[], stock_movements=[], site="WL")
        strict = APSScheduler(
            plan, start_datetime=start, planning_days=5,
            calendar_mode=CalendarMode.SEVEN_ZERO, site="WL",
            shift_hours=8, shifts_per_day=3,
            enforce_utilization_95=True,
            rule_set=_hard_capacity_rule_set(),
            use_kernel_capacity=True,
        )
        self.assertTrue(strict.strict_capacity_hard)
        self.assertEqual(len(strict._tiers()), 1)
        self.assertEqual(strict._tiers()[0][1], "hard")

        relaxed = APSScheduler(
            plan, start_datetime=start, planning_days=5,
            calendar_mode=CalendarMode.SEVEN_ZERO, site="WL",
            shift_hours=8, shifts_per_day=3,
            enforce_utilization_95=True,
            rule_set=_hard_capacity_rule_set(),
            use_kernel_capacity=True,
            allow_soft_capacity_fallback=True,
        )
        self.assertGreater(len(relaxed._tiers()), 1)


if __name__ == "__main__":
    unittest.main()
