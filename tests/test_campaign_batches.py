"""BS-05 SuperOrder grouping: same mold/part orders form a batch."""
import os
import sys
import unittest
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.batching import build_campaign_batches
from src.data_loader import ProductionTask


def _task(tid, part, due_days, qty=100.0):
    start = datetime(2026, 11, 1, 7, 0, 0)
    return ProductionTask(
        task_id=tid,
        stage="FG",
        line="PGLINKAGE",
        part_no=part,
        description=part,
        ct_seconds=60.0,
        trp=1.0,
        mpq=50,
        lot_size=50,
        lots=2,
        quantity=qty,
        lot_millis=2 * 3600 * 1000,
        order_id=f"O-{tid}",
        due_datetime=start + timedelta(days=due_days),
        mold_id="MOLD-X",
    )


class TestCampaignBatches(unittest.TestCase):
    def test_same_mold_part_orders_are_grouped(self):
        tasks = [_task("A", "P1", 1), _task("B", "P1", 2), _task("C", "P2", 2)]
        batches = build_campaign_batches(tasks, {}, site="WL", lookahead_days=10)
        p1 = [b for b in batches if b.part_no == "P1"]
        self.assertEqual(len(p1), 1)
        self.assertEqual(p1[0].total_quantity, 200.0)
        self.assertEqual(p1[0].order_ids, ("O-A", "O-B"))
        self.assertEqual(len([b for b in batches if b.part_no == "P2"]), 0)

    def test_lookahead_window_splits_batches(self):
        tasks = [_task("A", "P1", 1), _task("B", "P1", 2), _task("C", "P1", 20)]
        batches = build_campaign_batches(tasks, {}, site="WL", lookahead_days=10)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].task_ids, ("A", "B"))


if __name__ == "__main__":
    unittest.main()
