from __future__ import annotations

import unittest

from aps.schema.ddl import SQL_TYPE, create_all_ddl, create_ddl, create_indexes_ddl
from aps.schema.registry import REGISTRY, get_entity
from aps.schema.types import DataType

FAST = True

BANNED = ("AUTOINCREMENT", "INSERT OR REPLACE", "ON CONFLICT", "WITHOUT ROWID", "STRICT")


class TestSqlTypeMap(unittest.TestCase):
    def test_only_four_sql_types(self):
        allowed = {"TEXT", "INTEGER", "REAL", "NUMERIC"}
        for dtype, sql in SQL_TYPE.items():
            with self.subTest(dtype=dtype):
                self.assertIn(sql, allowed)

    def test_every_data_type_mapped(self):
        self.assertEqual(set(SQL_TYPE), set(DataType))

    def test_bool_and_temporal_are_storable(self):
        self.assertEqual(SQL_TYPE[DataType.BOOL], "INTEGER")
        self.assertEqual(SQL_TYPE[DataType.DATE], "TEXT")
        self.assertEqual(SQL_TYPE[DataType.MULTI_TEXT], "TEXT")


class TestCreateDdl(unittest.TestCase):
    def test_routing_ddl_shape(self):
        ddl = create_ddl(get_entity("routing"))
        self.assertIn("CREATE TABLE IF NOT EXISTS routing (", ddl)
        self.assertIn("site_code TEXT NOT NULL", ddl)
        self.assertIn("ct_sec REAL NOT NULL", ddl)
        self.assertIn("certified INTEGER NOT NULL", ddl)
        self.assertIn("PRIMARY KEY (site_code, pn, op_seq, line_code)", ddl)
        self.assertIn('REFERENCES site (site_code)', ddl)
        self.assertIn('REFERENCES line (line_code)', ddl)

    def test_nullable_field_has_no_not_null(self):
        ddl = create_ddl(get_entity("routing"))
        self.assertIn("mold_ids TEXT", ddl)
        self.assertNotIn("mold_ids TEXT NOT NULL", ddl)

    def test_all_entities_generate_ddl(self):
        for name, spec in REGISTRY.items():
            with self.subTest(entity=name):
                self.assertIn(f"CREATE TABLE IF NOT EXISTS {name} (", create_ddl(spec))

    def test_indexes_for_non_pk_fks(self):
        idx = create_indexes_ddl(get_entity("routing"))
        self.assertIn("CREATE INDEX IF NOT EXISTS ix_routing_family_code ON routing (family_code)", idx)

    def test_no_index_on_pk_column(self):
        idx = create_indexes_ddl(get_entity("routing"))
        self.assertNotIn("ix_routing_line_code", idx)
        self.assertNotIn("ix_routing_site_code", idx)
        stock_idx = create_indexes_ddl(get_entity("initial_stock"))
        self.assertNotIn("ix_initial_stock_pn ", stock_idx)


class TestCreateAllDdl(unittest.TestCase):
    def setUp(self):
        self.script = create_all_ddl()

    def test_contains_every_table(self):
        for name in REGISTRY:
            with self.subTest(entity=name):
                self.assertIn(f"CREATE TABLE IF NOT EXISTS {name} (", self.script)

    def test_referenced_tables_created_before_referrers(self):
        positions = {name: self.script.index(f"CREATE TABLE IF NOT EXISTS {name} (") for name in REGISTRY}
        self.assertLess(positions["site"], positions["line"])
        self.assertLess(positions["line"], positions["routing"])
        self.assertLess(positions["part"], positions["demand"])
        self.assertLess(positions["mold"], positions["setup_matrix"])
        self.assertLess(positions["product_family"], positions["part"])

    def test_no_banned_sqlite_dialect(self):
        upper = self.script.upper()
        for token in BANNED:
            with self.subTest(token=token):
                self.assertNotIn(token, upper)

    def test_idempotent_with_if_not_exists(self):
        self.assertEqual(self.script.count("CREATE TABLE IF NOT EXISTS routing ("), 1)
        self.assertIn("CREATE TABLE IF NOT EXISTS", self.script)


if __name__ == "__main__":
    unittest.main()
