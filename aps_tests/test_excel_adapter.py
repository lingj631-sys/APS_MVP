from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest

import openpyxl

from aps.data.excel import HEADER_SCAN_ROWS, RawRow, locate_header, read_workbook
from aps.data.issues import Severity
from aps.schema.registry import get_entity

FAST = True


def _write(rows_by_sheet: dict[str, list[list]], path: str) -> str:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet, rows in rows_by_sheet.items():
        ws = wb.create_sheet(sheet)
        for row in rows:
            ws.append(row)
    wb.save(path)
    return path


class _TempWorkbook(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        os.unlink(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def make(self, rows_by_sheet):
        return _write(rows_by_sheet, self.path)

    def values(self, tables, entity, field):
        return [r.values.get(field) for r in tables[entity]]


LINE_HEADER = ["产线", "效率 TRP", "单班工时 (小时)", "每天班次数"]
LINE_ROWS = [
    ["PGLINKAGE", 0.85, 8.0, 3],
    ["轴套6", 0.90, 8.0, 3],
]


class TestLocateHeader(_TempWorkbook):
    def test_header_on_row_one(self):
        self.make({"产线基础数据": [LINE_HEADER] + LINE_ROWS})
        wb = openpyxl.load_workbook(self.path)
        row, col_of, hit = locate_header(wb["产线基础数据"], get_entity("line"))
        self.assertEqual(1, row)
        self.assertEqual(1, col_of["line_code"])
        self.assertEqual(4, col_of["shifts_per_day"])
        self.assertIn("效率 TRP", hit)

    def test_header_below_title_rows(self):
        self.make({"产线基础数据": [["温岭工厂产线台账"], [], LINE_HEADER] + LINE_ROWS})
        wb = openpyxl.load_workbook(self.path)
        row, col_of, _ = locate_header(wb["产线基础数据"], get_entity("line"))
        self.assertEqual(3, row)
        self.assertEqual(2, col_of["trp_efficiency"])

    def test_column_order_is_irrelevant(self):
        shuffled = ["每天班次数", "产线", "单班工时 (小时)", "效率 TRP"]
        self.make({"产线基础数据": [shuffled, [3, "PGLINKAGE", 8.0, 0.85]]})
        wb = openpyxl.load_workbook(self.path)
        _, col_of, _ = locate_header(wb["产线基础数据"], get_entity("line"))
        self.assertEqual(4, col_of["trp_efficiency"])
        self.assertEqual(1, col_of["shifts_per_day"])

    def test_fullwidth_paren_alias_recognised(self):
        header = ["站点", "料号 (PN)", "阶段", "工序序号", "工序名称", "提前准备时间 (分钟)",
                  "转运批量 (件)", "容器"]
        self.make({"工序提前期 Operation-LeadTime": [header, ["WL", "WCA1927", "SFIN", 10, "静置",
                                                             45, 100, "A"]]})
        wb = openpyxl.load_workbook(self.path)
        _, col_of, _ = locate_header(
            wb["工序提前期 Operation-LeadTime"], get_entity("operation_leadtime")
        )
        self.assertIn("prep_minutes", col_of)

    def test_unlocatable_header_returns_none(self):
        self.make({"产线基础数据": [["甲", "乙", "丙"], [1, 2, 3]]})
        wb = openpyxl.load_workbook(self.path)
        row, col_of, hit = locate_header(wb["产线基础数据"], get_entity("line"))
        self.assertIsNone(row)
        self.assertEqual({}, col_of)
        self.assertEqual(set(), hit)

    def test_scan_window_is_six_rows(self):
        self.assertEqual(6, HEADER_SCAN_ROWS)


class TestRowValidity(_TempWorkbook):
    def test_blank_shell_rows_are_skipped(self):
        rows = [LINE_HEADER] + LINE_ROWS + [[None, None, None, None]] * 5
        self.make({"产线基础数据": rows})
        tables, log = read_workbook(self.path, "WL")
        self.assertEqual(2, len(tables["line"]))
        self.assertFalse([i for i in log.errors if "工作表缺失" not in i.message])

    def test_partial_pk_row_is_skipped(self):
        rows = [LINE_HEADER, ["PGLINKAGE", 0.85, 8.0, None]]
        self.make({"产线基础数据": rows})
        tables, _ = read_workbook(self.path, "WL")
        self.assertEqual(0, len(tables["line"]))

    def test_fg_params_only_64_of_960_style(self):
        # 复刻实测异常：895 行只有「最小生产量 MOQ」列有个 0
        fg_header = ["站点", "成品料号 (PN)", "品名描述", "最小包装量 MPQ", "单班工时 (小时)",
                     "每天班次数", "最小生产量 MOQ", "产品族名", "模具编号 (Mold ID)",
                     "特殊班次", "同时生产"]
        rows = [fg_header]
        rows.append(["WL", "WA14208S", "雨刮总成", 48, 8, 3, 0, "FAM1", "M1", None, None])
        for _ in range(895):
            rows.append([None, None, None, None, None, None, 0, None, None, None, None])
        self.make({"产线与成品参数 Parameters-FG": rows})
        tables, log = read_workbook(self.path, "WL")
        self.assertEqual(1, len(tables["part_param_fg"]))
        self.assertFalse([i for i in log.errors if "工作表缺失" not in i.message])

    def test_shift_geometry_columns_of_fg_are_not_read(self):
        fg_header = ["站点", "成品料号 (PN)", "品名描述", "最小包装量 MPQ", "单班工时 (小时)",
                     "每天班次数", "最小生产量 MOQ", "产品族名", "模具编号 (Mold ID)",
                     "特殊班次", "同时生产"]
        self.make({"产线与成品参数 Parameters-FG":
                   [fg_header, ["WL", "P1", "d", 48, 8, 3, 0, None, None, None, None]]})
        tables, _ = read_workbook(self.path, "WL")
        values = tables["part_param_fg"][0].values
        self.assertNotIn("shift_hours", values)
        self.assertNotIn("shifts_per_day", values)
        self.assertEqual(48, values["mpq"])


class TestSiteInjection(_TempWorkbook):
    def test_line_gets_site_from_context(self):
        self.make({"产线基础数据": [LINE_HEADER] + LINE_ROWS})
        tables, _ = read_workbook(self.path, "WL")
        self.assertEqual(["WL", "WL"], self.values(tables, "line", "site_code"))

    def test_other_entities_get_site_from_column(self):
        header = ["站点", "模具编号 (Mold ID)", "工装模具名称", "适用产线/设备",
                  "适用零件料号", "可用套数", "换模工时 (分钟)"]
        self.make({"工装模具约束 Tooling-Mold": [header, ["WL", "M1", "模具一", "PGLINKAGE",
                                                          "P1,P2", 2, 30]]})
        tables, _ = read_workbook(self.path, "SY38")
        self.assertEqual(["WL"], self.values(tables, "tooling_mold", "site_code"))


class TestStructureIssues(_TempWorkbook):
    def _minimal(self, extra=None):
        sheets = {
            "产线基础数据": [LINE_HEADER] + LINE_ROWS,
        }
        if extra:
            sheets.update(extra)
        self.make(sheets)

    def test_missing_required_sheet_is_error(self):
        self._minimal()
        _, log = read_workbook(self.path, "WL")
        missing = [i for i in log.errors if "工作表缺失" in i.message]
        names = {i.sheet for i in missing}
        self.assertIn("期初可用库存 Initial Stock", names)

    def test_missing_optional_v3_sheet_is_warning_not_error(self):
        self._minimal()
        _, log = read_workbook(self.path, "WL")
        for sheet in ("班次人力可用性", "产品配对关系", "工作日历", "换模技术员"):
            with self.subTest(sheet=sheet):
                self.assertNotIn(sheet, {i.sheet for i in log.errors})
                self.assertIn(sheet, {i.sheet for i in log.warnings})

    def test_missing_required_column_is_error(self):
        broken = ["站点", "物料类型", "品名描述", "期初库存 (件)"]   # 缺「物料料号 (PN)」
        self.make({"期初可用库存 Initial Stock": [broken, ["WL", "COMP", "d", 10]]})
        _, log = read_workbook(self.path, "WL")
        cols = {i.column for i in log.errors}
        self.assertIn("物料料号 (PN)", cols)

    def test_extra_column_is_warning(self):
        header = LINE_HEADER + ["随便一列"]
        self.make({"产线基础数据": [header, ["PGLINKAGE", 0.85, 8.0, 3, "x"]]})
        _, log = read_workbook(self.path, "WL")
        self.assertIn("随便一列", {i.column for i in log.warnings})

    def test_deprecated_column_residue_is_warning(self):
        header = ["站点", "成品料号 (PN)", "品名描述", "最小包装量 MPQ", "最小生产量 MOQ",
                  "产线", "效率 TRP"]
        self.make({"产线与成品参数 Parameters-FG":
                   [header, ["WL", "P1", "d", 48, 0, "PGLINKAGE", 0.85]]})
        _, log = read_workbook(self.path, "WL")
        warnings = {i.column for i in log.warnings}
        self.assertIn("产线", warnings)
        self.assertIn("效率 TRP", warnings)
        self.assertFalse(any(i.severity is Severity.ERROR and i.column == "产线"
                             for i in log.issues))

    def test_unlocatable_sheet_header_is_error(self):
        self.make({"产线基础数据": [["甲", "乙", "丙"], [1, 2, 3]]})
        _, log = read_workbook(self.path, "WL")
        self.assertIn("表头", "".join(i.message for i in log.errors))


class TestDemandUnpivot(_TempWorkbook):
    HEADER = ["站点", "料号 (PN)", "品名描述"]

    def _demand_sheet(self):
        d1 = dt.datetime(2026, 11, 3)
        d2 = dt.datetime(2026, 11, 4)
        d3 = dt.datetime(2026, 11, 5)
        rows = [self.HEADER + [d1, d2, d3],
                ["WL", "P1", "d", 100, 0, None],
                ["WL", "P2", "d", 48, 96, 0],
                [None, None, None, None, None, None]]
        return self.HEADER, rows, (d1, d2, d3)

    def test_one_row_per_positive_cell(self):
        _, rows, _ = self._demand_sheet()
        self.make({"客户需求订单清单 Customer Demand": rows})
        tables, log = read_workbook(self.path, "WL")
        self.assertEqual(3, len(tables["demand"]))     # P1:d1, P2:d1, P2:d2
        self.assertFalse([i for i in log.errors if "工作表缺失" not in i.message])

    def test_zero_and_blank_cells_produce_no_row(self):
        _, rows, _ = self._demand_sheet()
        self.make({"客户需求订单清单 Customer Demand": rows})
        tables, _ = read_workbook(self.path, "WL")
        dates = [(r.values["pn"], r.values["demand_date"]) for r in tables["demand"]]
        self.assertNotIn(("P1", dt.datetime(2026, 11, 4)), dates)
        self.assertNotIn(("P1", dt.datetime(2026, 11, 5)), dates)
        self.assertNotIn(("P2", dt.datetime(2026, 11, 5)), dates)

    def test_quantity_is_raw_cell_value(self):
        _, rows, _ = self._demand_sheet()
        self.make({"客户需求订单清单 Customer Demand": rows})
        tables, _ = read_workbook(self.path, "WL")
        first = tables["demand"][0]
        self.assertEqual(100, first.values["quantity"])
        self.assertEqual(dt.datetime(2026, 11, 3), first.values["demand_date"])

    def test_excel_row_is_preserved(self):
        _, rows, _ = self._demand_sheet()
        self.make({"客户需求订单清单 Customer Demand": rows})
        tables, _ = read_workbook(self.path, "WL")
        self.assertEqual({2, 3}, {r.excel_row for r in tables["demand"]})

    def test_out_of_horizon_dates_are_not_an_adapter_problem(self):
        far = dt.datetime(2026, 12, 6)
        rows = [self.HEADER + [far], ["WL", "P1", "d", 10]]
        self.make({"客户需求订单清单 Customer Demand": rows})
        tables, log = read_workbook(self.path, "WL")
        self.assertEqual(1, len(tables["demand"]))
        self.assertFalse([i for i in log.errors if "工作表缺失" not in i.message])

    def test_text_dates_in_header_also_recognised(self):
        rows = [self.HEADER + ["2026-11-03"], ["WL", "P1", "d", 10]]
        self.make({"客户需求订单清单 Customer Demand": rows})
        tables, _ = read_workbook(self.path, "WL")
        self.assertEqual(1, len(tables["demand"]))


class TestToolingSplit(_TempWorkbook):
    HEADER = ["站点", "模具编号 (Mold ID)", "工装模具名称", "适用产线/设备",
              "适用零件料号", "可用套数", "换模工时 (分钟)"]

    def test_one_row_per_line(self):
        rows = [self.HEADER, ["WL", "M1", "模具一", "PGLINKAGE,轴套6", "P1，P2", 2, 30]]
        self.make({"工装模具约束 Tooling-Mold": rows})
        tables, _ = read_workbook(self.path, "WL")
        self.assertEqual(2, len(tables["tooling_mold"]))
        self.assertEqual({"PGLINKAGE", "轴套6"},
                         {r.values["line_code"] for r in tables["tooling_mold"]})
        for row in tables["tooling_mold"]:
            self.assertEqual(2, row.excel_row)

    def test_applicable_pns_stays_multivalue_raw(self):
        rows = [self.HEADER, ["WL", "M1", "模具一", "PGLINKAGE", "P1，P2", 2, 30]]
        self.make({"工装模具约束 Tooling-Mold": rows})
        tables, _ = read_workbook(self.path, "WL")
        self.assertEqual("P1，P2", tables["tooling_mold"][0].values["applicable_pns"])

    def test_mold_dimension_is_deduped(self):
        rows = [self.HEADER,
                ["WL", "M1", "模具一", "PGLINKAGE", "P1", 2, 30],
                ["WL", "M1", "模具一", "轴套6", "P1", 2, 30]]
        self.make({"工装模具约束 Tooling-Mold": rows})
        tables, log = read_workbook(self.path, "WL")
        self.assertEqual(2, len(tables["tooling_mold"]))
        self.assertEqual(1, len(tables["mold"]))
        self.assertFalse([i for i in log.errors if "工作表缺失" not in i.message])

    def test_empty_line_cell_warns_and_skips(self):
        rows = [self.HEADER, ["WL", "M1", "模具一", None, "P1", 2, 30]]
        self.make({"工装模具约束 Tooling-Mold": rows})
        tables, log = read_workbook(self.path, "WL")
        self.assertEqual(0, len(tables["tooling_mold"]))
        self.assertIn("适用产线/设备", {i.column for i in log.warnings})


class TestMaterialArrivalSeq(_TempWorkbook):
    HEADER = ["站点", "合格供应商", "物料料号 (PN)", "物料描述", "预计入库日期",
              "预计入库班次", "预计入库时间", "送达数量 (件)"]

    def test_seq_numbers_within_group(self):
        rows = [self.HEADER,
                ["WL", "S", "NCICLABEL01", "标签", dt.date(2026, 11, 3), "早班", None, 100],
                ["WL", "S", "NCICLABEL01", "标签", dt.date(2026, 11, 3), "早班", None, 200],
                ["WL", "S", "NCICLABEL01", "标签", dt.date(2026, 11, 4), "早班", None, 300]]
        self.make({"关键物料到货 Material-Arrival-ETA": rows})
        tables, _ = read_workbook(self.path, "WL")
        seqs = [r.values["seq"] for r in tables["material_arrival"]]
        self.assertEqual([1, 2, 1], seqs)

    def test_shift_name_stays_raw(self):
        rows = [self.HEADER,
                ["WL", "S", "P1", "d", dt.date(2026, 11, 3), "早班", None, 100]]
        self.make({"关键物料到货 Material-Arrival-ETA": rows})
        tables, _ = read_workbook(self.path, "WL")
        self.assertEqual("早班", tables["material_arrival"][0].values["eta_shift"])


class TestSynthesizedEntities(_TempWorkbook):
    def _full(self):
        fg_header = ["站点", "成品料号 (PN)", "品名描述", "最小包装量 MPQ", "最小生产量 MOQ",
                     "产品族名", "模具编号 (Mold ID)"]
        sf_header = ["站点", "半成品料号 (PN)", "品名描述", "最小起投批量 MPQ", "报废率 (小数)",
                     "最小起订量 MOQ"]
        bom_header = ["站点", "母件：成品料号 (FG PN)", "母件品名描述",
                      "子件：半成品料号 (SF PN)", "子件品名描述", "装配单耗比例"]
        stock_header = ["站点", "物料类型", "物料料号 (PN)", "品名描述", "期初库存 (件)"]
        self.make({
            "产线基础数据": [LINE_HEADER] + LINE_ROWS,
            "产线与成品参数 Parameters-FG": [fg_header, ["WL", "P1", "总成", 48, 0, "FAM1", "M1"]],
            "半成品制造参数 Parameters-SFN": [sf_header, ["WL", "S1", "半成品", 96, 0.02, 0]],
            "产品装配清单 BOM List": [bom_header, ["WL", "P1", "总成", "S1", "半成品", 2.0]],
            "期初可用库存 Initial Stock": [stock_header,
                                           ["WL", "COMP", "C1", "采购件", 252],
                                           ["WL", "FING", "P1", "总成", 10]],
        })

    def test_site_entity(self):
        self._full()
        tables, _ = read_workbook(self.path, "WL")
        self.assertEqual(1, len(tables["site"]))
        self.assertEqual("WL", tables["site"][0].values["site_code"])

    def test_calendar_pattern_fixed_four_rows(self):
        self._full()
        tables, _ = read_workbook(self.path, "WL")
        rows = {r.values["pattern_code"]: r.values for r in tables["calendar_pattern"]}
        self.assertEqual({"CONTINUOUS", "6ON1OFF", "5ON2OFF", "4ON3OFF"}, set(rows))
        self.assertEqual(7, rows["CONTINUOUS"]["work_days"])
        self.assertEqual(0, rows["CONTINUOUS"]["rest_days"])
        self.assertEqual(3, rows["4ON3OFF"]["rest_days"])

    def test_part_union_covers_all_sheets(self):
        self._full()
        tables, _ = read_workbook(self.path, "WL")
        stages = {r.values["pn"]: r.values["stage"] for r in tables["part"]}
        self.assertEqual("FG", stages["P1"])
        self.assertEqual("SF", stages["S1"])
        self.assertEqual("COMP", stages["C1"])

    def test_part_stage_raw_value_is_preserved_for_stock_only_pn(self):
        # FING 归一化在类型门做，适配器保留原值
        self._full()
        tables, _ = read_workbook(self.path, "WL")
        stock_only = [r for r in tables["part"] if r.values["pn"] == "C1"]
        self.assertEqual("COMP", stock_only[0].values["stage"])

    def test_part_precedence_fg_over_stock(self):
        self._full()
        tables, _ = read_workbook(self.path, "WL")
        p1 = [r for r in tables["part"] if r.values["pn"] == "P1"][0]
        self.assertEqual("FG", p1.values["stage"])
        self.assertEqual("总成", p1.values["description"])
        self.assertEqual("FAM1", p1.values["family_code"])

    def test_part_uom_constant(self):
        self._full()
        tables, _ = read_workbook(self.path, "WL")
        self.assertTrue(all(r.values["uom"] == "件" for r in tables["part"]))

    def test_part_rows_are_synthesized(self):
        self._full()
        tables, _ = read_workbook(self.path, "WL")
        for row in tables["part"]:
            with self.subTest(pn=row.values["pn"]):
                self.assertIsNone(row.excel_row)
                self.assertIsNone(row.sheet)

    def test_product_family_from_part(self):
        self._full()
        tables, _ = read_workbook(self.path, "WL")
        codes = {r.values["family_code"] for r in tables["product_family"]}
        self.assertEqual({"FAM1"}, codes)

    def test_stage_ambiguity_warns(self):
        fg_header = ["站点", "成品料号 (PN)", "品名描述", "最小包装量 MPQ"]
        sf_header = ["站点", "半成品料号 (PN)", "品名描述", "最小起投批量 MPQ", "报废率 (小数)"]
        self.make({
            "产线基础数据": [LINE_HEADER] + LINE_ROWS,
            "产线与成品参数 Parameters-FG": [fg_header, ["WL", "X1", "d", 48]],
            "半成品制造参数 Parameters-SFN": [sf_header, ["WL", "X1", "d", 96, 0.0]],
        })
        tables, log = read_workbook(self.path, "WL")
        stages = [r.values["stage"] for r in tables["part"] if r.values["pn"] == "X1"]
        self.assertEqual(["FG"], stages)
        self.assertTrue(any("阶段歧义" in i.message for i in log.warnings))


class TestDuplicateDetectionIsNotAdapterJob(_TempWorkbook):
    def test_identical_leadtime_duplicates_pass_through(self):
        header = ["站点", "料号 (PN)", "阶段", "工序序号", "工序名称",
                  "提前准备时间（分钟）", "转运批量 (件)", "容器"]
        row = ["WL", "WCA1927", "SFIN", 10, "静置", 45, 100, "A"]
        self.make({"工序提前期 Operation-LeadTime": [header] + [row] * 38})
        tables, log = read_workbook(self.path, "WL")
        self.assertEqual(38, len(tables["operation_leadtime"]))
        self.assertFalse([i for i in log.errors if "工作表缺失" not in i.message])


if __name__ == "__main__":
    unittest.main()
