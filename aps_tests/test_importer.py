from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from aps.data.importer import ImportResult, import_workbook, rows_from_parsed
from aps.data.issues import IssueLog
from aps.data.sqlite import SqliteRepository
from aps.data.validate import CandidateRow, ValidationReport

FAST = True

SITE = "WL"
WORKBOOK = "APS_DataSource_Template - WL -V2.xlsx"

LINE_ROW = {"site_code": SITE, "line_code": "PGLINKAGE", "name": "PGLINKAGE",
            "trp_efficiency": 0.85, "shift_hours": 8.0, "shifts_per_day": 3}


def _report(errors=(), warnings=(), gaps=()) -> ValidationReport:
    return ValidationReport(errors=tuple(errors), warnings=tuple(warnings),
                            gaps=tuple(gaps))


def _gap_issue(entity: str, field: str):
    log = IssueLog()
    log.gap(entity=entity, field=field, sheet=None, row=None, column=None,
            message="T2 字段整列为空")
    return log.issues[0]


def _error_issue(entity: str, field: str):
    log = IssueLog()
    log.error(entity=entity, field=field, sheet=None, row=None, column=None,
              message="缺少必需列（T1）")
    return log.issues[0]


def _warning_issue(entity: str, field: str):
    log = IssueLog()
    log.warning(entity=entity, field=field, sheet=None, row=None, column=None,
                message="模板未定义的多余列，已忽略")
    return log.issues[0]


def _parsed_line(code: str = "PGLINKAGE") -> dict[str, list[CandidateRow]]:
    return {"line": [CandidateRow(entity="line", excel_row=2,
                                  values=dict(LINE_ROW, line_code=code, name=code))]}


class ImporterCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = os.path.join(self._tmp.name, "aps.db")

    def run_import(self, parsed, report, tables=None, log=None):
        tables = {} if tables is None else tables
        log = IssueLog() if log is None else log
        with patch("aps.data.importer.read_workbook",
                   return_value=(tables, log)) as reader, \
                patch("aps.data.importer.validate",
                      return_value=(parsed, report)) as validator:
            result = import_workbook(WORKBOOK, self.db, SITE)
        self.reader = reader
        self.validator = validator
        return result

    def repo(self) -> SqliteRepository:
        repo = SqliteRepository(self.db)
        self.addCleanup(repo.close)
        return repo


class TestRowsFromParsed(unittest.TestCase):
    def test_converts_candidates_to_plain_dicts(self):
        rows = rows_from_parsed(_parsed_line())
        self.assertEqual([dict(LINE_ROW)], rows["line"])

    def test_returns_a_copy_not_the_original(self):
        candidate = CandidateRow(entity="line", excel_row=2, values=dict(LINE_ROW))
        rows = rows_from_parsed({"line": [candidate]})
        rows["line"][0]["line_code"] = "MUTATED"
        self.assertEqual("PGLINKAGE", candidate.values["line_code"])

    def test_keys_are_sorted(self):
        parsed = {
            "routing": [CandidateRow("routing", 2, {"pn": "P"})],
            "bom": [CandidateRow("bom", 2, {"parent_pn": "P"})],
            "line": [CandidateRow("line", 2, dict(LINE_ROW))],
        }
        self.assertEqual(["bom", "line", "routing"], list(rows_from_parsed(parsed)))

    def test_empty_table_yields_empty_list(self):
        self.assertEqual({"line": []}, rows_from_parsed({"line": []}))


class TestFailureBlocksWrite(ImporterCase):
    def test_wrote_is_false(self):
        result = self.run_import(_parsed_line(), _report([_error_issue("line", "line_code")]))
        self.assertFalse(result.wrote)
        self.assertFalse(result.ok)

    def test_failing_import_does_not_create_db_file(self):
        self.run_import(_parsed_line(), _report([_error_issue("line", "line_code")]))
        self.assertFalse(os.path.exists(self.db))

    def test_counts_are_empty(self):
        result = self.run_import(_parsed_line(), _report([_error_issue("line", "line_code")]))
        self.assertEqual({}, dict(result.counts))

    def test_existing_data_survives_a_failing_reimport(self):
        self.run_import(_parsed_line("GOOD"), _report())
        self.run_import(_parsed_line("BAD"), _report([_error_issue("line", "line_code")]))
        repo = self.repo()
        self.assertEqual(["GOOD"], [r["line_code"] for r in repo.list_by("line")])

    def test_warnings_alone_do_not_block(self):
        result = self.run_import(_parsed_line(),
                                 _report(warnings=[_warning_issue("line", "name")]))
        self.assertTrue(result.wrote)
        self.assertTrue(result.ok)

    def test_gaps_alone_do_not_block(self):
        result = self.run_import(_parsed_line(),
                                 _report(gaps=[_gap_issue("routing", "headcount")]))
        self.assertTrue(result.wrote)
        self.assertTrue(result.ok)


class TestSuccess(ImporterCase):
    def test_counts_come_from_replace_site(self):
        result = self.run_import(_parsed_line(), _report())
        self.assertEqual({"line": 1}, dict(result.counts))

    def test_rows_land_in_the_database(self):
        self.run_import(_parsed_line(), _report())
        repo = self.repo()
        self.assertEqual([dict(LINE_ROW)], repo.list_by("line"))

    def test_site_row_is_created(self):
        self.run_import(_parsed_line(), _report())
        self.assertIsNotNone(self.repo().get_by_pk("site", (SITE,)))

    def test_empty_parsed_still_writes_site_row(self):
        result = self.run_import({}, _report())
        self.assertTrue(result.wrote)
        self.assertEqual({}, dict(result.counts))
        self.assertIsNotNone(self.repo().get_by_pk("site", (SITE,)))

    def test_reimport_replaces_previous_rows(self):
        self.run_import(_parsed_line("OLD"), _report())
        self.run_import(_parsed_line("NEW"), _report())
        self.assertEqual(["NEW"], [r["line_code"] for r in self.repo().list_by("line")])

    def test_paths_are_normalized_to_str(self):
        result = self.run_import(_parsed_line(), _report())
        self.assertIsInstance(result, ImportResult)
        self.assertIsInstance(result.db_path, str)
        self.assertIsInstance(result.workbook_path, str)
        self.assertEqual(WORKBOOK, result.workbook_path)

    def test_connection_is_released(self):
        self.run_import(_parsed_line("A"), _report())
        self.run_import(_parsed_line("B"), _report())
        self.assertEqual(["B"], [r["line_code"] for r in self.repo().list_by("line")])


class TestWiring(ImporterCase):
    def test_read_workbook_receives_path_and_site(self):
        self.run_import(_parsed_line(), _report())
        self.reader.assert_called_once_with(WORKBOOK, SITE)

    def test_validate_receives_tables_and_structure_log(self):
        log = IssueLog()
        tables = {"line": []}
        self.run_import(_parsed_line(), _report(), tables=tables, log=log)
        self.validator.assert_called_once_with(tables, log)

    def test_validate_is_not_called_after_a_read_failure(self):
        with patch("aps.data.importer.read_workbook", side_effect=OSError("no file")), \
                patch("aps.data.importer.validate") as validator:
            with self.assertRaises(OSError):
                import_workbook(WORKBOOK, self.db, SITE)
        validator.assert_not_called()


class TestDegradedFields(ImporterCase):
    def test_degraded_fields_come_from_gaps(self):
        result = self.run_import(
            _parsed_line(),
            _report(gaps=[_gap_issue("routing", "headcount"),
                          _gap_issue("routing", "per_shift_cap_declared")]),
        )
        self.assertEqual(
            frozenset({"routing.headcount", "routing.per_shift_cap_declared"}),
            result.degraded_fields,
        )

    def test_degraded_fields_ignore_gaps_without_entity_or_field(self):
        log = IssueLog()
        log.gap(entity=None, field=None, sheet=None, row=None, column=None, message="表级 gap")
        result = self.run_import(_parsed_line(), _report(gaps=log.issues))
        self.assertEqual(frozenset(), result.degraded_fields)

    def test_errors_and_warnings_are_not_degraded_fields(self):
        result = self.run_import(
            _parsed_line(),
            _report(warnings=[_warning_issue("line", "name")]),
        )
        self.assertEqual(frozenset(), result.degraded_fields)


class TestSummary(ImporterCase):
    def test_summary_reports_written_counts(self):
        result = self.run_import(_parsed_line(), _report())
        text = result.summary()
        self.assertIn(SITE, text)
        self.assertIn("line=1", text)
        self.assertIn("error=0", text)

    def test_summary_reports_abort(self):
        result = self.run_import(_parsed_line(), _report([_error_issue("line", "line_code")]))
        text = result.summary()
        self.assertIn("导入中止", text)
        self.assertIn("数据库未改动", text)
        self.assertIn("缺少必需列", text)

    def test_summary_handles_zero_rows(self):
        result = self.run_import({}, _report())
        self.assertIn("无数据行", result.summary())

    def test_summary_honours_limit(self):
        gaps = [_gap_issue("routing", f"f{i}") for i in range(5)]
        result = self.run_import(_parsed_line(), _report(gaps=gaps))
        self.assertNotIn("未显示", result.summary(limit=40))
        self.assertIn("另有 3 条未显示", result.summary(limit=2))


if __name__ == "__main__":
    unittest.main()
