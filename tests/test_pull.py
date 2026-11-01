"""BS-04-lite: pull mode shifts completion toward the due date (JIT)."""
import os
import sys
import unittest
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.data_loader import ProductionTask, RequirementPlan
from src.scheduler import APSScheduler
from src.shift_manager import CalendarMode


class TestPullMode(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 12, 7, 0, 0)
        task = ProductionTask(
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
            due_datetime=self.start + timedelta(days=3),
        )
        self.plan = RequirementPlan(fg_tasks=[task], sf_tasks=[], stock_movements=[], site="WL")

    def _solve(self, pull):
        scheduler = APSScheduler(
            self.plan,
            start_datetime=self.start,
            planning_days=5,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            shift_hours=8,
            shifts_per_day=3,
            enforce_utilization_95=False,
            enforce_material_balance=False,
            enforce_pull=pull,
        )
        return scheduler.solve(time_limit_seconds=10.0).tasks[0]

    def test_pull_completes_no_earlier_than_push(self):
        push = self._solve(False)
        pull = self._solve(True)
        self.assertGreaterEqual(pull.end_dt, push.end_dt)


if __name__ == "__main__":
    unittest.main()
