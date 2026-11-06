from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest

from openpyxl import load_workbook

from aps.calendar.geometry import build_slots
from aps.calendar.pattern import CalendarMode
from aps.dataset import DataSet, Horizon, LineGeometry, RouteOption, Task
from aps.export.excel import export_production_plan
from aps.model.context import StructureContext
from aps.model.plan_row import extract_line_shifts, extract_plan_rows
from aps.model.report import build_binding_reports
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


def _make_dataset(tasks=()) -> DataSet:
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
    )


class TestExportExcel(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out_path = os.path.join(self._tmp.name, "plan.xlsx")

        tasks = (
            Task(pn="FG01", stage="FG", net_requirement=96, due_date=START + dt.timedelta(days=1),
                 mpq=48, moq=None, mold_id="M1", family_code="F1", description="电机总成",
                 is_safety_topup=False),
        )
        self.ds = _make_dataset(tasks=tasks)
        self.ctx = StructureContext.build(self.ds)
        self.solver, self.summary = solve_schedule(self.ctx, time_limit_seconds=5.0)
        self.assertIsNotNone(self.solver)

        self.plan_rows = extract_plan_rows(self.ctx, self.solver)
        self.shift_summaries = extract_line_shifts(self.ctx, self.solver)
        self.binding_reports = build_binding_reports(self.ctx)

        export_production_plan(
            plan_rows=self.plan_rows,
            shift_summaries=self.shift_summaries,
            solve_summary=self.summary,
            binding_reports=self.binding_reports,
            tasks=self.ds.tasks,
            out_path=self.out_path,
        )

    def test_workbook_created_with_four_sheets(self):
        self.assertTrue(os.path.exists(self.out_path))
        wb = load_workbook(self.out_path)
        self.addCleanup(wb.close)
        expected_sheets = [
            "生产计划明细 Plan Details",
            "产线班次负荷 Line Shifts",
            "订单交付汇总 Order Delivery",
            "求解报告 Solver Report",
        ]
        self.assertEqual(expected_sheets, wb.sheetnames)

    def test_plan_details_sheet_content(self):
        wb = load_workbook(self.out_path)
        self.addCleanup(wb.close)
        ws = wb["生产计划明细 Plan Details"]
        self.assertGreater(ws.max_row, 1)
        headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
        self.assertIn("产线", headers)
        self.assertIn("料号", headers)
        self.assertIn("排产件数", headers)

    def test_line_shifts_sheet_content(self):
        wb = load_workbook(self.out_path)
        self.addCleanup(wb.close)
        ws = wb["产线班次负荷 Line Shifts"]
        self.assertGreater(ws.max_row, 1)
        headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
        self.assertIn("产线", headers)
        self.assertIn("实际负荷 (小时)", headers)
        self.assertIn("负荷利用率%", headers)

    def test_order_delivery_sheet_content(self):
        wb = load_workbook(self.out_path)
        self.addCleanup(wb.close)
        ws = wb["订单交付汇总 Order Delivery"]
        self.assertGreater(ws.max_row, 1)
        headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
        self.assertIn("需求件数", headers)
        self.assertIn("实排件数", headers)
        self.assertIn("交付状态", headers)

    def test_order_delivery_delay_shifts_calculation_when_delayed(self):
        # Construct scenario where due date is 2026-11-03, and finish is on 2026-11-04 slot 4 (1 shift late)
        from aps.model.plan_row import PlanRow
        fake_task = Task(pn="FG01", stage="FG", net_requirement=10, due_date=START,
                         mpq=1, moq=None, mold_id="M1", family_code="F1", description="电机",
                         is_safety_topup=False)
        fake_p = PlanRow(
            site=SITE, line_code="LINE_FG", date_str="2026-11-04", shift_name="早班",
            slot_index=3, task_id="FG_FG01_2026-11-03_0", pn="FG01", stage="FG",
            quantity=10, start_min=0.0, end_min=10.0, setup_min=0.0,
            is_tail_shift=False
        )
        fake_out = os.path.join(self._tmp.name, "delayed.xlsx")
        export_production_plan(
            plan_rows=[fake_p],
            shift_summaries=self.shift_summaries,
            solve_summary=self.summary,
            binding_reports=self.binding_reports,
            tasks=[fake_task],
            out_path=fake_out,
        )
        wb = load_workbook(fake_out)
        self.addCleanup(wb.close)
        ws = wb["订单交付汇总 Order Delivery"]
        # Delay shifts is column 10, should be 1 shift late (slot 3 vs last slot of 11-03 which is slot 2: 3 - 2 = 1)
        delay_val = ws.cell(2, 10).value
        self.assertEqual(1, delay_val)

    def test_solver_report_sheet_content(self):
        wb = load_workbook(self.out_path)
        self.addCleanup(wb.close)
        ws = wb["求解报告 Solver Report"]
        values = [ws.cell(r, 1).value for r in range(1, ws.max_row + 1)]
        text = " ".join(str(v) for v in values if v is not None)
        self.assertIn("求解总览", text)
        self.assertIn("四层字典序多目标达成", text)
        self.assertIn("规则生效状态报告", text)


if __name__ == "__main__":
    unittest.main()
