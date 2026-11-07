# -*- coding: utf-8 -*-
"""PSI balance: stock never goes negative, the shortfall is reported as backorder."""
import unittest

from src.balance_builder import roll_stock_balance


class TestPsiBackorder(unittest.TestCase):
    def test_shortfall_becomes_backorder_and_is_cleared_later(self):
        keys = ["S1", "S2", "S3", "S4"]
        demand = {"S1": 100}
        plan = {"S3": 60, "S4": 60}
        d, p, s, b = roll_stock_balance(40, keys, demand, plan)
        self.assertEqual(s["S1"], 0)          # never negative
        self.assertEqual(b["S1"], 60)         # 100 demanded, 40 in stock
        self.assertEqual(b["S2"], 60)         # carried
        self.assertEqual(b["S3"], 0)          # 60 produced clears it
        self.assertEqual(s["S3"], 0)
        self.assertEqual(s["S4"], 60)         # the second batch goes to stock
        self.assertEqual(b["S4"], 0)
        self.assertTrue(all(v >= 0 for v in s.values()))

    def test_covered_demand_has_no_backorder(self):
        keys = ["S1", "S2"]
        d, p, s, b = roll_stock_balance(40, keys, {"S1": 10}, {"S1": 0})
        self.assertEqual(b["S1"], 0)
        self.assertEqual(s["S1"], 30)
        self.assertTrue(all(v == 0 for v in b.values()))

    def test_backlog_is_served_before_new_demand(self):
        keys = ["S1", "S2"]
        # S1: demand 100, stock 0 -> backorder 100; S2: produce 150 -> serve 100, 50 left
        d, p, s, b = roll_stock_balance(0, keys, {"S1": 100, "S2": 50}, {"S2": 150})
        self.assertEqual(b["S1"], 100)
        self.assertEqual(b["S2"], 0)
        self.assertEqual(s["S2"], 0)

    def test_zero_demand_keeps_stock_flat(self):
        keys = ["S1", "S2"]
        d, p, s, b = roll_stock_balance(25, keys, {}, {})
        self.assertEqual(s["S1"], 25)
        self.assertEqual(s["S2"], 25)
        self.assertTrue(all(v == 0 for v in b.values()))


if __name__ == "__main__":
    unittest.main()
