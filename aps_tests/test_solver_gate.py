from __future__ import annotations

import datetime as dt
import unittest

from aps.calendar.geometry import build_slots
from aps.calendar.pattern import CalendarMode
from aps.dataset import BomLink, DataSet, Horizon, LineGeometry, RouteOption, Task
from aps.model.context import StructureContext
from aps.model.plan_row import extract_line_shifts, extract_plan_rows
from aps.model.solver import solve_schedule

FAST = True

SITE = "WL"
START = dt.date(2026, 11, 3)

LINES = (
    LineGeometry("LINE_FG", 8.0, 3, 0.85),
    LineGeometry("LINE_SF", 8.0, 3, 0.90),
)

ROUTES = (
    RouteOption(
        pn="FG01", op_seq=10, line_code="LINE_FG", certified=True,
        ct_sec=20.0, unit_ms=23529, per_shift_cap=1224, headcount=5,
        changeover_min=30.0, changeover_source="routing", mold_ids=("M1",),
    ),
    RouteOption(
        pn="SF01", op_seq=10, line_code="LINE_SF", certified=True,
        ct_sec=15.0, unit_ms=16666, per_shift_cap=1728, headcount=3,
        changeover_min=20.0, changeover_source="routing", mold_ids=("M2",),
    ),
)


def _make_dataset(tasks=(), bom=(), days=5) -> DataSet:
    horizon = Horizon(SITE, START, days)
    slots = build_slots(LINES, START, days, CalendarMode.CONTINUOUS)
    return DataSet(
        horizon=horizon,
        mode=CalendarMode.CONTINUOUS,
        lines=LINES,
        slots=slots,
        tasks=tasks,
        routes=ROUTES,
        bom=bom,
        safety_stock={},
        mold_to_parts={"M1": frozenset({"FG01"}), "M2": frozenset({"SF01"})},
        initial_stock={},
        calendar_overrides={},
    )


class TestSolverGate(unittest.TestCase):
    def test_l2_gate_scale_and_stability(self):
        # 构造包含 10 个工单任务的中型排产测试
        tasks = []
        for i in range(5):
            tasks.append(
                Task(pn="FG01", stage="FG", net_requirement=96,
                     due_date=START + dt.timedelta(days=i),
                     mpq=48, moq=None, mold_id="M1", family_code="F1",
                     description=f"FG Task {i}", is_safety_topup=False)
            )
            tasks.append(
                Task(pn="SF01", stage="SF", net_requirement=96,
                     due_date=START + dt.timedelta(days=i),
                     mpq=48, moq=None, mold_id="M2", family_code="F1",
                     description=f"SF Task {i}", is_safety_topup=False, parent_pn="FG01")
            )
        bom = (BomLink(parent_pn="FG01", child_pn="SF01", ratio=1.0),)

        ds = _make_dataset(tasks=tuple(tasks), bom=bom, days=5)
        ctx = StructureContext.build(ds)

        snapshot, summary = solve_schedule(ctx, time_limit_seconds=10.0)
        self.assertIsNotNone(snapshot)
        self.assertIn(summary.status, ("OPTIMAL", "FEASIBLE"))
        self.assertLess(summary.wall_time_seconds, 10.0)

        # 验证提取的计划行守恒性
        plan_rows = extract_plan_rows(ctx, snapshot)
        self.assertGreater(len(plan_rows), 0)

        produced_by_task = {}
        for r in plan_rows:
            produced_by_task[r.task_id] = produced_by_task.get(r.task_id, 0) + r.quantity

        for t_id in ctx.task_ids:
            expected_qty = ctx.tasks_by_id[t_id].net_requirement
            actual_qty = produced_by_task.get(t_id, 0)
            self.assertEqual(expected_qty, actual_qty)

    def test_l2_gate_zero_lateness_on_unconstrained_demand(self):
        tasks = (
            Task(pn="FG01", stage="FG", net_requirement=48, due_date=START + dt.timedelta(days=3),
                 mpq=48, moq=None, mold_id="M1", family_code="F1", description=None,
                 is_safety_topup=False),
        )
        ds = _make_dataset(tasks=tasks, days=4)
        ctx = StructureContext.build(ds)

        snapshot, summary = solve_schedule(ctx, time_limit_seconds=5.0)
        self.assertIsNotNone(snapshot)
        self.assertEqual(0, summary.p1_lateness_shifts)


if __name__ == "__main__":
    unittest.main()
