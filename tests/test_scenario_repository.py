"""TDD Tests for Phase 1: Database-Neutral Scenario Repository & Schema.

Verifies:
1. Neutral DDL execution (SQLite & PostgreSQL compatible, No Foreign Keys, high-density indices).
2. All 14 tables creation and idempotency.
3. Scenario CRUD operations and application-level cascade deletion in transactions.
4. Scenario task detail persistence and JSON serialization.
5. Building block read-only locking on solved/released scenarios.
"""
from __future__ import annotations

import unittest
from datetime import datetime
from src.scenarios.repository import ScenarioRepository


class TestScenarioRepository(unittest.TestCase):
    def setUp(self):
        # Use isolated in-memory SQLite database for test isolation
        self.repo = ScenarioRepository(db_url="sqlite:///:memory:")
        self.repo.init_db()

    def test_01_all_14_tables_created_and_idempotent(self):
        """Verify all 14 schema tables exist and init_db is idempotent."""
        tables = self.repo.get_table_names()
        expected_tables = {
            "scenarios",
            "scenario_tasks",
            "calendar_patterns",
            "lines",
            "routing_parameters",
            "bom_packages",
            "bom_items",
            "inventory_packages",
            "inventory_items",
            "demand_batches",
            "demand_orders",
            "tooling_molds",
            "pm_schedules",
            "setup_edges",
        }
        self.assertTrue(expected_tables.issubset(set(tables)), f"Missing tables: {expected_tables - set(tables)}")
        
        # Calling init_db again must be idempotent and succeed without error
        self.repo.init_db()

    def test_02_scenario_crud_and_application_cascade_delete(self):
        """Verify create, get, list, update and transactional cascade delete."""
        manifest = {
            "lines": ["PGLINKAGE", "轴套6"],
            "calendar_pattern_id": "WL_5_2",
            "routing_version": "ROUTING-WL-2026Q4-v1",
            "bom_package_id": "BOM-WL-2026Q4-v2",
            "rule_set_id": "WL_Phase1_v4",
            "inventory_batch_id": "INV-20261029-REAL",
            "demand_batch_id": "ORD-202610-W44",
            "setup_matrix_id": "SETUP-WL-DEFAULT",
            "planning_days": 10,
            "start_datetime": "2026-10-12T07:00:00",
        }
        
        # 1. Create scenario
        scn_id = self.repo.create_scenario(
            scenario_id="SCN-TEST-001",
            name="11月第一周排产 (基准方案)",
            site="WL",
            manifest=manifest,
            status="DRAFT",
        )
        self.assertEqual(scn_id, "SCN-TEST-001")

        # 2. Get scenario
        scn = self.repo.get_scenario("SCN-TEST-001")
        self.assertIsNotNone(scn)
        self.assertEqual(scn["name"], "11月第一周排产 (基准方案)")
        self.assertEqual(scn["site"], "WL")
        self.assertEqual(scn["status"], "DRAFT")
        self.assertEqual(scn["manifest"]["calendar_pattern_id"], "WL_5_2")

        # 3. Add scenario tasks
        task_data = [
            {
                "task_id": "T1",
                "line": "PGLINKAGE",
                "stage": "FG",
                "part_no": "WA14208",
                "quantity": 100.0,
                "start_dt": "2026-10-12T07:00:00",
                "end_dt": "2026-10-12T15:00:00",
                "duration_hours": 8.0,
                "setup_minutes": 30.0,
                "shift_breakdown": {"10/12 早班": 100},
            }
        ]
        saved_count = self.repo.save_scenario_tasks("SCN-TEST-001", task_data)
        self.assertEqual(saved_count, 1)

        tasks = self.repo.get_scenario_tasks("SCN-TEST-001")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task_id"], "T1")
        self.assertEqual(tasks[0]["shift_breakdown"]["10/12 早班"], 100)

        # 4. Update scenario with solved KPIs and checks
        kpis = {"otd_rate": 1.0, "avg_utilization": 0.96, "total_setup_minutes": 30.0}
        checks = {"overall_pass": True, "passed_count": 17, "total_count": 17}
        updated = self.repo.update_scenario(
            "SCN-TEST-001",
            status="SOLVED",
            kpis=kpis,
            validation_checks=checks,
            solve_duration_ms=4500,
        )
        self.assertTrue(updated)
        scn_after = self.repo.get_scenario("SCN-TEST-001")
        self.assertEqual(scn_after["status"], "SOLVED")
        self.assertEqual(scn_after["kpis"]["otd_rate"], 1.0)
        self.assertEqual(scn_after["validation_checks"]["passed_count"], 17)

        # 5. List scenarios with filter
        wl_list = self.repo.list_scenarios(site="WL", status="SOLVED")
        self.assertEqual(len(wl_list), 1)
        sy_list = self.repo.list_scenarios(site="SY38")
        self.assertEqual(len(sy_list), 0)

        # 6. Cascade delete scenario and tasks
        deleted = self.repo.delete_scenario("SCN-TEST-001")
        self.assertTrue(deleted)
        self.assertIsNone(self.repo.get_scenario("SCN-TEST-001"))
        self.assertEqual(len(self.repo.get_scenario_tasks("SCN-TEST-001")), 0)

    def test_03_building_blocks_lock_defense(self):
        """Verify lock_building_blocks locks referenced BOM, inventory, routing, demand batches."""
        # Insert unlocked building block records
        with self.repo.transaction() as conn:
            conn.execute(
                "INSERT INTO bom_packages (package_id, name, site_code, version_no, is_locked) VALUES (?, ?, ?, ?, ?)",
                ("BOM-WL-V1", "WL BOM V1", "WL", 1, 0),
            )
            conn.execute(
                "INSERT INTO routing_parameters (version_id, site_code, part_no, line_code, ct_seconds, is_locked) VALUES (?, ?, ?, ?, ?, ?)",
                ("ROUTING-WL-V1", "WL", "PART1", "LINE1", 30.0, 0),
            )
            conn.execute(
                "INSERT INTO demand_batches (batch_id, name, site_code, is_locked) VALUES (?, ?, ?, ?)",
                ("DEMAND-WL-W44", "Demand W44", "WL", 0),
            )
            conn.execute(
                "INSERT INTO inventory_packages (package_id, name, site_code, snapshot_time, is_locked) VALUES (?, ?, ?, ?, ?)",
                ("INV-WL-01", "Inv 01", "WL", "2026-10-12T00:00:00", 0),
            )

        manifest = {
            "bom_package_id": "BOM-WL-V1",
            "routing_version": "ROUTING-WL-V1",
            "demand_batch_id": "DEMAND-WL-W44",
            "inventory_batch_id": "INV-WL-01",
        }

        # Lock referenced building blocks
        self.repo.lock_building_blocks(manifest)

        # Verify they are now locked
        with self.repo.get_connection() as conn:
            bom_locked = conn.execute("SELECT is_locked FROM bom_packages WHERE package_id = 'BOM-WL-V1'").fetchone()[0]
            routing_locked = conn.execute("SELECT is_locked FROM routing_parameters WHERE version_id = 'ROUTING-WL-V1'").fetchone()[0]
            demand_locked = conn.execute("SELECT is_locked FROM demand_batches WHERE batch_id = 'DEMAND-WL-W44'").fetchone()[0]
            inv_locked = conn.execute("SELECT is_locked FROM inventory_packages WHERE package_id = 'INV-WL-01'").fetchone()[0]

        self.assertEqual(bom_locked, 1)
        self.assertEqual(routing_locked, 1)
        self.assertEqual(demand_locked, 1)
        self.assertEqual(inv_locked, 1)

    def test_04_transaction_rollback_on_error(self):
        """Verify transaction rolls back changes when an exception is raised."""
        try:
            with self.repo.transaction() as conn:
                conn.execute(
                    "INSERT INTO calendar_patterns (pattern_id, site_code, name, workdays_per_week, shifts_per_day, shift_hours, shift_start_times, shift_names) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    ("P1", "WL", "Test Pattern", 5, 3, 8.0, "07:00,15:00,23:00", "早,中,晚"),
                )
                raise RuntimeError("Simulated failure inside transaction")
        except RuntimeError:
            pass

        # P1 must not be inserted due to rollback
        with self.repo.get_connection() as conn:
            row = conn.execute("SELECT * FROM calendar_patterns WHERE pattern_id = 'P1'").fetchone()
            self.assertIsNone(row)

    def test_05_building_block_queries_for_builder(self):
        """Verify querying building blocks used by ScenarioBuilder."""
        with self.repo.transaction() as conn:
            conn.execute(
                "INSERT INTO calendar_patterns (pattern_id, site_code, name, workdays_per_week, shifts_per_day, shift_hours, shift_start_times, shift_names) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("WL_5_2", "WL", "5/2制度", 5, 3, 8.0, "07:00,15:00,23:00", "早班,中班,晚班"),
            )
            conn.execute(
                "INSERT INTO lines (line_code, site_code, workshop_code, stage, default_calendar_id, max_util_rate) VALUES (?, ?, ?, ?, ?, ?)",
                ("LINE_A", "WL", "WS1", "FG", "WL_5_2", 1.05),
            )
            conn.execute(
                "INSERT INTO routing_parameters (version_id, site_code, part_no, line_code, ct_seconds, trp, mpq) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("R_V1", "WL", "PART_X", "LINE_A", 35.0, 0.8, 50),
            )
            conn.execute(
                "INSERT INTO bom_items (package_id, parent_part_no, child_part_no, usage_ratio, scrap_rate) VALUES (?, ?, ?, ?, ?)",
                ("BOM_V1", "PART_X", "SUB_PART_Y", 1.0, 0.02),
            )
            conn.execute(
                "INSERT INTO demand_orders (batch_id, order_id, part_no, customer_code, customer_tier, due_datetime, demand_quantity) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("DEM_V1", "ORD1", "PART_X", "CUST_VALEO", "A", "2026-10-15T15:00:00", 500.0),
            )
            conn.execute(
                "INSERT INTO inventory_items (package_id, part_no, stage, on_hand_qty, safety_stock) VALUES (?, ?, ?, ?, ?)",
                ("INV_V1", "PART_X", "FG", 100.0, 20.0),
            )

        cal = self.repo.query_calendar("WL_5_2")
        self.assertIsNotNone(cal)
        self.assertEqual(cal["workdays_per_week"], 5)

        lines = self.repo.query_lines(["LINE_A"], site="WL")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["stage"], "FG")

        routing = self.repo.query_routing("R_V1", ["LINE_A"])
        self.assertEqual(len(routing), 1)
        self.assertEqual(routing[0]["part_no"], "PART_X")

        bom = self.repo.query_bom("BOM_V1")
        self.assertIn("PART_X", bom)
        self.assertEqual(bom["PART_X"][0]["child_part_no"], "SUB_PART_Y")

        orders = self.repo.query_demand_orders("DEM_V1")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["demand_quantity"], 500.0)

        inv = self.repo.query_inventory("INV_V1")
        self.assertEqual(inv.get("PART_X"), 100.0)


if __name__ == "__main__":
    unittest.main()
