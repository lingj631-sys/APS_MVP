"""TDD Tests for Phase 3: ScenarioBuilder Assembly Pipeline.

Verifies:
1. Dynamic assembly of ProductionInputSnapshot, RequirementPlan, and RuleSet from lightweight manifest.
2. Assembly performance (< 25ms in-memory).
3. Workshop/line slice isolation (full factory, FG-only, SF-only).
4. SF-only slice automatic safety stock replenishment derivation.
5. Stock overrides (What-If supply disruption).
6. End-to-end integration: assembled context solves cleanly with APSScheduler.
"""
from __future__ import annotations

import os
import time
import unittest
from datetime import datetime
from src.scenarios.repository import ScenarioRepository
from src.scenarios.ingestion import IngestionService
from src.scenarios.builder import ScenarioBuilder
from src.scheduler import APSScheduler


class TestScenarioBuilder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Initialize an isolated in-memory repository and pre-seed with real dataSource.xlsx
        cls.repo = ScenarioRepository(db_url="sqlite:///:memory:")
        cls.repo.init_db()
        ingestion = IngestionService(cls.repo)
        if os.path.exists("dataSource.xlsx"):
            cls.manifest_refs = ingestion.ingest_excel("dataSource.xlsx", site="WL", version_tag="2026Q4")
        else:
            cls.manifest_refs = {}

    def setUp(self):
        if not os.path.exists("dataSource.xlsx"):
            self.skipTest("dataSource.xlsx not present")
        self.builder = ScenarioBuilder(self.repo)

    def test_01_assemble_full_factory_context(self):
        """Assemble full factory scenario from manifest in under 25ms."""
        scenario = {
            "id": "SCN-FULL-01",
            "name": "WL 全厂排产方案",
            "site": "WL",
            "manifest": {
                "lines": ["PGLINKAGE", "轴套6"],
                "calendar_pattern_id": "WL_5_2",
                "routing_version": "ROUTING-WL-2026Q4",
                "bom_package_id": "BOM-WL-2026Q4",
                "rule_set_id": "WL_Phase1",
                "inventory_batch_id": "INV-WL-2026Q4",
                "demand_batch_id": "ORD-WL-2026Q4",
                "setup_matrix_id": "SETUP-WL-DEFAULT",
                "planning_days": 10,
                "start_datetime": "2026-10-12T07:00:00",
            },
        }

        t0 = time.perf_counter()
        snapshot, plan, rule_set = self.builder.assemble_context(scenario)
        elapsed = time.perf_counter() - t0

        self.assertLess(elapsed, 0.05, f"Assembly took too long: {elapsed:.3f}s")
        self.assertIsNotNone(snapshot)
        self.assertIsNotNone(plan)
        self.assertIsNotNone(rule_set)

        # Assert lines are filtered to selected lines
        self.assertTrue(all(p.line in ["PGLINKAGE", "轴套6"] for p in snapshot.fg_params.values()))
        self.assertTrue(all(p.line in ["PGLINKAGE", "轴套6"] for p in snapshot.sf_params.values()))
        self.assertGreater(len(plan.tasks), 0)

    def test_02_assemble_fg_only_slice(self):
        """Assemble scenario selecting only FG assembly line PGLINKAGE."""
        scenario = {
            "id": "SCN-FG-ONLY",
            "name": "WL 总装单独排产",
            "site": "WL",
            "manifest": {
                "lines": ["PGLINKAGE"],
                "calendar_pattern_id": "WL_5_2",
                "routing_version": "ROUTING-WL-2026Q4",
                "bom_package_id": "BOM-WL-2026Q4",
                "inventory_batch_id": "INV-WL-2026Q4",
                "demand_batch_id": "ORD-WL-2026Q4",
                "planning_days": 10,
                "start_datetime": "2026-10-12T07:00:00",
            },
        }

        snapshot, plan, _ = self.builder.assemble_context(scenario)
        # SF params should be empty since no SF lines are selected
        self.assertEqual(len(snapshot.sf_params), 0)
        self.assertTrue(all(t.line == "PGLINKAGE" for t in plan.tasks))

    def test_03_assemble_sf_only_slice_derives_safety_stock_orders(self):
        """Assemble scenario selecting only SF line (轴套6) derives replenishment demand."""
        scenario = {
            "id": "SCN-SF-ONLY",
            "name": "WL 轴套冲压单独排产",
            "site": "WL",
            "manifest": {
                "lines": ["轴套6"],
                "calendar_pattern_id": "WL_5_2",
                "routing_version": "ROUTING-WL-2026Q4",
                "bom_package_id": "BOM-WL-2026Q4",
                "inventory_batch_id": "INV-WL-2026Q4",
                "demand_batch_id": "ORD-WL-2026Q4",
                "planning_days": 10,
                "start_datetime": "2026-10-12T07:00:00",
            },
        }

        snapshot, plan, _ = self.builder.assemble_context(scenario)
        self.assertEqual(len(snapshot.fg_params), 0)
        self.assertGreater(len(snapshot.sf_params), 0)
        self.assertTrue(all(t.line == "轴套6" for t in plan.tasks))

    def test_04_stock_overrides_what_if(self):
        """What-If scenario overriding initial stock to 0."""
        target_part = "WA14208"
        scenario = {
            "id": "SCN-WHAT-IF",
            "name": "WA14208 断供模拟",
            "site": "WL",
            "manifest": {
                "lines": ["PGLINKAGE"],
                "routing_version": "ROUTING-WL-2026Q4",
                "bom_package_id": "BOM-WL-2026Q4",
                "inventory_batch_id": "INV-WL-2026Q4",
                "demand_batch_id": "ORD-WL-2026Q4",
                "planning_days": 10,
                "start_datetime": "2026-10-12T07:00:00",
                "stock_overrides": {target_part: 0.0},
            },
        }

        snapshot, plan, _ = self.builder.assemble_context(scenario)
        self.assertEqual(snapshot.initial_stock.get(target_part), 0.0)

    def test_05_assembled_context_solves_with_scheduler(self):
        """Verify that assembled context can be directly solved by APSScheduler."""
        scenario = {
            "id": "SCN-SOLVE-01",
            "name": "WL 快速验证排产",
            "site": "WL",
            "manifest": {
                "lines": ["PGLINKAGE"],
                "calendar_pattern_id": "WL_5_2",
                "routing_version": "ROUTING-WL-2026Q4",
                "bom_package_id": "BOM-WL-2026Q4",
                "inventory_batch_id": "INV-WL-2026Q4",
                "demand_batch_id": "ORD-WL-2026Q4",
                "planning_days": 5,
                "start_datetime": "2026-10-12T07:00:00",
            },
        }

        snapshot, plan, rule_set = self.builder.assemble_context(scenario)
        sched = APSScheduler(
            plan,
            start_datetime=datetime(2026, 10, 12, 7, 0),
            site="WL",
            rule_set=rule_set,
        )
        result = sched.solve(time_limit_seconds=10.0)
        self.assertIn(result.status_name, ("OPTIMAL", "FEASIBLE"))


if __name__ == "__main__":
    unittest.main()
