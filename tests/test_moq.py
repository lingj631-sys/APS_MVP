# -*- coding: utf-8 -*-
"""MOQ (minimum production quantity) semantics.

Business rule confirmed 2026-11-07:
  * demand below MOQ still starts the line at MOQ  (MPQ=10, MOQ=100, 1 pc -> 100)
  * above MOQ the quantity grows in whole MPQ steps (188 -> 100 + 9x10 = 190)
  * an MOQ that is not a multiple of MPQ is rounded up to the next MPQ multiple
  * when MOQ == MPQ the result is identical to plain MPQ rounding (no regression)
"""
import unittest
from datetime import datetime

from src.data_loader import (
    CustomerOrder,
    LineBase,
    ProductParam,
    ProductionInputSnapshot,
    build_requirement_plan,
    plan_moq_floor,
    plan_production_quantity,
)


def _snapshot(demand: float, mpq: float, moq: float, stock: float = 0.0, stage: str = "FG") -> ProductionInputSnapshot:
    fg = {
        "P1": ProductParam(
            site="WL", line="L1", part_no="P1", description="P1", ct_seconds=36.0, trp=1.0,
            mpq=mpq, moq=moq, output_per_shift=100.0, headcount=1.0,
        )
    }
    sf = {}
    bom = {}
    if stage == "SF":
        sf = {"S1": ProductParam(
            site="WL", line="L2", part_no="S1", description="S1", ct_seconds=36.0, trp=1.0,
            mpq=mpq, moq=moq, output_per_shift=100.0, headcount=1.0,
        )}
        bom = {"P1": [("S1", 1.0)]}
    return ProductionInputSnapshot(
        fg_params=fg,
        sf_params=sf,
        bom=bom,
        initial_stock={"P1": stock} if stage == "FG" else {},
        orders=[CustomerOrder(order_id="O1", part_no="P1", description="P1", due_date="11/10",
                              due_datetime=datetime(2026, 11, 10, 15, 0), demand_quantity=float(demand))],
        planning_dates=["11/9", "11/10"],
        tooling_molds=[],
        pm_schedules=[],
        material_arrivals=[],
        customer_tiers=[],
        line_base=[LineBase(line="L1", trp=1.0, hours_per_shift=8.0, shifts_per_day=3),
                   LineBase(line="L2", trp=1.0, hours_per_shift=8.0, shifts_per_day=3)],
        warnings=[],
        source_path="",
        layout={},
    )


class TestMoqQuantity(unittest.TestCase):
    def test_user_table(self):
        # (demand, mpq, moq, expected)
        cases = [
            (1, 10, 100, 100),
            (50, 10, 100, 100),
            (100, 10, 100, 100),
            (101, 10, 100, 110),
            (188, 10, 100, 190),
            (200, 10, 100, 200),
        ]
        for demand, mpq, moq, expected in cases:
            with self.subTest(demand=demand):
                self.assertEqual(plan_production_quantity(demand, mpq, moq), expected)

    def test_moq_equals_mpq_is_old_behaviour(self):
        for demand in (1, 9, 10, 11, 150, 188, 3057):
            self.assertEqual(
                plan_production_quantity(demand, 10, 10),
                ((demand + 9) // 10) * 10 if demand > 0 else 0,
            )

    def test_moq_not_multiple_of_mpq_rounds_up(self):
        self.assertEqual(plan_moq_floor(10, 95), 100)
        self.assertEqual(plan_production_quantity(1, 10, 95), 100)
        self.assertEqual(plan_production_quantity(120, 10, 95), 120)  # 100 + 2x10
        self.assertEqual(plan_production_quantity(121, 10, 95), 130)

    def test_zero_demand_and_zero_moq(self):
        self.assertEqual(plan_production_quantity(0, 10, 100), 0)
        self.assertEqual(plan_production_quantity(-5, 10, 100), 0)
        self.assertEqual(plan_moq_floor(10, 0), 10)   # missing MOQ falls back to MPQ

    def test_plan_produces_moq_when_demand_is_tiny(self):
        plan = build_requirement_plan(_snapshot(1, 10, 100))
        self.assertEqual(len(plan.fg_tasks), 1)
        task = plan.fg_tasks[0]
        self.assertEqual(task.quantity, 100.0)
        self.assertEqual(task.lots, 10)          # 100 / MPQ 10
        self.assertEqual(task.mpq, 10)

    def test_stock_offsets_before_moq(self):
        # 60 in stock, demand 1 -> nothing to build at all
        plan = build_requirement_plan(_snapshot(1, 10, 100, stock=60))
        self.assertEqual(plan.fg_tasks, [])
        # 40 in stock, demand 1 -> net 0 after stock? 1+0-40 <= 0 -> covered
        plan2 = build_requirement_plan(_snapshot(1, 10, 100, stock=40))
        self.assertEqual(plan2.fg_tasks, [])
        # no stock at all -> full MOQ
        plan3 = build_requirement_plan(_snapshot(1, 10, 100))
        self.assertEqual(plan3.fg_tasks[0].quantity, 100.0)

    def test_sf_quantity_respects_moq_floor(self):
        plan = build_requirement_plan(_snapshot(1, 10, 100, stage="SF"))
        self.assertTrue(plan.sf_tasks)
        sf_task = plan.sf_tasks[0]
        self.assertGreaterEqual(sf_task.quantity, 100.0)

    def test_sf_quantity_unchanged_when_moq_equals_mpq(self):
        plan = build_requirement_plan(_snapshot(188, 10, 10, stage="SF"))
        self.assertEqual(plan.fg_tasks[0].quantity, 190.0)   # 188 -> 190 by MPQ
        self.assertEqual(plan.sf_tasks[0].quantity, 190.0)   # BOM pulls exactly the FG qty


if __name__ == "__main__":
    unittest.main()
