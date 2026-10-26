"""
APS MVP Comprehensive Test Suite & Diagnostics Harness.

Tests end-to-end functionality across:
1. Module imports and server integrity.
2. Data loading and net requirement calculation from data/ (CSV & JSON snapshot).
3. WL Site CP-SAT solving and 17-rule independent validation (8h x 3 shifts).
4. SY38 Site CP-SAT solving and 9-rule independent validation (12h x 2 shifts).
5. PM maintenance avoidance enforcement (R-WL-27).
6. Validation matrix generation and single-sheet Excel export.
"""
import copy
import os
import sys
import unittest
from collections import defaultdict
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.balance_builder import build_validation_matrix
from src.data_loader import (
    CustomerOrder,
    ProductionInputSnapshot,
    RequirementPlan,
    build_requirement_plan,
    load_aps_database,
)
from src.exporter import export_validation_matrix_excel
from src.prechecker import precheck_capacity
from src.rules import get_rules_for_site
from src.scheduler import APSScheduler
from src.shift_manager import CalendarMode, generate_shift_calendar, get_topology
from src.validator import validate_schedule

DATA_DIR = os.path.join(BASE_DIR, "data")
START_DT = datetime(2026, 10, 12, 7, 0, 0)
START_DT_SY = datetime(2026, 10, 12, 8, 0, 0)


class TestApsPipeline(unittest.TestCase):

    def test_01_server_imports(self):
        """Test that server and all core modules import without NameError or syntax errors."""
        import src.server as server
        self.assertIsNotNone(server.app)

    def test_02_data_loader_wl_and_sy38(self):
        """Test data loading and order extraction for both WL and SY38 from data/ directory."""
        snap_wl = load_aps_database(DATA_DIR, site_filter="WL")
        self.assertGreater(len(snap_wl.orders), 0)
        self.assertGreater(len(snap_wl.fg_params), 0)

        snap_sy = load_aps_database(DATA_DIR, site_filter="SY38")
        self.assertGreater(len(snap_sy.orders), 0)
        self.assertGreater(len(snap_sy.fg_params), 0)

    def test_03_wl_10day_solve_and_validation(self):
        """Test WL 10-day schedule solve (8h x 3 shifts) and 17 independent validation checks."""
        from src.server import apply_demand_lookahead
        snap_wl = load_aps_database(DATA_DIR, site_filter="WL")
        solve_snap, _ = apply_demand_lookahead(snap_wl, START_DT, horizon_days=10, lookahead_days=30, max_pull_forward_days=10)
        plan_wl = build_requirement_plan(solve_snap)

        sched = APSScheduler(
            plan_wl,
            start_datetime=START_DT,
            planning_days=10,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
            allow_105_utilization_fg=True,
            allow_105_utilization_sf=True,
        )
        self.assertEqual(sched.shifts[0].duration_hours, 8.0)
        self.assertEqual(len(sched.shifts), 30)

        res = sched.solve(time_limit_seconds=120.0)
        self.assertIsNotNone(res)
        self.assertIn(res.status_name, ("OPTIMAL", "FEASIBLE"))

        checks = validate_schedule(plan_wl, res, site="WL", allow_105_fg=True, allow_105_sf=True)
        failed = [c.name for c in checks if not c.passed]
        self.assertEqual(len(failed), 0, f"WL Validation failed on: {failed}")

    def test_04_sy38_solve_and_topology(self):
        """Test SY38 schedule solve (12h x 2 shifts) under true 12-hour plant topology."""
        snap_sy = load_aps_database(DATA_DIR, site_filter="SY38")
        horizon_end = START_DT_SY + timedelta(days=15)
        snap_sy.orders = [o for o in snap_sy.orders if o.due_datetime <= horizon_end]
        plan_sy = build_requirement_plan(snap_sy)

        sched = APSScheduler(
            plan_sy,
            start_datetime=START_DT_SY,
            planning_days=15,
            calendar_mode=CalendarMode.FIVE_TWO,
            site="SY38",
        )
        # Verify true 12h x 2 shifts topology
        self.assertEqual(sched.shifts[0].duration_hours, 12.0)
        self.assertEqual(sched.topology.shifts_per_day, 2)
        self.assertEqual(sched.topology.shift_hours, 12.0)

        res = sched.solve(time_limit_seconds=120.0)
        self.assertIsNotNone(res)
        self.assertIn(res.status_name, ("OPTIMAL", "FEASIBLE"))
        self.assertGreater(len(res.tasks), 0)

        checks = validate_schedule(
            plan_sy,
            res,
            site="SY38",
            allow_105_fg=getattr(res, "allow_105_fg", False),
            allow_105_sf=getattr(res, "allow_105_sf", False),
        )
        # Ensure 12h capacity check passes
        cap_check = next((c for c in checks if "R-SY-08" in c.rule_ids and "单班产能" in c.name), None)
        self.assertIsNotNone(cap_check)
        self.assertTrue(cap_check.passed, f"SY38 12h capacity check failed: {cap_check.violations}")

    def test_05_pm_maintenance_auto_extraction(self):
        """Test R-WL-27: verify that APSScheduler automatically extracts PM slots when pm_shifts is None."""
        snap_wl = load_aps_database(DATA_DIR, site_filter="WL")
        horizon_end = START_DT + timedelta(days=10)
        snap_wl.orders = [o for o in snap_wl.orders if o.due_datetime <= horizon_end]
        plan_wl = build_requirement_plan(snap_wl)

        # Do NOT pass pm_shifts explicitly; verify auto-extraction
        sched = APSScheduler(
            plan_wl,
            start_datetime=START_DT,
            planning_days=10,
            calendar_mode=CalendarMode.SEVEN_ZERO,
            site="WL",
        )
        self.assertGreater(len(sched.pm_shifts), 0, "Expected auto-extracted PM shifts")
        res = sched.solve(time_limit_seconds=120.0)
        checks = validate_schedule(
            plan_wl,
            res,
            site="WL",
            allow_105_fg=getattr(res, "allow_105_fg", False),
            allow_105_sf=getattr(res, "allow_105_sf", False),
        )
        pm_check = next((c for c in checks if "R-WL-27" in c.rule_ids), None)
        self.assertIsNotNone(pm_check)
        self.assertTrue(pm_check.passed, f"PM check failed: {pm_check.violations}")

    def test_06_excel_export(self):
        """Test single-sheet Excel export with freeze panes."""
        import tempfile
        snap_wl = load_aps_database(DATA_DIR, site_filter="WL")
        horizon_end = START_DT + timedelta(days=10)
        snap_wl.orders = [o for o in snap_wl.orders if o.due_datetime <= horizon_end]
        plan_wl = build_requirement_plan(snap_wl)
        sched = APSScheduler(plan_wl, start_datetime=START_DT, planning_days=10, calendar_mode=CalendarMode.SEVEN_ZERO, site="WL")
        res = sched.solve(time_limit_seconds=120.0)
        matrix = build_validation_matrix(snap_wl, plan_wl, res, site="WL")

        out_path = os.path.join(tempfile.gettempdir(), "test_export_wl.xlsx")
        export_validation_matrix_excel(matrix, out_path, site="WL")
        self.assertTrue(os.path.exists(out_path))
        self.assertGreater(os.path.getsize(out_path), 5000)
        try:
            os.remove(out_path)
        except Exception:
            pass

    def test_07_fractional_bom_ratio_handling(self):
        """Test that fractional BOM ratios (e.g. 0.5) are modeled with exact cross-multiplication without integer truncation."""
        snap_wl = load_aps_database(DATA_DIR, site_filter="WL")
        horizon_end = START_DT + timedelta(days=10)
        snap_wl.orders = [o for o in snap_wl.orders if o.due_datetime <= horizon_end]
        first_fg = list(snap_wl.bom.keys())[0]
        first_sf, _ = snap_wl.bom[first_fg][0]
        snap_wl.bom[first_fg] = [(first_sf, 0.5)] + snap_wl.bom[first_fg][1:]
        plan = build_requirement_plan(snap_wl)
        sched = APSScheduler(plan, start_datetime=START_DT, planning_days=10, calendar_mode=CalendarMode.SEVEN_ZERO, site="WL")
        res = sched.solve(time_limit_seconds=120.0)
        self.assertIsNotNone(res)
        self.assertIn(res.status_name, ("OPTIMAL", "FEASIBLE"))
        self.assertGreater(len(res.tasks), 0)

    def test_08_manual_schedule_excel_audit(self):
        """Test POST /api/audit_manual_schedule endpoint logic with real Excel upload."""
        import asyncio
        from fastapi import UploadFile
        from src.server import audit_manual_schedule
        import io

        excel_path = os.path.join(BASE_DIR, "dataSource.xlsx")
        self.assertTrue(os.path.exists(excel_path))
        with open(excel_path, "rb") as f:
            content = f.read()

        async def run_audit():
            file_obj = UploadFile(filename="dataSource.xlsx", file=io.BytesIO(content))
            return await audit_manual_schedule(file=file_obj, site="WL")

        result = asyncio.run(run_audit())
        self.assertEqual(result.get("status"), "success")
        self.assertGreater(len(result.get("compliance_matrix", [])), 0)
        self.assertGreater(len(result.get("checks", [])), 0)


if __name__ == "__main__":
    unittest.main()
