"""SC-10: three-shift load smoothing spreads FG work across shifts."""
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


class TestLaborSmoothing(unittest.TestCase):
    def test_fg_load_is_spread_across_three_shifts(self):
        start = datetime(2026, 10, 12, 7, 0, 0)
        tasks = [
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
            )
            for i in range(1, 4)
        ]
        plan = RequirementPlan(fg_tasks=tasks, sf_tasks=[], stock_movements=[], site="WL")
        scheduler = APSScheduler(
            plan,
            start_datetime=start,
            planning_days=2,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            shift_hours=8,
            shifts_per_day=3,
            enforce_utilization_95=False,
            enforce_material_balance=False,
            enforce_labor_smoothing=True,
        )
        result = scheduler.solve(time_limit_seconds=10.0)
        shift_names = {
            key[1]
            for task in result.tasks
            for key in task.shift_breakdown
        }
        self.assertGreaterEqual(len(shift_names), 2)


if __name__ == "__main__":
    unittest.main()
