from __future__ import annotations

import contextlib
import datetime as dt
import io
import os
import tempfile
import unittest

from openpyxl import load_workbook

from aps.calendar.geometry import build_slots
from aps.calendar.pattern import CalendarMode
from aps.cli import DEFAULT_DB, DEFAULT_SITE, EXIT_OK, EXIT_VALIDATION, build_parser, main
from aps.data.sqlite import SqliteRepository
from aps.data.template_v3 import write_v3_template

FAST = True


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class TestCliSolve(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workbook = os.path.join(self._tmp.name, "v3.xlsx")
        self.db = os.path.join(self._tmp.name, "aps.db")
        self.out_plan = os.path.join(self._tmp.name, "plan.xlsx")

        write_v3_template(self.workbook, DEFAULT_SITE)

    def test_solve_subcommand_registered(self):
        parser = build_parser()
        choices = parser._subparsers._group_actions[0].choices
        self.assertIn("solve", choices)

    def test_solve_empty_template_succeeds(self):
        code, out, _ = run([
            "solve",
            "--workbook", self.workbook,
            "--db", self.db,
            "--site", DEFAULT_SITE,
            "--start", "2026-11-03",
            "--days", "3",
            "--out", self.out_plan,
            "--timeout", "10",
        ])
        self.assertEqual(EXIT_OK, code)
        self.assertIn("求解成功", out)
        self.assertIn(self.out_plan, out)
        self.assertTrue(os.path.exists(self.out_plan))

        wb = load_workbook(self.out_plan)
        self.addCleanup(wb.close)
        self.assertEqual(4, len(wb.sheetnames))

    def test_solve_missing_db_returns_2(self):
        code, _, err = run([
            "solve",
            "--db", os.path.join(self._tmp.name, "ghost.db"),
            "--site", DEFAULT_SITE,
            "--start", "2026-11-03",
            "--days", "3",
        ])
        self.assertEqual(2, code)
        self.assertIn("数据库不存在", err)


if __name__ == "__main__":
    unittest.main()
