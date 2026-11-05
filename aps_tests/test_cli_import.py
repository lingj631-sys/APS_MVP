from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import unittest

from aps.cli import (
    DEFAULT_DB,
    DEFAULT_SITE,
    EXIT_OK,
    EXIT_USAGE,
    EXIT_VALIDATION,
    build_parser,
    main,
)
from aps.data.template_v3 import INSTRUCTIONS_SHEET, SHEET_ORDER
from aps.schema.registry import REGISTRY

FAST = True


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.template = os.path.join(self._tmp.name, "v3.xlsx")
        self.db = os.path.join(self._tmp.name, "aps.db")
        code, self.gen_out, _ = run(
            ["template", "--out", self.template, "--site", DEFAULT_SITE]
        )
        self.assertEqual(EXIT_OK, code)

    def import_template(self, db=None):
        return run(["import", "--workbook", self.template,
                    "--db", db or self.db, "--site", DEFAULT_SITE])


class TestParser(unittest.TestCase):
    def test_subcommands_are_registered(self):
        parser = build_parser()
        choices = parser._subparsers._group_actions[0].choices
        self.assertEqual(
            {"template", "import", "verify-template", "show", "dataset", "solve"}, set(choices)
        )

    def test_missing_command_exits_2(self):
        with self.assertRaises(SystemExit) as caught:
            with contextlib.redirect_stderr(io.StringIO()):
                main([])
        self.assertEqual(2, caught.exception.code)

    def test_unknown_command_exits_2(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                main(["nope"])

    def test_import_requires_workbook(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                main(["import"])

    def test_defaults(self):
        args = build_parser().parse_args(["import", "--workbook", "w.xlsx"])
        self.assertEqual(DEFAULT_DB, args.db)
        self.assertEqual(DEFAULT_SITE, args.site)
        self.assertEqual(40, args.limit)

    def test_demand_start_is_parsed_to_date(self):
        args = build_parser().parse_args(
            ["template", "--out", "o.xlsx", "--demand-start", "2027-05-01"]
        )
        self.assertEqual(dt.date(2027, 5, 1), args.demand_start)


class TestTemplateCommand(CliCase):
    def test_writes_a_workbook_with_every_sheet(self):
        from openpyxl import load_workbook

        wb = load_workbook(self.template)
        self.addCleanup(wb.close)
        self.assertEqual([INSTRUCTIONS_SHEET, *SHEET_ORDER], wb.sheetnames)

    def test_stdout_names_the_output_file(self):
        self.assertIn(self.template, self.gen_out)

    def test_honours_demand_window(self):
        path = os.path.join(self._tmp.name, "short.xlsx")
        code, _, _ = run(["template", "--out", path, "--site", "WL",
                          "--demand-start", "2027-01-01", "--demand-days", "3"])
        self.assertEqual(EXIT_OK, code)
        from openpyxl import load_workbook

        wb = load_workbook(path)
        self.addCleanup(wb.close)
        ws = wb["客户需求订单清单 Customer Demand"]
        self.assertEqual(dt.datetime(2027, 1, 1), ws.cell(1, 5).value)
        self.assertEqual(dt.datetime(2027, 1, 3), ws.cell(1, 7).value)
        self.assertIsNone(ws.cell(1, 8).value)

    def test_missing_out_argument_exits_2(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                main(["template", "--site", "WL"])


class TestImportCommand(CliCase):
    def test_empty_template_imports_ok(self):
        code, out, _ = self.import_template()
        self.assertEqual(EXIT_OK, code)
        self.assertIn("导入完成", out)

    def test_site_and_calendar_pattern_are_seeded(self):
        self.import_template()
        code, out, _ = run(["show", "--db", self.db, "--site", DEFAULT_SITE])
        self.assertEqual(EXIT_OK, code)
        self.assertIn("site", out)
        counts = dict(
            (line.split()[0], int(line.split()[1]))
            for line in out.strip().splitlines()
            if line.strip() and not line.startswith("共")
        )
        self.assertEqual(1, counts["site"])
        self.assertEqual(4, counts["calendar_pattern"])
        self.assertEqual(0, counts["routing"])

    def test_missing_workbook_returns_2(self):
        code, _, err = run(["import", "--workbook", os.path.join(self._tmp.name, "ghost.xlsx"),
                            "--db", self.db, "--site", "WL"])
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("ghost.xlsx", err)
        self.assertFalse(os.path.exists(self.db))

    def test_report_limit_is_forwarded(self):
        code, out, _ = run(["import", "--workbook", self.template,
                            "--db", self.db, "--site", "WL", "--limit", "1"])
        self.assertEqual(EXIT_OK, code)
        self.assertIn("error=0", out)


class TestVerifyTemplateCommand(unittest.TestCase):
    def test_returns_zero_and_reports_the_generated_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "v3.xlsx")
            code, out, _ = run(["verify-template", "--out", out_path, "--site", "WL"])
            self.assertEqual(EXIT_OK, code)
            self.assertIn(out_path, out)
            self.assertIn("导入完成", out)
            self.assertTrue(os.path.exists(out_path))

    def test_verify_template_does_not_touch_default_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                run(["verify-template", "--out", os.path.join(tmp, "v3.xlsx"), "--site", "WL"])
                self.assertFalse(os.path.exists(DEFAULT_DB))
            finally:
                os.chdir(cwd)

    def test_verify_template_accepts_no_db_argument(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                main(["verify-template", "--out", "v3.xlsx", "--db", "x.db"])


class TestShowCommand(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.import_template()

    def test_counts_lists_every_registered_entity(self):
        code, out, _ = run(["show", "--db", self.db, "--site", DEFAULT_SITE])
        self.assertEqual(EXIT_OK, code)
        for name in REGISTRY:
            self.assertIn(name, out)

    def test_entity_rows_are_json(self):
        code, out, _ = run(["show", "--db", self.db, "--site", DEFAULT_SITE,
                            "--entity", "calendar_pattern"])
        self.assertEqual(EXIT_OK, code)
        rows = json.loads(out)
        self.assertEqual(4, len(rows))
        self.assertEqual(
            ["4ON3OFF", "5ON2OFF", "6ON1OFF", "CONTINUOUS"],
            sorted(row["pattern_code"] for row in rows),
        )

    def test_entity_without_site_column_is_still_listable(self):
        code, out, _ = run(["show", "--db", self.db, "--site", DEFAULT_SITE,
                            "--entity", "calendar_pattern", "--limit", "2"])
        self.assertEqual(EXIT_OK, code)
        self.assertEqual(2, len(json.loads(out)))

    def test_unknown_entity_returns_2(self):
        code, _, err = run(["show", "--db", self.db, "--site", DEFAULT_SITE,
                            "--entity", "ghost"])
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("ghost", err)

    def test_missing_db_returns_2(self):
        code, _, err = run(["show", "--db", os.path.join(self._tmp.name, "ghost.db"),
                            "--site", DEFAULT_SITE])
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("先运行 import", err)


class TestDatasetCommand(CliCase):
    def setUp(self) -> None:
        super().setUp()
        self.import_template()

    def test_empty_dataset_still_loads(self):
        code, out, _ = run(["dataset", "--db", self.db, "--site", DEFAULT_SITE,
                            "--start", "2026-11-03", "--days", "10"])
        self.assertEqual(EXIT_OK, code)
        self.assertIn("2026-11-03", out)
        self.assertIn("2026-11-12", out)
        self.assertIn("CONTINUOUS", out)

    def test_zero_lines_yield_zero_slots(self):
        code, out, _ = run(["dataset", "--db", self.db, "--site", DEFAULT_SITE,
                            "--start", "2026-11-03", "--days", "10"])
        self.assertEqual(EXIT_OK, code)
        self.assertIn("产线 0", out)
        self.assertIn("槽位 0", out)

    def test_explicit_calendar_mode_is_honoured(self):
        code, out, _ = run(["dataset", "--db", self.db, "--site", DEFAULT_SITE,
                            "--start", "2026-11-03", "--days", "10",
                            "--calendar-mode", "CONTINUOUS"])
        self.assertEqual(EXIT_OK, code)
        self.assertIn("CONTINUOUS", out)

    def test_unknown_calendar_mode_returns_2(self):
        code, _, err = run(["dataset", "--db", self.db, "--site", DEFAULT_SITE,
                            "--start", "2026-11-03", "--days", "10",
                            "--calendar-mode", "NOPE"])
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("NOPE", err)

    def test_start_and_days_are_required(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                main(["dataset", "--db", self.db, "--site", DEFAULT_SITE])

    def test_missing_db_returns_2(self):
        code, _, err = run(["dataset", "--db", os.path.join(self._tmp.name, "ghost.db"),
                            "--site", DEFAULT_SITE, "--start", "2026-11-03", "--days", "3"])
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("先运行 import", err)


if __name__ == "__main__":
    unittest.main()
