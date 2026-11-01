"""SC-09: baseline jitter penalty keeps the previous shift/line when possible."""
import os
import sys
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.data_loader import ProductionTask, RequirementPlan
from src.scheduler import APSScheduler
from src.shift_manager import CalendarMode


class TestJitterPenalty(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 12, 7, 0, 0)
        self.task = ProductionTask(
            task_id="T1",
            stage="FG",
            line="PGLINKAGE",
            part_no="P1",
            description="P1",
            ct_seconds=60.0,
            trp=1.0,
            mpq=50,
            lot_size=50,
            lots=2,
            quantity=100.0,
            lot_millis=2 * 3600 * 1000,
            order_id="O1",
            due_datetime=self.start + timedelta(days=2),
        )
        self.plan = RequirementPlan(
            fg_tasks=[self.task], sf_tasks=[], stock_movements=[], site="WL"
        )

    def _solve(self, baseline):
        scheduler = APSScheduler(
            self.plan,
            start_datetime=self.start,
            planning_days=2,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            shift_hours=8,
            shifts_per_day=3,
            enforce_utilization_95=False,
            enforce_material_balance=False,
            enforce_jitter=True,
            baseline_tasks=[baseline],
        )
        return scheduler.solve(time_limit_seconds=10.0)

    def test_keeps_baseline_completion_shift(self):
        baseline = SimpleNamespace(
            task_id="T1", line="PGLINKAGE", shift_breakdown={("10/12", "晚班"): 100}
        )
        result = self._solve(baseline)
        self.assertEqual(list(result.tasks[0].shift_breakdown), [("10/12", "晚班")])

    def test_no_baseline_is_safe(self):
        scheduler = APSScheduler(
            self.plan,
            start_datetime=self.start,
            planning_days=2,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            shift_hours=8,
            shifts_per_day=3,
            enforce_utilization_95=False,
            enforce_material_balance=False,
            enforce_jitter=True,
        )
        result = scheduler.solve(time_limit_seconds=10.0)
        self.assertTrue(result.tasks)


if __name__ == "__main__":
    unittest.main()
