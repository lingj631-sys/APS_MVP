from __future__ import annotations

import datetime as dt
import os
import sqlite3
import tempfile
import unittest

from aps.data.sqlite import SqliteRepository, decode_value, encode_value
from aps.schema.ddl import create_all_ddl
from aps.schema.types import DataType

FAST = True


def _line_row(code="PGLINKAGE", trp=0.85, hours=8.0, shifts=3):
    return {
        "site_code": "WL", "line_code": code, "name": code,
        "trp_efficiency": trp, "shift_hours": hours, "shifts_per_day": shifts,
    }


class TestValueCodec(unittest.TestCase):
    def test_bool_roundtrip(self):
        for value in (True, False):
            self.assertEqual(decode_value(encode_value(value), DataType.BOOL), value)

    def test_date_roundtrip(self):
        value = dt.date(2026, 11, 3)
        self.assertEqual(encode_value(value), "2026-11-03")
        self.assertEqual(decode_value("2026-11-03", DataType.DATE), value)

    def test_time_roundtrip(self):
        value = dt.time(7, 0)
        self.assertEqual(encode_value(value), "07:00:00")
        self.assertEqual(decode_value("07:00:00", DataType.TIME), value)

    def test_datetime_roundtrip(self):
        value = dt.datetime(2026, 11, 3, 7, 0)
        self.assertEqual(encode_value(value), "2026-11-03 07:00:00")
        self.assertEqual(decode_value("2026-11-03 07:00:00", DataType.DATETIME), value)

    def test_multi_text_roundtrip(self):
        value = ("M1", "M2")
        self.assertEqual(encode_value(value), "M1,M2")
        self.assertEqual(decode_value("M1,M2", DataType.MULTI_TEXT), value)

    def test_none_roundtrip(self):
        for dtype in DataType:
            with self.subTest(dtype=dtype):
                self.assertIsNone(encode_value(None))
                self.assertIsNone(decode_value(None, dtype))

    def test_numbers_pass_through(self):
        self.assertEqual(encode_value(48), 48)
        self.assertEqual(encode_value(0.85), 0.85)


class TestSqliteRepository(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.path)
        self.repo = SqliteRepository(self.path)
        self.repo.initialize_schema()

    def tearDown(self):
        self.repo.close()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_schema_created_all_tables(self):
        names = {
            r[0] for r in self.repo._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertIn("routing", names)
        self.assertIn("calendar_day", names)

    def test_foreign_keys_pragma_on(self):
        row = self.repo._conn.execute("PRAGMA foreign_keys").fetchone()
        self.assertEqual(row[0], 1)

    def test_fk_violation_raises(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.insert_many("line", [_line_row(code="GHOST")])
            self.repo.insert_many(
                "routing",
                [{
                    "site_code": "WL", "pn": "X", "op_seq": 10, "line_code": "GHOST",
                    "certified": True, "ct_sec": 27.0,
                }],
            )

    def test_insert_then_list(self):
        self.repo.insert_many("site", [{"site_code": "WL", "name": "温岭"}])
        self.repo.insert_many("line", [_line_row()])
        rows = self.repo.list_by("line")
        self.assertEqual(1, len(rows))
        self.assertEqual("PGLINKAGE", rows[0]["line_code"])
        self.assertEqual(0.85, rows[0]["trp_efficiency"])
        self.assertEqual(3, rows[0]["shifts_per_day"])

    def test_list_by_filters(self):
        self.repo.insert_many("site", [{"site_code": "WL"}, {"site_code": "SY38"}])
        self.repo.insert_many("line", [_line_row("A"), _line_row("B")])
        self.repo.insert_many("line", [dict(_line_row("C"), site_code="SY38")])
        self.assertEqual(2, len(self.repo.list_by("line", site_code="WL")))
        self.assertEqual(1, len(self.repo.list_by("line", site_code="SY38", line_code="C")))

    def test_list_by_is_deterministically_ordered(self):
        self.repo.insert_many("site", [{"site_code": "WL"}])
        self.repo.insert_many("line", [_line_row(c) for c in ("Z线", "A线", "M线")])
        codes = [r["line_code"] for r in self.repo.list_by("line")]
        self.assertEqual(["A线", "M线", "Z线"], codes)

    def test_get_by_pk(self):
        self.repo.insert_many("site", [{"site_code": "WL"}])
        self.repo.insert_many("line", [_line_row()])
        self.assertIsNotNone(self.repo.get_by_pk("line", ("WL", "PGLINKAGE")))
        self.assertIsNone(self.repo.get_by_pk("line", ("WL", "NOPE")))

    def test_get_by_pk_wrong_arity_raises(self):
        with self.assertRaises(ValueError):
            self.repo.get_by_pk("line", ("WL",))

    def test_count(self):
        self.repo.insert_many("site", [{"site_code": "WL"}])
        self.repo.insert_many("line", [_line_row("A"), _line_row("B")])
        self.assertEqual(2, self.repo.count("line"))
        self.assertEqual(1, self.repo.count("line", line_code="A"))

    def test_delete_where(self):
        self.repo.insert_many("site", [{"site_code": "WL"}])
        self.repo.insert_many("line", [_line_row("A"), _line_row("B")])
        self.assertEqual(1, self.repo.delete_where("line", line_code="A"))
        self.assertEqual(1, self.repo.count("line"))

    def test_insert_many_rejects_unknown_field(self):
        with self.assertRaises(KeyError):
            self.repo.insert_many("line", [dict(_line_row(), ghost=1)])

    def test_insert_many_rejects_missing_non_nullable(self):
        with self.assertRaises(KeyError):
            self.repo.insert_many("line", [{"site_code": "WL", "line_code": "A", "name": "A"}])

    def test_insert_many_fills_absent_nullable_columns(self):
        self.repo.insert_many("site", [{"site_code": "WL"}])
        self.repo.insert_many("line", [
            {"site_code": "WL", "line_code": "A", "name": "A",
             "trp_efficiency": 0.9, "shift_hours": 8.0, "shifts_per_day": 3},
        ])
        row = self.repo.get_by_pk("site", ("WL",))
        self.assertEqual({"site_code", "name", "default_calendar_mode"}, set(row))
        self.assertIsNone(row["name"])
        self.assertIsNone(row["default_calendar_mode"])


class TestReplaceSite(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.path)
        self.repo = SqliteRepository(self.path)
        self.repo.initialize_schema()
        self.repo.insert_many("site", [{"site_code": "WL"}, {"site_code": "SY38"}])

    def tearDown(self):
        self.repo.close()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_replace_clears_only_target_site(self):
        self.repo.insert_many("line", [_line_row("OLD")])
        self.repo.insert_many("line", [dict(_line_row("SY"), site_code="SY38")])
        counts = self.repo.replace_site("WL", {"line": [_line_row("NEW")]})
        self.assertEqual({"line": 1}, counts)
        wl = [r["line_code"] for r in self.repo.list_by("line", site_code="WL")]
        sy = [r["line_code"] for r in self.repo.list_by("line", site_code="SY38")]
        self.assertEqual(["NEW"], wl)
        self.assertEqual(["SY"], sy)

    def test_replace_respects_fk_order(self):
        counts = self.repo.replace_site("WL", {
            "part_param_fg": [{
                "site_code": "WL", "pn": "P1", "description": "d", "mpq": 48,
            }],
            "part": [{"site_code": "WL", "pn": "P1", "description": "d", "stage": "FG"}],
            "line": [_line_row()],
        })
        self.assertEqual(1, counts["part_param_fg"])

    def test_replace_is_atomic_on_failure(self):
        with self.assertRaises(Exception):
            self.repo.replace_site("WL", {
                "line": [_line_row("GOOD")],
                "routing": [{"site_code": "WL", "pn": "X", "op_seq": 10,
                             "line_code": "GHOST", "certified": True, "ct_sec": 27.0}],
            })
        self.assertEqual(0, self.repo.count("line", site_code="WL"))

    def test_replace_site_row_itself_is_preserved(self):
        self.repo.replace_site("WL", {})
        self.assertIsNotNone(self.repo.get_by_pk("site", ("WL",)))

    def test_replace_site_creates_missing_site_row_before_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "fresh.db")
            fresh = SqliteRepository(path)
            try:
                fresh.initialize_schema()
                counts = fresh.replace_site(
                    "BRAND", {"line": [dict(_line_row("L"), site_code="BRAND")]}
                )
                self.assertEqual({"line": 1}, counts)
                self.assertIsNotNone(fresh.get_by_pk("site", ("BRAND",)))
                self.assertEqual(1, fresh.count("line", site_code="BRAND"))
            finally:
                fresh.close()

    def test_ddl_script_is_stable_across_calls(self):
        self.assertEqual(create_all_ddl(), create_all_ddl())


if __name__ == "__main__":
    unittest.main()
