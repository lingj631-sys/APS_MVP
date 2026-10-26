"""ACT-L3: labor master-data import and validation."""
import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.labor import (
    AVAILABILITY_FILE,
    POOL_FILE,
    STAFFING_FILE,
    load_labor_data,
    write_templates,
)


def _write(path: Path, headers, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


class TestLaborData(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aps_labor_"))

    def test_01_valid_lookup(self):
        _write(self.tmp / AVAILABILITY_FILE, ["site", "date", "shift", "available_headcount"], [["WL", "2026-10-12", "早班", 20]])
        _write(self.tmp / STAFFING_FILE, ["site", "line", "part_no", "crew_required", "changeover_extra", "skill"], [["WL", "PGLINKAGE", "*", 3, 0, ""]])
        _write(self.tmp / POOL_FILE, ["site", "pool_id", "line"], [["WL", "POOL-A", "PGLINKAGE"]])
        data = load_labor_data(self.tmp, site="WL")
        self.assertEqual(data.errors, [])
        self.assertEqual(data.available("WL", "2026-10-12", "早班"), 20)
        self.assertEqual(data.crew_required("WL", "PGLINKAGE", "WA14208S"), 3)
        self.assertEqual(data.pool("WL", "PGLINKAGE"), "POOL-A")
        self.assertEqual(data.pool("WL", "轴套6"), "__line__:轴套6")

    def test_02_negative_values_rejected(self):
        _write(self.tmp / AVAILABILITY_FILE, ["site", "date", "shift", "available_headcount"], [["WL", "2026-10-12", "早班", -1]])
        _write(self.tmp / STAFFING_FILE, ["site", "line", "part_no", "crew_required", "changeover_extra", "skill"], [["WL", "PGLINKAGE", "*", 0, 0, ""]])
        data = load_labor_data(self.tmp, site="WL")
        self.assertEqual(len(data.errors), 2)
        self.assertIsNone(data.available("WL", "2026-10-12", "早班"))

    def test_03_duplicate_pool_assignment_rejected(self):
        _write(self.tmp / POOL_FILE, ["site", "pool_id", "line"], [["WL", "POOL-A", "PGLINKAGE"], ["WL", "POOL-B", "PGLINKAGE"]])
        data = load_labor_data(self.tmp, site="WL")
        self.assertTrue(any("同时属于" in error for error in data.errors), data.errors)

    def test_04_missing_files_warn_but_load(self):
        data = load_labor_data(self.tmp, site="WL")
        self.assertEqual(data.errors, [])
        self.assertTrue(data.warnings)
        self.assertIsNone(data.available("WL", "2026-10-12", "早班"))

    def test_05_templates_are_importable(self):
        write_templates(self.tmp)
        data = load_labor_data(self.tmp, site="WL")
        self.assertEqual(data.errors, [])
        self.assertEqual(data.available("WL", "2026-10-12", "早班"), 20)
        self.assertEqual(data.crew_required("WL", "PGLINKAGE", "WA14208S"), 3)


if __name__ == "__main__":
    unittest.main()
