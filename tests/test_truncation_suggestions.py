"""BS-06-lite: batch tail-order truncation suggestions."""
import os
import sys
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.batching import CampaignBatch, build_truncation_suggestions, select_deferred_orders, truncation_improves


class TestTruncationSuggestions(unittest.TestCase):
    def test_suggests_tail_orders_while_keeping_moq(self):
        start = datetime(2026, 11, 1, 8, 0, 0)
        tasks = []
        for i in range(5):
            tasks.append(SimpleNamespace(
                task_id=f"B{i}", order_id=f"O-B{i}", line="PGLINKAGE",
                part_no="P1", quantity=100.0, ct_seconds=60.0, trp=1.0,
                due_datetime=start + timedelta(days=1),
                end_dt=start + timedelta(hours=8 + i),
            ))
        late = SimpleNamespace(
            task_id="LATE", order_id="O-LATE", line="PGLINKAGE",
            part_no="P2", quantity=100.0, ct_seconds=60.0, trp=1.0,
            due_datetime=start + timedelta(hours=4),
            end_dt=start + timedelta(hours=8),
        )
        batch = CampaignBatch(
            batch_id="CB1", site="WL", key="M1", mold_id="M1",
            campaign_group="G1", part_no="P1",
            task_ids=tuple(t.task_id for t in tasks),
            order_ids=tuple(t.order_id for t in tasks),
            total_quantity=500.0, earliest_due=start, latest_due=start + timedelta(days=1),
            lookahead_days=30, min_moq=100,
        )
        suggestions = build_truncation_suggestions([late] + tasks, [batch])
        self.assertEqual(len(suggestions), 1)
        item = suggestions[0]
        self.assertTrue(item["feasible"])
        self.assertGreaterEqual(item["split_qty"], 240.0)
        self.assertGreaterEqual(item["keep_qty"], 100.0)
        self.assertTrue(item["split_order_ids"])

    def test_deferred_orders_only_include_feasible_and_dedupe(self):
        suggestions = [
            {"feasible": True, "batch_id": "B1", "split_qty": 200, "target_order_id": "T1",
             "split_order_ids": ["O1", "O2"], "note": "ok"},
            {"feasible": False, "batch_id": "B2", "split_qty": 50, "target_order_id": "T2",
             "split_order_ids": ["O3"], "note": "keep below MOQ"},
            {"feasible": True, "batch_id": "B3", "split_qty": 10, "target_order_id": "T3",
             "split_order_ids": ["O1"], "note": "duplicate"},
        ]
        result = select_deferred_orders(suggestions)
        self.assertEqual([item["order_id"] for item in result], ["O1", "O2"])

    def test_acceptance_guard(self):
        self.assertTrue(truncation_improves(2, -4.0, 1, -2.0))
        self.assertFalse(truncation_improves(1, -2.0, 2, -1.0))
        self.assertTrue(truncation_improves(1, -2.0, 1, 1.0))
        self.assertFalse(truncation_improves(1, 1.0, 1, -1.0))


if __name__ == "__main__":
    unittest.main()
