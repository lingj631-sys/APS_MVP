"""HC-16-lite: per-task block cap keeps one run (no 48-piece fragmentation)."""
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


class TestHC16Lite(unittest.TestCase):
    def test_block_cap_keeps_task_contiguous(self):
        start = datetime(2026, 10, 12, 7, 0, 0)
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
            lots=12,
            quantity=600.0,
            lot_millis=2 * 3600 * 1000,
            order_id="O1",
            due_datetime=start + timedelta(days=2),
        )
        plan = RequirementPlan(fg_tasks=[task], sf_tasks=[], stock_movements=[], site="WL")
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
            max_blocks_per_task=1,
        )
        result = scheduler.solve(time_limit_seconds=10.0)
        slots = [
            shift.index
            for shift in scheduler.shifts
            if (shift.date_str, shift.shift_name) in result.tasks[0].shift_breakdown
        ]
        self.assertTrue(slots)
        self.assertEqual(max(slots) - min(slots) + 1, len(slots), "task blocks must be contiguous")


if __name__ == "__main__":
    unittest.main()
