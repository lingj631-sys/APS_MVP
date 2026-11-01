"""SC-01-lite: D+1 stock clock penalty is wired (soft, no catch-up hard mode)."""
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


class TestD1Clock(unittest.TestCase):
    def test_d1_penalty_solves(self):
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
            lots=2,
            quantity=100.0,
            lot_millis=2 * 3600 * 1000,
            order_id="O1",
            due_datetime=start + timedelta(days=2),
        )
        plan = RequirementPlan(fg_tasks=[task], sf_tasks=[], stock_movements=[], site="WL")
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
            enforce_d1_clock=True,
            demand_by_date={"10/12": {"P1": 100}, "10/13": {"P1": 100}},
        )
        result = scheduler.solve(time_limit_seconds=10.0)
        self.assertEqual(len(result.tasks), 1)

    def test_hard_clock_rejects_uncoverable_next_day_demand(self):
        start = datetime(2026, 10, 12, 7, 0, 0)
        task = ProductionTask(
            task_id="T1", stage="FG", line="PGLINKAGE", part_no="P1",
            description="P1", ct_seconds=60.0, trp=1.0, mpq=50, lot_size=50,
            lots=2, quantity=100.0, lot_millis=2 * 3600 * 1000,
            order_id="O1", due_datetime=start + timedelta(days=2),
        )
        plan = RequirementPlan(fg_tasks=[task], sf_tasks=[], stock_movements=[], site="WL")
        scheduler = APSScheduler(
            plan, start_datetime=start, planning_days=3,
            calendar_mode=CalendarMode.SEVEN_ZERO, site="WL",
            shift_hours=8, shifts_per_day=3,
            enforce_utilization_95=False, enforce_material_balance=False,
            enforce_d1_clock=True, enforce_d1_clock_hard=True,
            demand_by_date={"10/12": {"P1": 100}, "10/13": {"P1": 200}},
        )
        with self.assertRaises(RuntimeError):
            scheduler.solve(time_limit_seconds=5.0)


if __name__ == "__main__":
    unittest.main()
