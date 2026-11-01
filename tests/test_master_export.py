"""Site-specific template export tests (Tab-6 download)."""
import os
import tempfile
import unittest

import openpyxl

from src.data_loader import load_aps_database
from src.excel_loader import read_master_rows
from src.master_export import export_site_workbook
from src.master_schema import INPUT_SHEETS, validate_workbook

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(BASE_DIR, "APS_DataSource_Template.xlsx")
LEGACY = os.path.join(BASE_DIR, "dataSource.xlsx")


class TestSiteTemplateExport(unittest.TestCase):
    def _export(self, source, site):
        snap = load_aps_database(source, site_filter=site)
        masters = read_master_rows(source, site)
        out = tempfile.mktemp(suffix=".xlsx")
        export_site_workbook(snap, masters, TEMPLATE, out, site)
        self.addCleanup(lambda: os.path.exists(out) and os.remove(out))
        wb = openpyxl.load_workbook(out, data_only=True)
        try:
            report = validate_workbook(wb)
        finally:
            wb.close()
        return report, out

    def test_01_wl_export_keeps_template_schema_and_data(self):
        report, out = self._export(TEMPLATE, "WL")
        self.assertTrue(report.ok, f"{report.missing_sheets} {report.missing_columns}")
        snap = load_aps_database(out, site_filter="WL")
        self.assertEqual(len(snap.fg_params), 17)
        self.assertEqual(len(snap.sf_params), 22)
        self.assertEqual({l.line for l in snap.line_base}, {"PGLINKAGE", "轴套6", "轴套7"})
        self.assertEqual(len(snap.orders), 65)
        self.assertEqual(snap.warnings, [])

    def test_02_sy38_export_is_site_specific(self):
        report, out = self._export(LEGACY, "SY38")
        self.assertTrue(report.ok, f"{report.missing_sheets} {report.missing_columns}")
        snap = load_aps_database(out, site_filter="SY38")
        self.assertEqual(len(snap.fg_params), 11)
        self.assertEqual(len(snap.sf_params), 5)
        self.assertTrue(all(l.hours_per_shift == 12.0 and l.shifts_per_day == 2 for l in snap.line_base))
        self.assertIn("PU07", {l.line for l in snap.line_base})
        self.assertEqual(len(snap.orders), 16)
        self.assertEqual(snap.warnings, [])

    def test_03_instructions_cover_tables_and_fields(self):
        _, out = self._export(TEMPLATE, "WL")
        wb = openpyxl.load_workbook(out, data_only=True)
        try:
            ws = wb.worksheets[0]
            text = "\n".join(
                str(ws.cell(r, c).value or "")
                for r in range(1, ws.max_row + 1)
                for c in range(1, 8)
            )
        finally:
            wb.close()
        for section in ("一、使用流程", "二、全局填写规则", "三、表清单", "四、字段说明",
                        "五、跨表一致性规则", "六、系统校验", "七、版本与变更记录"):
            self.assertIn(section, text)
        for spec in INPUT_SHEETS:
            self.assertIn(spec.title, text, f"missing table guide: {spec.title}")
            for col in spec.active_columns():
                self.assertIn(col.header, text, f"missing field doc: {spec.title}.{col.header}")


if __name__ == "__main__":
    unittest.main()
