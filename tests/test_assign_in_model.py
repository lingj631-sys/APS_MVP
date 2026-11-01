"""C2-b-2: in-model line assignment (assign(task, line) + per-line capacity)."""
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


def _task(task_id: str, line: str, part: str, start: datetime) -> ProductionTask:
    return ProductionTask(
        task_id=task_id,
        stage="FG",
        line=line,
        part_no=part,
        description=part,
        ct_seconds=60.0,
        trp=1.0,
        mpq=50,
        lot_size=50,
        lots=2,
        quantity=100.0,
        lot_millis=2 * 3600 * 1000,
        order_id=f"O-{task_id}",
        due_datetime=start + timedelta(days=2),
    )


class TestInModelAssignment(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 12, 7, 0, 0)
        plan = RequirementPlan(
            fg_tasks=[
                _task("T1", "PGLINKAGE", "P1", self.start),
                _task("T2", "轴套6", "P2", self.start),
            ],
            sf_tasks=[],
            stock_movements=[],
            site="WL",
        )
        self.scheduler = APSScheduler(
            plan,
            start_datetime=self.start,
            planning_days=2,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            shift_hours=8,
            shifts_per_day=3,
            enforce_utilization_95=False,
            enforce_material_balance=False,
            line_options={"T1": ["PGLINKAGE", "轴套6"]},
        )

    def test_assignment_is_exactly_one_and_writeback(self):
        result = self.scheduler.solve(time_limit_seconds=10.0)
        self.assertTrue(result.tasks)
        selected = {t.task_id: t.line for t in result.tasks}
        self.assertIn(selected["T1"], {"PGLINKAGE", "轴套6"})
        self.assertEqual(selected["T2"], "轴套6")
        self.assertEqual(len(self.scheduler.assignment_decisions), 1)
        self.assertEqual(self.scheduler.assignment_decisions[0]["task_id"], "T1")

    def test_fixed_task_never_moves(self):
        result = self.scheduler.solve(time_limit_seconds=10.0)
        t2 = next(t for t in result.tasks if t.task_id == "T2")
        self.assertEqual(t2.line, "轴套6")

    def test_repeated_solve_keeps_original_from_line(self):
        self.scheduler.solve(time_limit_seconds=10.0)
        original = dict(self.scheduler._original_lines)
        self.scheduler.solve(time_limit_seconds=10.0)
        for decision in self.scheduler.assignment_decisions:
            self.assertEqual(decision["from"], original[decision["task_id"]])

    def test_sequence_resolve_keeps_assignment(self):
        scheduler = APSScheduler(
            self.scheduler.plan,
            start_datetime=self.start,
            planning_days=2,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            shift_hours=8,
            shifts_per_day=3,
            enforce_utilization_95=False,
            enforce_material_balance=False,
            line_options={"T1": ["PGLINKAGE", "轴套6"]},
            sequence_setup=True,
            changeover_minutes=30,
        )
        result = scheduler.solve(time_limit_seconds=20.0)
        self.assertTrue(result.tasks)
        self.assertTrue(scheduler.assignment_decisions)
        self.assertTrue(getattr(result, "sequence_plan", None))
        selected = {t.task_id: t.line for t in result.tasks}
        self.assertEqual(selected["T1"], scheduler.assignment_decisions[0]["to"])


if __name__ == "__main__":
    unittest.main()
