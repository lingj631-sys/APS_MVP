from __future__ import annotations

import datetime as dt
import unittest

from aps.calendar.geometry import build_slots
from aps.calendar.pattern import CalendarMode
from aps.dataset import DataSet, Horizon, LineGeometry, RouteOption, Task
from aps.model.context import StructureContext
from aps.model.plan_row import LineShiftSummary, PlanRow, extract_line_shifts, extract_plan_rows
from aps.model.report import BindingReport, build_binding_reports
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


def _make_dataset(tasks=(), degraded_fields=frozenset()) -> DataSet:
    horizon = Horizon(SITE, START, 2)
    slots = build_slots(LINES, START, 2, CalendarMode.CONTINUOUS)
    return DataSet(
        horizon=horizon,
        mode=CalendarMode.CONTINUOUS,
        lines=LINES,
        slots=slots,
        tasks=tasks,
        routes=ROUTES,
        bom=(),
        safety_stock={},
        mold_to_parts={"M1": frozenset({"FG01"}), "M2": frozenset({"SF01"})},
        initial_stock={},
        calendar_overrides={},
        degraded_fields=degraded_fields,
    )


class TestPlanRow(unittest.TestCase):
    def setUp(self) -> None:
        tasks = (
            Task(pn="FG01", stage="FG", net_requirement=96, due_date=START + dt.timedelta(days=1),
                 mpq=48, moq=None, mold_id="M1", family_code="F1", description="成品01",
                 is_safety_topup=False),
        )
        self.ds = _make_dataset(tasks=tasks)
        self.ctx = StructureContext.build(self.ds)
        self.solver, self.summary = solve_schedule(self.ctx, time_limit_seconds=5.0)
        self.assertIsNotNone(self.solver)

    def test_extract_plan_rows_non_empty(self):
        rows = extract_plan_rows(self.ctx, self.solver)
        self.assertGreater(len(rows), 0)
        for r in rows:
            self.assertIsInstance(r, PlanRow)
            self.assertEqual(SITE, r.site)
            self.assertIn(r.line_code, ("LINE_FG", "LINE_SF"))
            self.assertGreater(r.quantity, 0)
            self.assertGreaterEqual(r.end_min, r.start_min)
            self.assertIn(r.shift_name, ("早班", "中班", "晚班"))

    def test_extract_line_shifts_covers_all_slots(self):
        shifts = extract_line_shifts(self.ctx, self.solver)
        # 2 lines * 2 days * 3 shifts = 12 slots
        self.assertEqual(12, len(shifts))
        for s in shifts:
            self.assertIsInstance(s, LineShiftSummary)
            self.assertEqual(8.0, s.cap_hours)
            self.assertGreaterEqual(s.utilization_pct, 0.0)

    def test_build_binding_reports(self):
        reports = build_binding_reports(self.ctx)
        self.assertGreater(len(reports), 0)
        rule_ids = [rep.rule_id for rep in reports]
        self.assertIn("R1_LINE_ASSIGNMENT", rule_ids)
        self.assertIn("R2_NET_REQUIREMENT", rule_ids)
        self.assertIn("R9_UTILIZATION_95", rule_ids)

    def test_degraded_fields_reflected_in_report(self):
        ds_degraded = _make_dataset(
            tasks=self.ds.tasks,
            degraded_fields=frozenset({"demand.customer_code"}),
        )
        ctx = StructureContext.build(ds_degraded)
        reports = build_binding_reports(ctx)
        otd_rep = [r for r in reports if r.rule_id == "R8_CUSTOMER_PRIORITY_OTD"]
        self.assertEqual(1, len(otd_rep))
        self.assertEqual("DEGRADED", otd_rep[0].status)


if __name__ == "__main__":
    unittest.main()
