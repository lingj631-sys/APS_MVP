from __future__ import annotations

import datetime as dt
import unittest

from aps.data.excel import RawRow
from aps.data.issues import IssueLog, Severity
from aps.data.validate import (
    CandidateRow,
    ValidationReport,
    detect_gaps,
    gate_business,
    gate_references,
    gate_types,
    resolve_duplicates,
    validate,
)

FAST = True


def raw(entity, values, excel_row=2, sheet="S"):
    return RawRow(entity=entity, sheet=sheet, excel_row=excel_row, values=dict(values))


def line_raw(code="PGLINKAGE", trp=0.85, hours=8.0, shifts=3, **kw):
    values = {"site_code": "WL", "line_code": code, "name": code,
              "trp_efficiency": trp, "shift_hours": hours, "shifts_per_day": shifts}
    values.update(kw)
    return raw("line", values)


def site_tables(*extra):
    tables = {"site": [raw("site", {"site_code": "WL"}, sheet=None, excel_row=None)]}
    for row in extra:
        tables.setdefault(row.entity, []).append(row)
    return tables


def parse(tables):
    log = IssueLog()
    parsed = gate_types(tables, log)
    return parsed, log


class TestTypeGate(unittest.TestCase):
    def test_scalars_parsed(self):
        parsed, log = parse(site_tables(line_raw()))
        row = parsed["line"][0]
        self.assertEqual(0.85, row.values["trp_efficiency"])
        self.assertEqual(8.0, row.values["shift_hours"])
        self.assertEqual(3, row.values["shifts_per_day"])
        self.assertFalse(log.has_errors())

    def test_stage_normalization(self):
        tables = site_tables(raw("routing", {
            "site_code": "WL", "pn": "P1", "op_seq": "10", "line_code": "L1",
            "stage": "SFIN", "certified": "True", "ct_sec": "27",
        }))
        parsed, log = parse(tables)
        self.assertEqual("SF", parsed["routing"][0].values["stage"])
        self.assertEqual(10, parsed["routing"][0].values["op_seq"])
        self.assertIs(True, parsed["routing"][0].values["certified"])
        self.assertEqual(27.0, parsed["routing"][0].values["ct_sec"])
        self.assertFalse(log.has_errors())

    def test_initial_stock_material_type_normalized(self):
        tables = site_tables(raw("initial_stock", {
            "site_code": "WL", "pn": "P1", "material_type": "FING", "quantity": "10",
        }))
        parsed, _ = parse(tables)
        self.assertEqual("FG", parsed["initial_stock"][0].values["material_type"])

    def test_shift_name_to_index(self):
        tables = site_tables(raw("material_arrival", {
            "site_code": "WL", "pn": "P1", "eta_date": dt.date(2026, 11, 3),
            "eta_shift": "早班", "seq": 1, "quantity": 100,
        }))
        parsed, log = parse(tables)
        self.assertEqual(0, parsed["material_arrival"][0].values["eta_shift"])
        self.assertFalse(log.has_errors())

    def test_enum_code_extraction(self):
        tables = site_tables(raw("customer", {
            "site_code": "WL", "customer_code": "C1", "name": "客户",
            "tier": "TIER_A (核心A级)",
        }))
        parsed, log = parse(tables)
        self.assertEqual("TIER_A", parsed["customer"][0].values["tier"])
        self.assertFalse(log.has_errors())

    def test_pm_disposition_enum_code(self):
        tables = site_tables(raw("preventive_maintenance", {
            "site_code": "WL", "line_code": "L1", "start_date": dt.date(2026, 11, 3),
            "item": "保养", "downtime_hours": 4.0,
            "disposition": "避让顺延 (Shift_Delay)",
        }))
        parsed, _ = parse(tables)
        self.assertEqual("Shift_Delay", parsed["preventive_maintenance"][0].values["disposition"])

    def test_multivalue_parsed_to_tuple(self):
        tables = site_tables(raw("tooling_mold", {
            "site_code": "WL", "mold_id": "M1", "line_code": "L1",
            "applicable_pns": "P1，P2", "available_sets": "2",
        }))
        parsed, _ = parse(tables)
        self.assertEqual(("P1", "P2"), parsed["tooling_mold"][0].values["applicable_pns"])
        self.assertEqual(2, parsed["tooling_mold"][0].values["available_sets"])

    def test_unparseable_value_is_error_with_location(self):
        tables = site_tables(line_raw(trp="很高"))
        log = IssueLog()
        gate_types(tables, log)
        errors = log.errors
        self.assertEqual(1, len(errors))
        issue = errors[0]
        self.assertEqual(Severity.ERROR, issue.severity)
        self.assertEqual("line", issue.entity)
        self.assertEqual("trp_efficiency", issue.field)
        self.assertEqual(2, issue.row)
        self.assertEqual("效率 TRP", issue.column)
        self.assertEqual("很高", issue.value)

    def test_missing_non_nullable_is_error(self):
        tables = site_tables(raw("line", {
            "site_code": "WL", "line_code": "L1", "name": "L1",
            "trp_efficiency": None, "shift_hours": 8.0, "shifts_per_day": 3,
        }))
        log = IssueLog()
        gate_types(tables, log)
        self.assertTrue(any(i.field == "trp_efficiency" for i in log.errors))

    def test_pk_must_not_be_null(self):
        tables = site_tables(raw("line", {"site_code": "WL", "line_code": None,
                                          "trp_efficiency": 0.85, "shift_hours": 8.0,
                                          "shifts_per_day": 3}))
        log = IssueLog()
        gate_types(tables, log)
        self.assertTrue(any(i.field == "line_code" for i in log.errors))

    def test_enum_domain_violation_is_error(self):
        tables = site_tables(raw("inventory_policy", {
            "site_code": "WL", "pn": "P1", "period_type": "WEEKLY", "start_day": 1,
            "end_day": 5,
        }))
        log = IssueLog()
        gate_types(tables, log)
        self.assertTrue(any(i.field == "period_type" for i in log.errors))

    def test_bad_date_is_error(self):
        tables = site_tables(raw("routing", {
            "site_code": "WL", "pn": "P1", "op_seq": 10, "line_code": "L1",
            "certified": True, "ct_sec": 27.0, "valid_from": "昨天",
        }))
        log = IssueLog()
        gate_types(tables, log)
        self.assertTrue(any(i.field == "valid_from" for i in log.errors))

    def test_t2_all_null_column_is_one_gap(self):
        rows = [raw("routing", {"site_code": "WL", "pn": f"P{i}", "op_seq": 10,
                                "line_code": "L1", "certified": True, "ct_sec": 27.0,
                                "per_shift_cap_declared": None}, excel_row=i + 2)
                for i in range(3)]
        log = IssueLog()
        gate_types(site_tables(*rows), log)
        gaps = [i for i in log.gaps if i.field == "per_shift_cap_declared"]
        self.assertEqual(1, len(gaps))
        self.assertEqual("routing", gaps[0].entity)

    def test_t1_all_null_column_is_not_a_gap(self):
        log = IssueLog()
        gate_types(site_tables(line_raw()), log)
        self.assertEqual((), log.gaps)

    def test_empty_entity_produces_no_gap(self):
        log = IssueLog()
        gate_types({"site": [raw("site", {"site_code": "WL"}, sheet=None, excel_row=None)],
                    "routing": []}, log)
        self.assertEqual((), tuple(i for i in log.gaps if i.entity == "routing"))


class TestReferentialGate(unittest.TestCase):
    def _parsed(self, rows):
        log = IssueLog()
        return gate_types(site_tables(*rows), log), log

    def test_valid_fk_passes(self):
        parsed, log = self._parsed([
            line_raw(),
            raw("routing", {"site_code": "WL", "pn": "P1", "op_seq": 10,
                            "line_code": "PGLINKAGE", "certified": True, "ct_sec": 27.0}),
            raw("part", {"site_code": "WL", "pn": "P1", "stage": "FG"},
                sheet=None, excel_row=None),
        ])
        gate_references(parsed, log)
        self.assertFalse(log.has_errors())

    def test_unknown_line_is_error(self):
        parsed, log = self._parsed([
            raw("routing", {"site_code": "WL", "pn": "P1", "op_seq": 10,
                            "line_code": "GHOST", "certified": True, "ct_sec": 27.0}),
        ])
        gate_references(parsed, log)
        errors = [i for i in log.errors if i.field == "line_code"]
        self.assertEqual(1, len(errors))
        self.assertEqual("GHOST", errors[0].value)

    def test_null_fk_is_skipped(self):
        parsed, log = self._parsed([
            raw("part", {"site_code": "WL", "pn": "P1", "stage": "FG"},
                sheet=None, excel_row=None),
            raw("part_param_fg", {"site_code": "WL", "pn": "P1", "mpq": 48,
                                  "mold_id": None}),
        ])
        gate_references(parsed, log)
        self.assertFalse(log.has_errors())

    def test_optional_fk_downgrades_to_warning(self):
        parsed, log = self._parsed([
            raw("part", {"site_code": "WL", "pn": "P1", "stage": "FG"},
                sheet=None, excel_row=None),
            raw("bom", {"site_code": "WL", "parent_pn": "P1", "child_pn": "NCICLABEL01",
                        "ratio": 2.0}),
        ])
        gate_references(parsed, log)
        self.assertFalse(log.has_errors())
        warnings = [i for i in log.warnings if i.field == "child_pn"]
        self.assertEqual(1, len(warnings))
        self.assertEqual("NCICLABEL01", warnings[0].value)

    def test_required_child_fk_still_errors_for_parent(self):
        parsed, log = self._parsed([
            raw("bom", {"site_code": "WL", "parent_pn": "GHOST", "child_pn": "S1",
                        "ratio": 2.0}),
        ])
        gate_references(parsed, log)
        self.assertTrue(any(i.field == "parent_pn" for i in log.errors))

    def test_out_of_horizon_demand_date_is_not_an_error(self):
        parsed, log = self._parsed([
            raw("part", {"site_code": "WL", "pn": "P1", "stage": "FG"},
                sheet=None, excel_row=None),
            raw("demand", {"site_code": "WL", "pn": "P1",
                           "demand_date": dt.date(2026, 12, 6), "quantity": 10}),
        ])
        gate_references(parsed, log)
        self.assertFalse(log.has_errors())

    def test_initial_stock_pn_never_checked(self):
        parsed, log = self._parsed([
            raw("initial_stock", {"site_code": "WL", "pn": "58113447",
                                  "material_type": "COMP", "quantity": 252}),
        ])
        gate_references(parsed, log)
        self.assertFalse(log.has_errors())


class TestBusinessGate(unittest.TestCase):
    def _parsed(self, rows):
        log = IssueLog()
        return gate_types(site_tables(*rows), log), log

    def test_trp_out_of_range(self):
        for bad in (0, -0.1, 1.5):
            with self.subTest(trp=bad):
                parsed, log = self._parsed([line_raw(trp=bad)])
                gate_business(parsed, log)
                self.assertTrue(any(i.field == "trp_efficiency" for i in log.errors))

    def test_trp_one_is_legal(self):
        parsed, log = self._parsed([line_raw(trp=1.0)])
        gate_business(parsed, log)
        self.assertFalse(log.has_errors())

    def test_mpq_zero_is_error(self):
        parsed, log = self._parsed([
            raw("part_param_fg", {"site_code": "WL", "pn": "P1", "mpq": 0}),
        ])
        gate_business(parsed, log)
        self.assertTrue(any(i.field == "mpq" for i in log.errors))

    def test_scrap_rate_domain(self):
        for bad in (-0.01, 1.0, 1.5):
            with self.subTest(rate=bad):
                parsed, log = self._parsed([
                    raw("part_param_sf", {"site_code": "WL", "pn": "S1", "mpq": 96,
                                          "scrap_rate": bad}),
                ])
                gate_business(parsed, log)
                self.assertTrue(any(i.field == "scrap_rate" for i in log.errors))

    def test_scrap_rate_zero_is_legal(self):
        parsed, log = self._parsed([
            raw("part_param_sf", {"site_code": "WL", "pn": "S1", "mpq": 96,
                                  "scrap_rate": 0.0}),
        ])
        gate_business(parsed, log)
        self.assertFalse(log.has_errors())

    def test_ct_zero_is_error(self):
        parsed, log = self._parsed([
            raw("routing", {"site_code": "WL", "pn": "P1", "op_seq": 10, "line_code": "L1",
                            "certified": True, "ct_sec": 0}),
        ])
        gate_business(parsed, log)
        self.assertTrue(any(i.field == "ct_sec" for i in log.errors))

    def test_valid_from_after_valid_to_is_error(self):
        parsed, log = self._parsed([
            raw("routing", {"site_code": "WL", "pn": "P1", "op_seq": 10, "line_code": "L1",
                            "certified": True, "ct_sec": 27.0,
                            "valid_from": dt.date(2026, 12, 1),
                            "valid_to": dt.date(2026, 11, 1)}),
        ])
        gate_business(parsed, log)
        self.assertTrue(any(i.field == "valid_to" for i in log.errors))

    def test_inventory_policy_day_range(self):
        parsed, log = self._parsed([
            raw("inventory_policy", {"site_code": "WL", "pn": "P1", "period_type": "NORMAL",
                                     "start_day": 20, "end_day": 5}),
        ])
        gate_business(parsed, log)
        self.assertTrue(any(i.field == "end_day" for i in log.errors))

    def test_negative_safety_stock_is_error(self):
        parsed, log = self._parsed([
            raw("inventory_policy", {"site_code": "WL", "pn": "P1", "period_type": "NORMAL",
                                     "start_day": 1, "end_day": 31, "safety_stock": -5}),
        ])
        gate_business(parsed, log)
        self.assertTrue(any(i.field == "safety_stock" for i in log.errors))

    def test_capacity_pct_over_100_is_error(self):
        parsed, log = self._parsed([
            raw("preventive_maintenance", {
                "site_code": "WL", "line_code": "L1", "start_date": dt.date(2026, 11, 3),
                "item": "保养", "downtime_hours": 4.0, "capacity_pct": 120}),
        ])
        gate_business(parsed, log)
        self.assertTrue(any(i.field == "capacity_pct" for i in log.errors))

    def test_family_conflict_with_part_is_warning(self):
        parsed, log = self._parsed([
            raw("part", {"site_code": "WL", "pn": "P1", "stage": "FG",
                         "family_code": "FAM1"}, sheet=None, excel_row=None),
            raw("routing", {"site_code": "WL", "pn": "P1", "op_seq": 10, "line_code": "L1",
                            "certified": True, "ct_sec": 27.0, "family_code": "FAM2"}),
        ])
        gate_business(parsed, log)
        self.assertFalse(log.has_errors())
        self.assertTrue(any("产品族" in i.message for i in log.warnings))

    def test_no_family_conflict_when_routing_blank(self):
        parsed, log = self._parsed([
            raw("part", {"site_code": "WL", "pn": "P1", "stage": "FG",
                         "family_code": "FAM1"}, sheet=None, excel_row=None),
            raw("routing", {"site_code": "WL", "pn": "P1", "op_seq": 10, "line_code": "L1",
                            "certified": True, "ct_sec": 27.0, "family_code": None}),
        ])
        gate_business(parsed, log)
        self.assertFalse(any("产品族" in i.message for i in log.warnings))


class TestDuplicatePolicy(unittest.TestCase):
    def _parsed(self, rows):
        log = IssueLog()
        return gate_types(site_tables(*rows), log), log

    def test_initial_stock_same_pk_sums(self):
        parsed, log = self._parsed([
            raw("initial_stock", {"site_code": "WL", "pn": "58113447",
                                  "material_type": "COMP", "quantity": 252}, excel_row=5),
            raw("initial_stock", {"site_code": "WL", "pn": "58113447",
                                  "material_type": "COMP", "quantity": 3888}, excel_row=912),
        ])
        out = resolve_duplicates(parsed, log)
        rows = out["initial_stock"]
        self.assertEqual(1, len(rows))
        self.assertEqual(4140, rows[0].values["quantity"])
        warnings = [i for i in log.warnings if i.entity == "initial_stock"]
        self.assertEqual(1, len(warnings))
        self.assertIn("5", warnings[0].message)
        self.assertIn("912", warnings[0].message)
        self.assertFalse(log.has_errors())

    def test_initial_stock_different_material_type_is_not_a_duplicate(self):
        parsed, log = self._parsed([
            raw("initial_stock", {"site_code": "WL", "pn": "P1", "material_type": "COMP",
                                  "quantity": 252}),
            raw("initial_stock", {"site_code": "WL", "pn": "P1", "material_type": "PACK",
                                  "quantity": 3888}),
        ])
        out = resolve_duplicates(parsed, log)
        self.assertEqual(2, len(out["initial_stock"]))
        self.assertFalse(log.has_errors())

    def test_leadtime_identical_duplicates_dedupe_with_warning(self):
        base = {"site_code": "WL", "pn": "WCA1927", "op_seq": 10, "stage": "SF",
                "op_name": "静置", "prep_minutes": 45.0, "transfer_batch": 100,
                "container": "A"}
        parsed, log = self._parsed([raw("operation_leadtime", base, excel_row=i + 2)
                                    for i in range(38)])
        out = resolve_duplicates(parsed, log)
        self.assertEqual(1, len(out["operation_leadtime"]))
        self.assertFalse(log.has_errors())
        self.assertTrue(any(i.entity == "operation_leadtime" for i in log.warnings))

    def test_dedupe_policy_with_differing_field_is_error(self):
        parsed, log = self._parsed([
            raw("operation_leadtime", {"site_code": "WL", "pn": "WCA1927", "op_seq": 10,
                                       "prep_minutes": 45.0}, excel_row=2),
            raw("operation_leadtime", {"site_code": "WL", "pn": "WCA1927", "op_seq": 10,
                                       "prep_minutes": 60.0}, excel_row=3),
        ])
        out = resolve_duplicates(parsed, log)
        self.assertEqual(1, len(out["operation_leadtime"]))
        self.assertTrue(any(i.entity == "operation_leadtime" for i in log.errors))

    def test_error_policy_duplicate_is_error(self):
        parsed, log = self._parsed([
            line_raw("PGLINKAGE"), line_raw("PGLINKAGE", trp=0.9),
        ])
        out = resolve_duplicates(parsed, log)
        self.assertEqual(1, len(out["line"]))
        self.assertTrue(log.has_errors())

    def test_mold_dedupe_across_lines_is_silent(self):
        parsed, log = self._parsed([
            raw("mold", {"site_code": "WL", "mold_id": "M1", "name": "模具一",
                         "available_sets": 2, "changeover_min": 30.0}, excel_row=2),
            raw("mold", {"site_code": "WL", "mold_id": "M1", "name": "模具一",
                         "available_sets": 2, "changeover_min": 30.0}, excel_row=3),
        ])
        out = resolve_duplicates(parsed, log)
        self.assertEqual(1, len(out["mold"]))
        self.assertFalse(log.has_errors())

    def test_rows_are_sorted_deterministically(self):
        parsed, log = self._parsed([
            line_raw("Z线"), line_raw("A线"), line_raw("M线"),
        ])
        out = resolve_duplicates(parsed, log)
        self.assertEqual(["A线", "M线", "Z线"],
                         [r.values["line_code"] for r in out["line"]])


class TestGapDetection(unittest.TestCase):
    def test_all_t2_routing_columns_reported(self):
        rows = [raw("routing", {"site_code": "WL", "pn": "P1", "op_seq": 10,
                                "line_code": "L1", "certified": True, "ct_sec": 27.0})]
        log = IssueLog()
        gate_types(site_tables(*rows), log)
        gap_fields = {i.field for i in log.gaps if i.entity == "routing"}
        self.assertIn("per_shift_cap_declared", gap_fields)
        self.assertIn("mold_ids", gap_fields)
        self.assertIn("headcount", gap_fields)


class TestValidateEndToEnd(unittest.TestCase):
    def test_structure_log_errors_are_merged(self):
        structure = IssueLog()
        structure.error(entity="line", sheet="产线基础数据", message="工作表缺失")
        parsed, report = validate(site_tables(line_raw()), structure)
        self.assertFalse(report.ok)
        self.assertEqual(1, report.counts()["error"])
        self.assertIn("line", parsed)

    def test_clean_tables_produce_ok_report(self):
        parsed, report = validate(
            site_tables(
                line_raw(),
                raw("part", {"site_code": "WL", "pn": "P1", "stage": "FG"},
                    sheet=None, excel_row=None),
                raw("part_param_fg", {"site_code": "WL", "pn": "P1", "mpq": 48}),
            ),
            IssueLog(),
        )
        self.assertTrue(report.ok)
        self.assertEqual(0, report.counts()["error"])

    def test_report_render_mentions_counts(self):
        _, report = validate(site_tables(line_raw(trp=9.0)), IssueLog())
        text = report.render()
        self.assertIn("error", text.lower())
        self.assertFalse(report.ok)

    def test_candidate_rows_carry_excel_row(self):
        parsed, _ = validate(site_tables(line_raw()), IssueLog())
        self.assertIsInstance(parsed["line"][0], CandidateRow)
        self.assertEqual(2, parsed["line"][0].excel_row)

    def test_report_is_immutable(self):
        _, report = validate(site_tables(line_raw()), IssueLog())
        self.assertIsInstance(report, ValidationReport)
        with self.assertRaises(Exception):
            report.errors = ()


if __name__ == "__main__":
    unittest.main()
