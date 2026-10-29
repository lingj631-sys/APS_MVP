"""B4-a: inventory policy data layer smoke tests."""
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.inventory_policy import InventoryPolicy, load_inventory_policy, policy_for_day, project_inventory


class TestInventoryPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policies = load_inventory_policy(
            os.path.join(BASE_DIR, "data", "master", "Inventory-Policy.csv"), site="WL"
        )

    def test_both_periods_exist_for_a_part(self):
        self.assertIn(("WL", "WBC9317C", "NORMAL"), self.policies)
        self.assertIn(("WL", "WBC9317C", "MONTH_END"), self.policies)

    def test_month_end_window_wins(self):
        normal = policy_for_day(self.policies, "WL", "WBC9317C", 10)
        month_end = policy_for_day(self.policies, "WL", "WBC9317C", 28)
        self.assertIsNotNone(normal)
        self.assertIsNotNone(month_end)
        self.assertEqual(normal.period_type, "NORMAL")
        self.assertEqual(month_end.period_type, "MONTH_END")
        self.assertLess(month_end.max_days, normal.max_days)

    def test_policy_rows_cover_both_factories(self):
        all_policies = load_inventory_policy(os.path.join(BASE_DIR, "data", "master", "Inventory-Policy.csv"))
        self.assertTrue(any(key[0] == "WL" for key in all_policies))
        self.assertTrue(any(key[0] == "SY38" for key in all_policies))

    def test_projection_flags_stock_above_policy(self):
        from datetime import datetime
        from types import SimpleNamespace

        shifts = [
            SimpleNamespace(
                site="WL", date_str="10/12", shift_name="早班",
                start_dt=datetime(2026, 10, 12, 7, 0), end_dt=datetime(2026, 10, 12, 15, 0),
            )
        ]
        tasks = [SimpleNamespace(part_no="P1", shift_breakdown={("10/12", "早班"): 300})]
        snapshot = SimpleNamespace(
            orders=[SimpleNamespace(part_no="P1", due_datetime=datetime(2026, 10, 12, 15, 0), demand_quantity=100)],
            bom={},
            initial_stock={},
        )
        policies = {
            ("WL", "P1", "NORMAL"): InventoryPolicy(
                "WL", "P1", "NORMAL", 1, 31, 1, 1, 0, 0
            )
        }
        result = project_inventory(tasks, snapshot, shifts, policies, site="WL")
        self.assertEqual(result.metrics["violating_parts"], 1)
        self.assertTrue(result.violations)


if __name__ == "__main__":
    unittest.main()
