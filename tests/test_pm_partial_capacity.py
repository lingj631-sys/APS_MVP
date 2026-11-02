"""HC-03: PM capacity_rate reduces the affected shift's available minutes."""
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


class TestPMPartialCapacity(unittest.TestCase):
    def test_half_pm_shift_cannot_take_full_load(self):
        start = datetime(2026, 10, 12, 7)
        task = ProductionTask(
            task_id="FG1", stage="FG", line="PGLINKAGE", part_no="P1",
            description="P1", ct_seconds=60, trp=1, mpq=100,
            lot_size=100, lots=3, quantity=300,
            lot_millis=100 * 60 * 1000, order_id="O1",
            due_datetime=start + timedelta(days=3),
        )
        plan = RequirementPlan(
            fg_tasks=[task], sf_tasks=[], stock_movements=[], site="WL",
            pm_capacity={("PGLINKAGE", 0): 0.5},
        )
        sched = APSScheduler(
            plan, start_datetime=start, planning_days=5,
            calendar_mode=CalendarMode.SEVEN_ZERO, site="WL",
            shift_hours=8, shifts_per_day=3,
            enforce_utilization_95=False, enforce_material_balance=False,
            enforce_pm_maintenance=False,
        )
        result = sched.solve(time_limit_seconds=10)
        blocks = result.tasks[0].shift_breakdown
        self.assertEqual(sum(blocks.values()), 300)
        self.assertNotIn("10/12 早班", blocks)


if __name__ == "__main__":
    unittest.main()
