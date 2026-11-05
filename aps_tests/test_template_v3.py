from __future__ import annotations

import datetime as dt
import os
import tempfile
import unittest

from openpyxl import load_workbook

from aps.data.excel import read_workbook
from aps.data.template_v3 import (
    DEMAND_DAYS,
    DEMAND_START,
    INSTRUCTIONS_SHEET,
    PREAMBLE,
    SHEET_ORDER,
    TYPE_LABEL,
    demand_date_columns,
    header_for,
    header_tier,
    headers_for_sheet,
    instruction_rows,
    specs_for_sheet,
    write_v3_template,
)
from aps.schema.registry import DEPRECATED_HEADERS, REGISTRY, entities_with_sheet
from aps.schema.types import ContractTier, DataType

FAST = True

SITE = "WL"

V3_SHEETS = ("班次人力可用性", "产品配对关系", "工作日历", "换模技术员")


class TemplateCase(unittest.TestCase):
    """生成一次 V3 模板，供多个测试共用。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls._tmp.name, "APS_DataSource_Template - WL -V3.xlsx")
        write_v3_template(cls.path, SITE)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def setUp(self) -> None:
        self.wb = load_workbook(self.path)
        self.addCleanup(self.wb.close)

    def headers(self, sheet: str) -> tuple:
        ws = self.wb[sheet]
        out = []
        for col in range(1, (ws.max_column or 0) + 1):
            value = ws.cell(1, col).value
            if value is not None:
                out.append(value)
        return tuple(out)

    def texts(self, sheet: str) -> tuple[str, ...]:
        return tuple(str(v) for v in self.headers(sheet) if not isinstance(v, dt.datetime))


class TestSheetOrder(unittest.TestCase):
    def test_sheet_order_matches_registry(self):
        self.assertEqual(
            set(SHEET_ORDER), {spec.sheet for spec in entities_with_sheet()}
        )
        self.assertEqual(18, len(SHEET_ORDER))
        self.assertEqual(18, len(set(SHEET_ORDER)))

    def test_sheet_order_has_no_duplicates(self):
        self.assertEqual(len(SHEET_ORDER), len(set(SHEET_ORDER)))

    def test_specs_for_sheet_groups_shared_sheet(self):
        specs = specs_for_sheet("工装模具约束 Tooling-Mold")
        self.assertEqual(("mold", "tooling_mold"), tuple(s.name for s in specs))

    def test_specs_for_single_sheet_entities(self):
        self.assertEqual(("line",), tuple(s.name for s in specs_for_sheet("产线基础数据")))

    def test_sheets_are_written_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.xlsx")
            write_v3_template(path, SITE)
            wb = load_workbook(path)
            self.addCleanup(wb.close)
        self.assertEqual([INSTRUCTIONS_SHEET, *SHEET_ORDER], wb.sheetnames)


class TestHeaderDerivation(unittest.TestCase):
    def test_header_prefers_halfwidth_parens(self):
        field = REGISTRY["operation_leadtime"].field("prep_minutes")
        self.assertEqual("提前准备时间 (分钟)", header_for(field))

    def test_header_is_none_for_injected_and_derived(self):
        self.assertIsNone(header_for(REGISTRY["line"].field("site_code")))
        self.assertIsNone(header_for(REGISTRY["demand"].field("demand_date")))
        self.assertIsNone(header_for(REGISTRY["demand"].field("quantity")))

    def test_headers_dedupe_across_shared_sheet(self):
        headers = headers_for_sheet(specs_for_sheet("工装模具约束 Tooling-Mold"))
        self.assertEqual(len(headers), len(set(headers)))
        self.assertEqual(
            ("站点", "模具编号 (Mold ID)", "工装模具名称", "可用套数",
             "换模工时 (分钟)", "适用产线/设备", "适用零件料号"),
            headers,
        )

    def test_line_sheet_has_no_site_column(self):
        self.assertEqual(
            ("产线", "效率 TRP", "单班工时 (小时)", "每天班次数"),
            headers_for_sheet(specs_for_sheet("产线基础数据")),
        )

    def test_demand_headers(self):
        self.assertEqual(
            ("站点", "料号 (PN)", "品名描述", "客户代码 (Code)"),
            headers_for_sheet(specs_for_sheet("客户需求订单清单 Customer Demand")),
        )

    def test_header_tier_t1_wins_on_shared_sheet(self):
        specs = specs_for_sheet("工装模具约束 Tooling-Mold")
        self.assertIs(ContractTier.T1, header_tier(specs, "可用套数"))
        self.assertIs(ContractTier.T2, header_tier(specs, "换模工时 (分钟)"))
        self.assertIs(ContractTier.T2, header_tier(specs, "适用零件料号"))

    def test_every_sheet_header_is_known_or_date(self):
        for sheet in SHEET_ORDER:
            specs = specs_for_sheet(sheet)
            known = set()
            for spec in specs:
                known.update(spec.header_to_field)
            for header in headers_for_sheet(specs):
                self.assertIn(header, known, f"{sheet}: {header}")

    def test_no_deprecated_header_is_generated(self):
        for sheet, deprecated in DEPRECATED_HEADERS.items():
            generated = set(headers_for_sheet(specs_for_sheet(sheet)))
            self.assertEqual(frozenset(), generated & set(deprecated), sheet)


class TestTypeLabel(unittest.TestCase):
    def test_every_data_type_has_a_label(self):
        self.assertEqual(set(DataType), set(TYPE_LABEL))

    def test_labels_are_non_empty(self):
        for dtype, label in TYPE_LABEL.items():
            self.assertTrue(label.strip(), dtype)


class TestDemandDateAxis(unittest.TestCase):
    def test_default_axis_matches_v2(self):
        columns = demand_date_columns()
        self.assertEqual(DEMAND_DAYS, len(columns))
        self.assertEqual(dt.datetime(2026, 11, 3), columns[0])
        self.assertEqual(dt.datetime(2026, 12, 6), columns[-1])

    def test_axis_is_contiguous_daily(self):
        columns = demand_date_columns()
        for index in range(1, len(columns)):
            self.assertEqual(dt.timedelta(days=1), columns[index] - columns[index - 1])

    def test_axis_is_configurable(self):
        columns = demand_date_columns(dt.date(2027, 1, 1), 3)
        self.assertEqual(
            (dt.datetime(2027, 1, 1), dt.datetime(2027, 1, 2), dt.datetime(2027, 1, 3)),
            columns,
        )

    def test_zero_days_yields_empty_axis(self):
        self.assertEqual((), demand_date_columns(DEMAND_START, 0))


class TestWrittenWorkbook(TemplateCase):
    def test_instructions_sheet_exists(self):
        self.assertIn(INSTRUCTIONS_SHEET, self.wb.sheetnames)

    def test_v3_extension_sheets_exist(self):
        for sheet in V3_SHEETS:
            self.assertIn(sheet, self.wb.sheetnames)

    def test_shift_staffing_headers(self):
        self.assertEqual(
            ("站点", "产线", "日期", "班次", "可用人数"),
            self.texts("班次人力可用性"),
        )

    def test_product_pairing_headers(self):
        self.assertEqual(
            ("站点", "左件料号", "右件料号", "配对类型", "同步容差班次"),
            self.texts("产品配对关系"),
        )

    def test_calendar_day_headers(self):
        self.assertEqual(
            ("站点", "产线", "日期", "班次序号", "是否开班", "备注"),
            self.texts("工作日历"),
        )

    def test_setup_technician_headers(self):
        self.assertEqual(
            ("站点", "技术员", "日期", "班次", "可用产线", "可同时换模数"),
            self.texts("换模技术员"),
        )

    def test_leadtime_uses_halfwidth_parens(self):
        headers = self.texts("工序提前期 Operation-LeadTime")
        self.assertIn("提前准备时间 (分钟)", headers)
        self.assertNotIn("提前准备时间（分钟）", headers)

    def test_line_sheet_written_without_site(self):
        self.assertEqual(("产线", "效率 TRP", "单班工时 (小时)", "每天班次数"),
                         self.texts("产线基础数据"))

    def test_demand_sheet_has_date_axis(self):
        headers = self.headers("客户需求订单清单 Customer Demand")
        self.assertEqual(
            ("站点", "料号 (PN)", "品名描述", "客户代码 (Code)"), headers[:4]
        )
        dates = headers[4:]
        self.assertEqual(DEMAND_DAYS, len(dates))
        self.assertTrue(all(isinstance(v, dt.datetime) for v in dates))
        self.assertEqual(dt.datetime(2026, 11, 3), dates[0])

    def test_demand_date_axis_honours_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.xlsx")
            write_v3_template(path, SITE, dt.date(2027, 3, 1), 5)
            wb = load_workbook(path)
            self.addCleanup(wb.close)
            ws = wb["客户需求订单清单 Customer Demand"]
            dates = [ws.cell(1, col).value for col in range(5, 10)]
        self.assertEqual(dt.datetime(2027, 3, 1), dates[0])
        self.assertEqual(dt.datetime(2027, 3, 5), dates[4])
        self.assertIsNone(ws.cell(1, 10).value)

    def test_date_header_number_format_is_date_only(self):
        ws = self.wb["客户需求订单清单 Customer Demand"]
        self.assertEqual("yyyy-mm-dd", ws.cell(1, 5).number_format)

    def test_headers_are_frozen(self):
        for sheet in SHEET_ORDER:
            self.assertEqual("A2", self.wb[sheet].freeze_panes, sheet)

    def test_t2_headers_are_greyed(self):
        ws = self.wb["工艺路线与候选机台 Routing-Machines"]
        headers = self.texts("工艺路线与候选机台 Routing-Machines")
        col_of = {header: index + 1 for index, header in enumerate(headers)}
        t2 = ws.cell(1, col_of["人数"]).fill.start_color.rgb
        t1 = ws.cell(1, col_of["CT (秒)"]).fill.start_color.rgb
        self.assertNotEqual(t1, t2)

    def test_no_data_rows_are_written(self):
        for sheet in SHEET_ORDER:
            self.assertEqual(1, self.wb[sheet].max_row, sheet)


class TestInstructions(TemplateCase):
    def test_rows_have_nine_columns(self):
        for row in instruction_rows():
            self.assertEqual(9, len(row))

    def test_first_row_is_the_header(self):
        self.assertEqual(
            ("工作表", "列名", "类型", "契约层级", "必填", "单位", "允许值", "主键", "说明"),
            instruction_rows()[0],
        )

    def test_every_generated_column_is_documented(self):
        documented = {(row[0], row[1]) for row in instruction_rows()[1:]}
        for sheet in SHEET_ORDER:
            for header in headers_for_sheet(specs_for_sheet(sheet)):
                self.assertIn((sheet, str(header)), documented, f"{sheet}: {header}")

    def test_documented_columns_have_no_duplicates(self):
        keys = [(row[0], row[1]) for row in instruction_rows()[1:]]
        self.assertEqual(len(keys), len(set(keys)))

    def test_t1_rows_are_marked_required(self):
        rows = {row[:2]: row for row in instruction_rows()[1:]}
        self.assertEqual("是", rows[("工艺路线与候选机台 Routing-Machines", "CT (秒)")][4])
        self.assertEqual("否", rows[("工艺路线与候选机台 Routing-Machines", "人数")][4])

    def test_enum_values_are_listed(self):
        rows = {row[:2]: row for row in instruction_rows()[1:]}
        self.assertEqual("NORMAL / MONTH_END", rows[("库存策略 Inventory-Policy", "周期类型")][6])

    def test_foreign_key_is_documented(self):
        rows = {row[:2]: row for row in instruction_rows()[1:]}
        self.assertIn("part.pn", rows[("客户需求订单清单 Customer Demand", "料号 (PN)")][8])

    def test_fk_optional_is_documented(self):
        rows = {row[:2]: row for row in instruction_rows()[1:]}
        note = rows[("产品装配清单 BOM List", "子件：半成品料号 (SF PN)")][8]
        self.assertIn("只 warn", note)

    def test_demand_date_axis_is_documented(self):
        rows = {(row[0], row[1]) for row in instruction_rows()[1:]}
        self.assertIn(("客户需求订单清单 Customer Demand", "（日期列）"), rows)

    def test_preamble_mentions_all_five_extensions(self):
        text = "\n".join(PREAMBLE)
        self.assertIn("客户代码 (Code)", text)
        for sheet in V3_SHEETS:
            self.assertIn(sheet, text)

    def test_preamble_mentions_site_injection(self):
        self.assertTrue(any("--site" in line for line in PREAMBLE))

    def test_instructions_sheet_contains_preamble_and_rows(self):
        ws = self.wb[INSTRUCTIONS_SHEET]
        text = "\n".join(
            str(cell.value)
            for row in ws.iter_rows()
            for cell in row
            if cell.value is not None
        )
        self.assertIn(SITE, text)
        self.assertIn("班次人力可用性", text)
        self.assertIn("提前准备时间 (分钟)", text)
        self.assertIn("客户代码 (Code)", text)
        self.assertLess(len(instruction_rows()) + len(PREAMBLE), ws.max_row)


class TestRoundTrip(TemplateCase):
    def test_generated_template_imports_without_errors(self):
        _tables, log = read_workbook(self.path, SITE)
        self.assertEqual([], [i.message for i in log.errors])

    def test_generated_template_yields_no_data_rows(self):
        tables, _log = read_workbook(self.path, SITE)
        self.assertEqual(1, len(tables["site"]))
        self.assertEqual([], tables["part"])
        self.assertEqual([], tables.get("demand", []))
        self.assertEqual([], tables.get("routing", []))

    def test_missing_v3_sheets_are_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "v2like.xlsx")
            write_v3_template(path, SITE)
            wb = load_workbook(path)
            for sheet in V3_SHEETS:
                wb.remove(wb[sheet])
            wb.save(path)
            _tables, log = read_workbook(path, SITE)
        self.assertEqual([], [i.message for i in log.errors])
        self.assertTrue(any("班次人力可用性" in i.message for i in log.warnings))


if __name__ == "__main__":
    unittest.main()
