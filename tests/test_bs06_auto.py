"""BS-06 auto truncation: deferred orders are removed before re-planning."""
import os
import sys
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.batching import (
    CampaignBatch,
    build_truncation_suggestions,
    select_deferred_orders,
    without_orders,
)
from src.data_loader import build_requirement_plan, load_aps_database


class TestBS06AutoTruncation(unittest.TestCase):
    def test_deferred_orders_are_removed_and_replanned(self):
        snap = load_aps_database(os.path.join(BASE_DIR, "data"), site_filter="WL")
        self.assertTrue(snap.orders)
        target = snap.orders[0].order_id
        suggestions = [
            {
                "feasible": True,
                "batch_id": "CB1",
                "split_qty": 10,
                "target_order_id": "TARGET",
                "split_order_ids": [target],
                "note": "test",
            },
            {
                "feasible": False,
                "batch_id": "CB2",
                "split_qty": 5,
                "target_order_id": "TARGET2",
                "split_order_ids": ["SHOULD_NOT_DEFER"],
                "note": "keep below MOQ",
            },
        ]
        deferred = select_deferred_orders(suggestions)
        self.assertEqual([d["order_id"] for d in deferred], [target])

        reduced = without_orders(snap, [d["order_id"] for d in deferred])
        self.assertNotIn(target, [o.order_id for o in reduced.orders])
        self.assertEqual(len(reduced.orders), len(snap.orders) - 1)
        # The original snapshot must not be mutated.
        self.assertIn(target, [o.order_id for o in snap.orders])

        plan = build_requirement_plan(reduced)
        self.assertNotIn(target, [t.order_id for t in plan.tasks])

    def test_late_batch_produces_deferred_orders_end_to_end(self):
        snap = load_aps_database(os.path.join(BASE_DIR, "data"), site_filter="WL")
        chosen = snap.orders[:3]
        self.assertEqual(len(chosen), 3)
        start = datetime(2026, 10, 12, 7, 0, 0)
        batch_tasks = []
        for idx, order in enumerate(chosen):
            batch_tasks.append(
                SimpleNamespace(
                    task_id=f"B{idx}",
                    order_id=order.order_id,
                    line="PGLINKAGE",
                    part_no="P1",
                    quantity=100.0,
                    ct_seconds=60.0,
                    trp=1.0,
                    due_datetime=start + timedelta(days=1),
                    end_dt=start + timedelta(hours=8 + idx),
                    mold_id="M1",
                )
            )
        late = SimpleNamespace(
            task_id="LATE",
            order_id="LATE-ORDER",
            line="PGLINKAGE",
            part_no="P2",
            quantity=100.0,
            ct_seconds=60.0,
            trp=1.0,
            due_datetime=start + timedelta(hours=4),
            end_dt=start + timedelta(hours=8),
            mold_id="M2",
        )
        batch = CampaignBatch(
            batch_id="CB-LATE",
            site="WL",
            key="M1",
            mold_id="M1",
            campaign_group="G1",
            part_no="P1",
            task_ids=tuple(t.task_id for t in batch_tasks),
            order_ids=tuple(t.order_id for t in batch_tasks),
            total_quantity=300.0,
            earliest_due=start + timedelta(days=1),
            latest_due=start + timedelta(days=1),
            lookahead_days=30,
            min_moq=0,
        )
        suggestions = build_truncation_suggestions([late, *batch_tasks], [batch])
        self.assertEqual(len(suggestions), 1)
        self.assertTrue(suggestions[0]["feasible"])

        deferred = select_deferred_orders(suggestions)
        self.assertTrue(deferred)
        reduced = without_orders(snap, [d["order_id"] for d in deferred])
        deferred_ids = {d["order_id"] for d in deferred}
        self.assertFalse(deferred_ids & {o.order_id for o in reduced.orders})
        plan = build_requirement_plan(reduced)
        self.assertFalse(deferred_ids & {t.order_id for t in plan.tasks})


if __name__ == "__main__":
    unittest.main()
