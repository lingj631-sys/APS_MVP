"""Regression tests for the 2026-10 master-data framework (15-sheet v2)."""
import os
import re
import tempfile
import unittest
import zipfile

import openpyxl

from src.data_loader import load_aps_database
from src.excel_loader import read_master_rows
from src.master_schema import SHEET_BY_KEY, resolve_sheet, validate_workbook

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V2_PATH = os.path.join(BASE_DIR, "APS_DataSource_Template.xlsx")
LEGACY_PATH = os.path.join(BASE_DIR, "dataSource.xlsx")


def _strip_dimension(src: str, dst: str) -> None:
    """Write ``dst`` as a copy of ``src`` with every ``<dimension>`` removed.

    WPS Office, several JS exporters and some ERP exports save xlsx files
    without the ``<dimension>`` element.  openpyxl then reports ``max_row`` and
    ``max_column`` as ``None`` for read-only sheets.
    """
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.startswith("xl/worksheets/") and item.filename.endswith(".xml"):
                data = re.sub(rb"<dimension[^>]*/>", b"", data)
            zout.writestr(item, data)


class TestMasterSchema(unittest.TestCase):
    def test_01_v2_template_is_valid(self):
        wb = openpyxl.load_workbook(V2_PATH, data_only=True)
        try:
            report = validate_workbook(wb)
        finally:
            wb.close()
        self.assertEqual(report.layout, "v2")
        self.assertTrue(report.ok, f"missing={report.missing_sheets} cols={report.missing_columns}")
        self.assertEqual(len(report.sheets), 15)
        self.assertEqual(report.optional_missing, [])

    def test_02_legacy_workbook_still_valid(self):
        wb = openpyxl.load_workbook(LEGACY_PATH, data_only=True)
        try:
            report = validate_workbook(wb)
        finally:
            wb.close()
        self.assertEqual(report.layout, "legacy")
        self.assertTrue(report.ok, f"missing={report.missing_sheets} cols={report.missing_columns}")

    def test_03_missing_required_column_is_reported(self):
        wb = openpyxl.load_workbook(V2_PATH)
        try:
            ws = resolve_sheet(wb, SHEET_BY_KEY["parameters_fg"])
            for col in range(1, ws.max_column + 1):
                if str(ws.cell(1, col).value or "").strip() == "最小包装量 MPQ":
                    ws.cell(1, col).value = "包装数量（旧名）"
                    break
            report = validate_workbook(wb)
        finally:
            wb.close()
        self.assertIn(SHEET_BY_KEY["parameters_fg"].title, report.missing_columns)
        self.assertIn("最小包装量 MPQ", report.missing_columns[SHEET_BY_KEY["parameters_fg"].title])

    def test_04_column_order_can_change(self):
        """The template says column order is free; the loader must follow headers."""
        wb = openpyxl.load_workbook(V2_PATH)
        try:
            ws = resolve_sheet(wb, SHEET_BY_KEY["parameters_fg"])
            rows = [[ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
                    for r in range(1, ws.max_row + 1)]
            title = ws.title
            wb.remove(ws)
            new_ws = wb.create_sheet(title)
            for row in rows:
                new_ws.append(list(reversed(row)))
            tmp = tempfile.mktemp(suffix=".xlsx")
            wb.save(tmp)
        finally:
            wb.close()
        try:
            snap = load_aps_database(tmp, site_filter="WL")
            self.assertEqual(len(snap.fg_params), 17)
            self.assertEqual(snap.fg_params["WA14208"].mpq, 48)
            self.assertEqual(snap.fg_params["WA14208"].description, "DX11电机连杆总成")
            self.assertEqual(snap.fg_params["WA14208"].ct_seconds, 38.0)
        finally:
            os.remove(tmp)

    def test_05_gray_columns_can_be_deleted(self):
        """The planner deletes the gray columns by hand; the upload must still load."""
        wb = openpyxl.load_workbook(V2_PATH)
        try:
            for spec in SHEET_BY_KEY.values():
                if spec.key == "instructions":
                    continue
                ws = resolve_sheet(wb, spec)
                if ws is None:
                    continue
                positions = {}
                for col in spec.columns:
                    if not col.deprecated:
                        continue
                    for c in range(1, ws.max_column + 1):
                        if str(ws.cell(1, c).value or "").strip() == col.header:
                            positions[c] = col.header
                for c in sorted(positions, reverse=True):
                    ws.delete_cols(c)
            tmp = tempfile.mktemp(suffix=".xlsx")
            wb.save(tmp)
        finally:
            wb.close()
        try:
            report_wb = openpyxl.load_workbook(tmp, data_only=True)
            try:
                report = validate_workbook(report_wb)
            finally:
                report_wb.close()
            self.assertTrue(report.ok, f"missing={report.missing_sheets} cols={report.missing_columns}")
            self.assertEqual(report.deprecated_present, {})
            snap = load_aps_database(tmp, site_filter="WL")
            self.assertEqual(len(snap.fg_params), 17)
            self.assertEqual(snap.fg_params["WA14208"].line, "PGLINKAGE")
            self.assertEqual(snap.fg_params["WA14208"].trp, 0.71)
            self.assertEqual(snap.sf_params["W000061536"].line, "轴套6")
            self.assertEqual(snap.sf_params["W000061536"].trp, 0.85)
            self.assertEqual(snap.fg_params["WA14208"].ct_seconds, 38.0)
        finally:
            os.remove(tmp)

    def test_06_workbook_without_dimension_elements_still_uploads(self):
        """WPS/JS exporters omit <dimension>; that used to crash the header scan."""
        tmp = tempfile.mktemp(suffix=".xlsx")
        _strip_dimension(V2_PATH, tmp)
        try:
            ro_wb = openpyxl.load_workbook(tmp, read_only=True, data_only=True)
            try:
                self.assertIsNone(ro_wb.worksheets[0].max_row)  # precondition
                self.assertIsNone(ro_wb.worksheets[0].max_column)
                report = validate_workbook(ro_wb)
            finally:
                ro_wb.close()
            self.assertTrue(report.ok, f"missing={report.missing_sheets} cols={report.missing_columns}")
            self.assertEqual(report.layout, "v2")

            snap = load_aps_database(tmp, site_filter="WL")
            self.assertEqual(len(snap.fg_params), 17)
            self.assertEqual(len(snap.sf_params), 22)
            self.assertEqual(snap.fg_params["WA14208"].mpq, 48)
            self.assertEqual(snap.fg_params["WAB6404"].ct_seconds, 46.0)
        finally:
            os.remove(tmp)


class TestV2Loader(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v2 = load_aps_database(V2_PATH, site_filter="WL")
        cls.legacy = load_aps_database(LEGACY_PATH, site_filter="WL")

    def test_01_same_core_dataset_as_legacy(self):
        self.assertEqual(len(self.v2.fg_params), len(self.legacy.fg_params))
        # v2 adds the two CX11 axle sub-assemblies referenced by WB80510 BOM.
        self.assertEqual(len(self.v2.sf_params), len(self.legacy.sf_params) + 2)
        self.assertTrue({"W268001", "W268002"} <= set(self.v2.sf_params))
        self.assertEqual(set(self.v2.bom), set(self.legacy.bom))
        self.assertEqual(len(self.v2.orders), len(self.legacy.orders))
        self.assertEqual(len(self.v2.initial_stock), len(self.legacy.initial_stock))
        self.assertEqual(len(self.v2.tooling_molds), len(self.legacy.tooling_molds))
        for pn, param in self.v2.fg_params.items():
            old = self.legacy.fg_params[pn]
            self.assertAlmostEqual(param.ct_seconds, old.ct_seconds, places=6, msg=pn)
            self.assertAlmostEqual(param.trp, old.trp, places=6, msg=pn)
            self.assertAlmostEqual(param.mpq, old.mpq, places=6, msg=pn)
            self.assertAlmostEqual(param.moq, old.moq, places=6, msg=pn)
            self.assertEqual(param.description, old.description)

    def test_02_ct_and_headcount_come_from_routing(self):
        # FG/SFN no longer carry CT; the value is taken from Routing-Machines.
        self.assertEqual(self.v2.fg_params["WAB6404"].ct_seconds, 46.0)
        self.assertEqual(self.v2.sf_params["WAB6407"].ct_seconds, 27.0)
        # 人数 was filled with simulated values: FG 3 / SF 2 per shift.
        self.assertEqual(self.v2.fg_params["WA14208"].headcount, 3.0)
        self.assertEqual(self.v2.sf_params["W000061536"].headcount, 2.0)
        self.assertEqual(self.legacy.fg_params["WA14208"].headcount, 3.0)

    def test_03_target_stock_comes_from_inventory_policy(self):
        # Legacy had Target Stock = 0; v2 derives the safety stock instead.
        self.assertEqual(self.legacy.fg_params["WA14208"].target_stock, 0.0)
        self.assertEqual(self.v2.fg_params["WA14208"].target_stock, 24.0)

    def test_04_line_base_is_deduplicated_and_derived(self):
        lines = {lb.line: lb for lb in self.v2.line_base}
        self.assertEqual(set(lines), {"PGLINKAGE", "轴套6", "轴套7"})
        self.assertEqual(lines["PGLINKAGE"].source, "产线基础数据")
        self.assertEqual(lines["PGLINKAGE"].trp, 0.71)
        self.assertEqual(lines["轴套6"].trp, 0.85)
        self.assertEqual(lines["轴套6"].source, "产线基础数据")

    def test_05_mold_id_comes_from_campaign_and_tooling(self):
        self.assertEqual(self.v2.fg_params["WB35356"].mold_id, "MOLD_WL_LINK_01")
        self.assertEqual(self.v2.fg_params["WD48696"].mold_id, "MOLD_WL_LINK_02")
        self.assertEqual(self.v2.sf_params["WAB6407"].mold_id, "MOLD_WL_AXLE_03")
        self.assertFalse(any("模具编号" in w and "AA" in w for w in self.v2.warnings))

    def test_06_lead_time_comes_from_operation_rest_time(self):
        # 新版不再使用 SFN 的“生产提前期”，而是由工序提前期的静置时间推导。
        self.assertEqual(self.v2.sf_params["W000061536"].lead_time_days, 0.0)
        self.assertFalse(any("生产提前期" in w for w in self.v2.warnings))
        rows = read_master_rows(V2_PATH, "WL")
        prep = {r["part_no"]: r["prep_minutes"] for r in rows["operation_leadtime"]}
        self.assertEqual(prep["W000061536"], 45)      # 可 45min 极限转运
        self.assertEqual(prep["WA14222"], 480)        # 静置 8h
        self.assertEqual(prep["W268001"], 240)        # CX11 静置 4h

    def test_07_new_fields_are_parsed(self):
        self.assertEqual(self.v2.fg_params["WB35356"].simultaneous_production, "Y")
        self.assertEqual(self.v2.fg_params["WA14208"].simultaneous_production, "")
        self.assertEqual(self.v2.layout, "v2")
        self.assertEqual(self.legacy.layout, "legacy")


if __name__ == "__main__":
    unittest.main()
