"""B2: scrap-rate input recovery is opt-in and increases SF input quantities."""
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.data_loader import build_requirement_plan, load_aps_database


class TestScrapRecovery(unittest.TestCase):
    def test_scrap_recovery_increases_sf_quantity(self):
        snap = load_aps_database(os.path.join(BASE_DIR, "data"), site_filter="WL")
        base = build_requirement_plan(snap, apply_scrap=False)
        scrap = build_requirement_plan(snap, apply_scrap=True)
        self.assertGreater(
            sum(t.quantity for t in scrap.sf_tasks),
            sum(t.quantity for t in base.sf_tasks),
            "3% SF scrap should increase input quantities",
        )


if __name__ == "__main__":
    unittest.main()
