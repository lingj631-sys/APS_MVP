"""BS-06-lite: slack fallback info is populated and does not disturb a feasible plan."""
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


class TestSlackFallback(unittest.TestCase):
    def test_no_late_order_keeps_original_plan(self):
        start = datetime(2026, 10, 12, 7, 0, 0)
        tasks = []
        for i in (1, 2):
            tasks.append(
                ProductionTask(
                    task_id=f"T{i}",
                    stage="FG",
                    line="PGLINKAGE",
                    part_no=f"P{i}",
                    description=f"P{i}",
                    ct_seconds=60.0,
                    trp=1.0,
                    mpq=50,
                    lot_size=50,
                    lots=1,
                    quantity=50.0,
                    lot_millis=2 * 3600 * 1000,
                    order_id=f"O{i}",
                    due_datetime=start + timedelta(days=2),
                    mold_id="M1",
                )
            )
        plan = RequirementPlan(fg_tasks=tasks, sf_tasks=[], stock_movements=[], site="WL")
        scheduler = APSScheduler(
            plan,
            start_datetime=start,
            planning_days=3,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            shift_hours=8,
            shifts_per_day=3,
            enforce_utilization_95=False,
            enforce_material_balance=False,
            enforce_campaign_batching=True,
            enforce_slack_fallback=True,
        )
        scheduler.solve(time_limit_seconds=10.0)
        info = scheduler.slack_fallback_info
        self.assertTrue(info["enabled"])
        self.assertFalse(info["triggered"])
        self.assertEqual(info["chosen"], "original")


if __name__ == "__main__":
    unittest.main()
