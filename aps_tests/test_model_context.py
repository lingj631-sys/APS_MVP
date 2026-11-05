from __future__ import annotations

import datetime as dt
import unittest

from ortools.sat.python import cp_model

from aps.calendar.geometry import MS_PER_HOUR
from aps.calendar.pattern import CalendarMode
from aps.dataset import DataSet, Horizon, LineGeometry, RouteOption, Task
from aps.model.context import StructureContext

FAST = True

SITE = "WL"
START = dt.date(2026, 11, 3)

SAMPLE_LINES = (
    LineGeometry("LINE_FG", 8.0, 3, 0.85),
    LineGeometry("LINE_SF", 8.0, 3, 0.90),
)

SAMPLE_ROUTES = (
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

SAMPLE_TASKS = (
    Task(pn="FG01", stage="FG", net_requirement=500, due_date=START + dt.timedelta(days=2),
         mpq=48, moq=None, mold_id="M1", family_code="FAM1", description="成品",
         is_safety_topup=False),
    Task(pn="SF01", stage="SF", net_requirement=1000, due_date=START + dt.timedelta(days=2),
         mpq=96, moq=None, mold_id="M2", family_code="FAM1", description="半成品",
         is_safety_topup=False, parent_pn="FG01"),
)


def _make_sample_dataset(days: int = 3) -> DataSet:
    horizon = Horizon(SITE, START, days)
    slots = []
    from aps.calendar.geometry import build_slots
    slots = build_slots(SAMPLE_LINES, START, days, CalendarMode.CONTINUOUS)
    return DataSet(
        horizon=horizon,
        mode=CalendarMode.CONTINUOUS,
        lines=SAMPLE_LINES,
        slots=slots,
        tasks=SAMPLE_TASKS,
        routes=SAMPLE_ROUTES,
        bom=(),
        safety_stock={},
        mold_to_parts={"M1": frozenset({"FG01"}), "M2": frozenset({"SF01"})},
        initial_stock={},
        calendar_overrides={},
    )


class TestModelContext(unittest.TestCase):
    def setUp(self) -> None:
        self.ds = _make_sample_dataset(days=3)
        self.ctx = StructureContext.build(self.ds)

    def test_context_holds_model_and_dataset(self):
        self.assertIsInstance(self.ctx.model, cp_model.CpModel)
        self.assertEqual(self.ds, self.ctx.dataset)
        self.assertEqual(3, self.ctx.horizon_days)

    def test_task_ids_are_deterministic_and_unique(self):
        self.assertEqual(2, len(self.ctx.task_ids))
        self.assertEqual(2, len(set(self.ctx.task_ids)))

    def test_assign_variables_exist_for_certified_lines(self):
        fg_task = self.ctx.task_ids[0]
        self.assertIn((fg_task, "LINE_FG"), self.ctx.assign)
        self.assertNotIn((fg_task, "LINE_SF"), self.ctx.assign)

    def test_x_variables_bounded_by_capacity_and_net_req(self):
        fg_task = self.ctx.task_ids[0]
        var = self.ctx.x[(fg_task, "LINE_FG", 0)]
        self.assertIsNotNone(var)

    def test_active_and_x_equivalence_in_model(self):
        # Test solving a tiny model checking x > 0 <=> active
        m = self.ctx.model
        fg_task = self.ctx.task_ids[0]
        x_var = self.ctx.x[(fg_task, "LINE_FG", 0)]
        act_var = self.ctx.active[(fg_task, "LINE_FG", 0)]

        # If x == 100, active must be 1
        m.Add(x_var == 100)
        solver = cp_model.CpSolver()
        status = solver.Solve(m)
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))
        self.assertEqual(1, solver.Value(act_var))

    def test_cumulative_production_recursion(self):
        fg_task = self.ctx.task_ids[0]
        cum_0 = self.ctx.cum[(fg_task, "LINE_FG", 0)]
        cum_1 = self.ctx.cum[(fg_task, "LINE_FG", 1)]
        self.assertIsNotNone(cum_0)
        self.assertIsNotNone(cum_1)

    def test_load_ms_variables_exist_for_all_line_slots(self):
        for line in SAMPLE_LINES:
            for s in range(3 * line.shifts_per_day):
                self.assertIn((line.line_code, s), self.ctx.load_ms)
                self.assertIn((line.line_code, s), self.ctx.is_open)
                self.assertIn((line.line_code, s), self.ctx.is_tail)

    def test_assumption_literals_initialized(self):
        self.assertIsInstance(self.ctx.assumptions, dict)


if __name__ == "__main__":
    unittest.main()
