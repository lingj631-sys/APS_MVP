from __future__ import annotations

import datetime as dt
import unittest

from aps.schema.types import (
    BOOL_TRUE,
    ContractTier,
    DataType,
    DupPolicy,
    ParseError,
    SHIFT_NAME_TO_INDEX,
    parse_enum_code,
    parse_scalar,
    parse_shift_index,
)

FAST = True


class TestParseScalar(unittest.TestCase):
    def test_blank_becomes_none(self):
        for dtype in DataType:
            for blank in (None, "", "   "):
                with self.subTest(dtype=dtype, blank=blank):
                    self.assertIsNone(parse_scalar(blank, dtype))

    def test_text_strips(self):
        self.assertEqual(parse_scalar("  WL  ", DataType.TEXT), "WL")

    def test_int_accepts_integral_float(self):
        self.assertEqual(parse_scalar(48.0, DataType.INT), 48)

    def test_int_rejects_fractional_float(self):
        with self.assertRaises(ParseError):
            parse_scalar(48.5, DataType.INT)

    def test_int_rejects_non_numeric_text(self):
        with self.assertRaises(ParseError):
            parse_scalar("四十八", DataType.INT)

    def test_real(self):
        self.assertEqual(parse_scalar("0.85", DataType.REAL), 0.85)

    def test_bool_variants(self):
        for raw in (True, 1, "True", "true", "是", "Y"):
            with self.subTest(raw=raw):
                self.assertTrue(parse_scalar(raw, DataType.BOOL))
        for raw in (False, 0, "False", "否", "N"):
            with self.subTest(raw=raw):
                self.assertFalse(parse_scalar(raw, DataType.BOOL))

    def test_bool_rejects_garbage(self):
        with self.assertRaises(ParseError):
            parse_scalar("maybe", DataType.BOOL)

    def test_date_from_datetime_object(self):
        raw = dt.datetime(2026, 11, 3, 0, 0)
        self.assertEqual(parse_scalar(raw, DataType.DATE), dt.date(2026, 11, 3))

    def test_date_from_text(self):
        self.assertEqual(parse_scalar("2026-11-03", DataType.DATE), dt.date(2026, 11, 3))
        self.assertEqual(parse_scalar("2026/11/03", DataType.DATE), dt.date(2026, 11, 3))

    def test_time_from_text(self):
        self.assertEqual(parse_scalar("07:00", DataType.TIME), dt.time(7, 0))
        self.assertEqual(parse_scalar("23:00:00", DataType.TIME), dt.time(23, 0, 0))

    def test_time_rejects_range(self):
        # 实测 Parameters-FG.特殊班次 有一行 '6:30-18:30'，不是合法 TIME
        with self.assertRaises(ParseError):
            parse_scalar("6:30-18:30", DataType.TIME)

    def test_datetime(self):
        raw = "2026-11-03 07:00:00"
        self.assertEqual(parse_scalar(raw, DataType.DATETIME), dt.datetime(2026, 11, 3, 7, 0, 0))

    def test_multi_text_splits_both_commas(self):
        self.assertEqual(
            parse_scalar("M1, M2，M3,,", DataType.MULTI_TEXT),
            ("M1", "M2", "M3"),
        )

    def test_multi_text_single_value(self):
        self.assertEqual(parse_scalar("M1", DataType.MULTI_TEXT), ("M1",))


class TestParseEnumCode(unittest.TestCase):
    def test_code_inside_parens(self):
        self.assertEqual(parse_enum_code("避让顺延 (Shift_Delay)"), "Shift_Delay")

    def test_code_outside_parens(self):
        self.assertEqual(parse_enum_code("TIER_A (核心A级)"), "TIER_A")

    def test_bare_ascii_code(self):
        self.assertEqual(parse_enum_code("NORMAL"), "NORMAL")

    def test_bare_chinese_returns_none(self):
        self.assertIsNone(parse_enum_code("早班"))

    def test_none_passthrough(self):
        self.assertIsNone(parse_enum_code(None))


class TestShiftIndex(unittest.TestCase):
    def test_names(self):
        for name, idx in SHIFT_NAME_TO_INDEX.items():
            with self.subTest(name=name):
                self.assertEqual(parse_shift_index(name), idx)

    def test_int_passthrough(self):
        self.assertEqual(parse_shift_index(2), 2)

    def test_unknown_returns_none(self):
        self.assertIsNone(parse_shift_index("凌晨班"))


class TestEnums(unittest.TestCase):
    def test_tier_values(self):
        self.assertEqual([t.value for t in ContractTier], ["T1", "T2"])

    def test_dup_policy_values(self):
        self.assertEqual([p.value for p in DupPolicy], ["error", "sum", "dedupe"])

    def test_bool_true_set(self):
        self.assertIn("是", BOOL_TRUE)


if __name__ == "__main__":
    unittest.main()
