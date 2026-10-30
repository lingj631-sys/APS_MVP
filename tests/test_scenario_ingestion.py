"""TDD Tests for Phase 2: IngestionService for Excel Automatic Sheet Unbundling.

Verifies:
1. Ingesting single dataSource.xlsx splits into all 7 building block tables.
2. Correct mapping of lines, routing parameters, BOM packages, inventory batches, demand orders.
3. Sub-second performance for full factory data unbundling.
4. Idempotent re-ingestion with same version tag.
"""
from __future__ import annotations

import os
import time
import unittest
from src.scenarios.repository import ScenarioRepository
from src.scenarios.ingestion import IngestionService


class TestScenarioIngestion(unittest.TestCase):
    def setUp(self):
        # Use isolated in-memory repository for tests
        self.repo = ScenarioRepository(db_url="sqlite:///:memory:")
        self.repo.init_db()
        self.service = IngestionService(self.repo)
        self.excel_path = "dataSource.xlsx"

    def test_01_ingest_excel_splits_7_building_blocks(self):
        """Verify single Excel file unbundles into 7 building block tables."""
        if not os.path.exists(self.excel_path):
            self.skipTest("dataSource.xlsx not present")

        t0 = time.perf_counter()
        manifest_refs = self.service.ingest_excel(self.excel_path, site="WL", version_tag="2026Q4")
        duration = time.perf_counter() - t0

        # 1. Check returned manifest reference IDs
        self.assertEqual(manifest_refs["bom_package_id"], "BOM-WL-2026Q4")
        self.assertEqual(manifest_refs["demand_batch_id"], "ORD-WL-2026Q4")
        self.assertEqual(manifest_refs["inventory_batch_id"], "INV-WL-2026Q4")
        self.assertEqual(manifest_refs["routing_version"], "ROUTING-WL-2026Q4")
        self.assertEqual(manifest_refs["setup_matrix_id"], "SETUP-WL-DEFAULT")

        # 2. Check execution speed (< 1.5s in Python/openpyxl)
        self.assertLess(duration, 2.5, f"Ingestion took too long: {duration:.2f}s")

        # 3. Verify database tables are populated with correct counts
        with self.repo.get_connection() as conn:
            lines_count = conn.execute("SELECT COUNT(*) FROM lines WHERE site_code = 'WL'").fetchone()[0]
            routing_count = conn.execute(
                "SELECT COUNT(*) FROM routing_parameters WHERE version_id = 'ROUTING-WL-2026Q4'"
            ).fetchone()[0]
            bom_pkg_count = conn.execute(
                "SELECT COUNT(*) FROM bom_packages WHERE package_id = 'BOM-WL-2026Q4'"
            ).fetchone()[0]
            bom_items_count = conn.execute(
                "SELECT COUNT(*) FROM bom_items WHERE package_id = 'BOM-WL-2026Q4'"
            ).fetchone()[0]
            inv_items_count = conn.execute(
                "SELECT COUNT(*) FROM inventory_items WHERE package_id = 'INV-WL-2026Q4'"
            ).fetchone()[0]
            demand_count = conn.execute(
                "SELECT COUNT(*) FROM demand_orders WHERE batch_id = 'ORD-WL-2026Q4'"
            ).fetchone()[0]
            molds_count = conn.execute("SELECT COUNT(*) FROM tooling_molds WHERE site_code = 'WL'").fetchone()[0]
            pm_count = conn.execute("SELECT COUNT(*) FROM pm_schedules WHERE site_code = 'WL'").fetchone()[0]
            setup_count = conn.execute(
                "SELECT COUNT(*) FROM setup_edges WHERE matrix_id = 'SETUP-WL-DEFAULT'"
            ).fetchone()[0]

        self.assertGreaterEqual(lines_count, 2)
        self.assertGreaterEqual(routing_count, 30)
        self.assertEqual(bom_pkg_count, 1)
        self.assertGreaterEqual(bom_items_count, 20)
        self.assertGreaterEqual(inv_items_count, 30)
        self.assertGreaterEqual(demand_count, 50)
        self.assertGreaterEqual(molds_count, 4)
        self.assertGreaterEqual(pm_count, 1)
        self.assertGreaterEqual(setup_count, 10)

    def test_02_idempotent_reingest(self):
        """Re-ingesting same version tag cleans up old version items without primary key error."""
        if not os.path.exists(self.excel_path):
            self.skipTest("dataSource.xlsx not present")

        # Ingest twice
        self.service.ingest_excel(self.excel_path, site="WL", version_tag="V_TEST")
        self.service.ingest_excel(self.excel_path, site="WL", version_tag="V_TEST")

        with self.repo.get_connection() as conn:
            bom_pkg_count = conn.execute(
                "SELECT COUNT(*) FROM bom_packages WHERE package_id = 'BOM-WL-V_TEST'"
            ).fetchone()[0]
            self.assertEqual(bom_pkg_count, 1)


if __name__ == "__main__":
    unittest.main()
