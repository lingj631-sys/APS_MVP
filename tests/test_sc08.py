"""SC-08 prototype: completion-order inversion penalty vs natural due order."""
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


class TestSC08(unittest.TestCase):
    def test_due_order_is_not_inverted(self):
        start = datetime(2026, 10, 12, 7, 0, 0)

        def task(tid, due_days):
            return ProductionTask(
                task_id=tid,
                stage="FG",
                line="PGLINKAGE",
                part_no=tid,
                description=tid,
                ct_seconds=60.0,
                trp=1.0,
                mpq=50,
                lot_size=50,
                lots=1,
                quantity=50.0,
                lot_millis=2 * 3600 * 1000,
                order_id=f"O-{tid}",
                due_datetime=start + timedelta(days=due_days),
            )

        plan = RequirementPlan(
            fg_tasks=[task("EARLY", 2), task("LATE", 4)],
            sf_tasks=[],
            stock_movements=[],
            site="WL",
        )
        scheduler = APSScheduler(
            plan,
            start_datetime=start,
            planning_days=5,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            shift_hours=8,
            shifts_per_day=3,
            enforce_utilization_95=False,
            enforce_material_balance=False,
            enforce_sc08=True,
        )
        result = scheduler.solve(time_limit_seconds=10.0)
        by_id = {t.task_id: t for t in result.tasks}
        self.assertLessEqual(by_id["EARLY"].end_dt, by_id["LATE"].end_dt)


if __name__ == "__main__":
    unittest.main()
