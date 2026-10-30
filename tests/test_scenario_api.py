"""TDD Tests for Phase 4: Scenarios Service and RESTful API Endpoints.

Verifies:
1. Scenario CRUD operations via HTTP endpoints (/api/scenarios).
2. Cloning an existing scenario into a new draft.
3. Triggering solver for a scenario (/api/scenarios/{id}/solve) and persisting KPIs + checks.
4. Retrieving Gantt schedule tasks (/api/scenarios/{id}/tasks).
5. Releasing scenario (/api/scenarios/{id}/release) and preventing updates to released scenarios.
6. Multi-scenario KPI comparison arena (/api/scenarios/compare).
"""
from __future__ import annotations

import os
import unittest
from fastapi.testclient import TestClient

from src.server import app
from src.scenarios.repository import ScenarioRepository
from src.scenarios.ingestion import IngestionService
from src.scenarios.service import ScenarioService


class TestScenarioAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Seed default database if needed
        repo = ScenarioRepository()
        repo.init_db()
        ingestion = IngestionService(repo)
        if os.path.exists("dataSource.xlsx"):
            ingestion.ingest_excel("dataSource.xlsx", site="WL", version_tag="2026Q4")
        cls.client = TestClient(app)

    def test_01_scenario_crud_lifecycle(self):
        """Verify scenario creation, retrieval, listing, update, and deletion."""
        payload = {
            "name": "11月基准方案",
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

        # 1. Create
        create_resp = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(create_resp.status_code, 201)
        created = create_resp.json()
        scn_id = created["id"]
        self.assertEqual(created["name"], "11月基准方案")
        self.assertEqual(created["status"], "DRAFT")

        # 2. Get by ID
        get_resp = self.client.get(f"/api/scenarios/{scn_id}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["id"], scn_id)

        # 3. List
        list_resp = self.client.get("/api/scenarios?site=WL")
        self.assertEqual(list_resp.status_code, 200)
        self.assertTrue(any(s["id"] == scn_id for s in list_resp.json()))

        # 4. Update
        update_resp = self.client.put(f"/api/scenarios/{scn_id}", json={"name": "11月优化方案"})
        self.assertEqual(update_resp.status_code, 200)
        self.assertEqual(self.client.get(f"/api/scenarios/{scn_id}").json()["name"], "11月优化方案")

        # 5. Delete
        del_resp = self.client.delete(f"/api/scenarios/{scn_id}")
        self.assertEqual(del_resp.status_code, 200)
        self.assertEqual(self.client.get(f"/api/scenarios/{scn_id}").status_code, 404)

    def test_02_clone_scenario(self):
        """Verify cloning an existing scenario copies manifest into a new draft."""
        parent_payload = {
            "name": "父方案",
            "site": "WL",
            "manifest": {
                "lines": ["PGLINKAGE"],
                "routing_version": "ROUTING-WL-2026Q4",
                "planning_days": 5,
            },
        }
        p_resp = self.client.post("/api/scenarios", json=parent_payload)
        parent_id = p_resp.json()["id"]

        clone_payload = {
            "name": "克隆方案 (微调)",
            "site": "WL",
            "clone_from_id": parent_id,
        }
        c_resp = self.client.post("/api/scenarios", json=clone_payload)
        self.assertEqual(c_resp.status_code, 201)
        cloned = c_resp.json()
        self.assertEqual(cloned["manifest"]["routing_version"], "ROUTING-WL-2026Q4")
        self.assertEqual(cloned["manifest"]["planning_days"], 5)
        self.assertEqual(cloned["status"], "DRAFT")

        # Clean up
        self.client.delete(f"/api/scenarios/{parent_id}")
        self.client.delete(f"/api/scenarios/{cloned['id']}")

    def test_03_solve_scenario_and_persist_kpis(self):
        """Trigger solving for a scenario, verify KPIs, checks and tasks persistence."""
        payload = {
            "name": "可求解验证方案",
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
        scn_id = self.client.post("/api/scenarios", json=payload).json()["id"]

        # Solve
        solve_resp = self.client.post(f"/api/scenarios/{scn_id}/solve", json={"time_limit_seconds": 10.0})
        self.assertEqual(solve_resp.status_code, 200)
        res = solve_resp.json()
        self.assertEqual(res["status"], "SOLVED")
        self.assertIn("otd_rate", res["kpis"])
        self.assertIn("avg_utilization", res["kpis"])
        self.assertIn("total_setup_minutes", res["kpis"])
        self.assertIn("validation_checks", res)

        # Get tasks
        tasks_resp = self.client.get(f"/api/scenarios/{scn_id}/tasks")
        self.assertEqual(tasks_resp.status_code, 200)
        tasks = tasks_resp.json()
        self.assertGreater(len(tasks), 0)

        # Release
        rel_resp = self.client.post(f"/api/scenarios/{scn_id}/release")
        self.assertEqual(rel_resp.status_code, 200)
        self.assertEqual(rel_resp.json()["status"], "RELEASED")

        # Attempt to update released scenario must fail
        cant_update = self.client.put(f"/api/scenarios/{scn_id}", json={"name": "非法修改已发布方案"})
        self.assertEqual(cant_update.status_code, 400)

        # Clean up
        self.client.delete(f"/api/scenarios/{scn_id}")

    def test_04_compare_scenarios(self):
        """Verify comparing multiple scenarios returns comparative KPI matrix."""
        # Create two scenarios
        p1 = {
            "name": "方案A",
            "site": "WL",
            "manifest": {
                "lines": ["PGLINKAGE"],
                "routing_version": "ROUTING-WL-2026Q4",
                "bom_package_id": "BOM-WL-2026Q4",
                "inventory_batch_id": "INV-WL-2026Q4",
                "demand_batch_id": "ORD-WL-2026Q4",
                "planning_days": 5,
                "start_datetime": "2026-10-12T07:00:00",
            },
        }
        id1 = self.client.post("/api/scenarios", json=p1).json()["id"]
        self.client.post(f"/api/scenarios/{id1}/solve")

        p2 = {
            "name": "方案B (What-If)",
            "site": "WL",
            "manifest": {
                "lines": ["PGLINKAGE"],
                "routing_version": "ROUTING-WL-2026Q4",
                "bom_package_id": "BOM-WL-2026Q4",
                "inventory_batch_id": "INV-WL-2026Q4",
                "demand_batch_id": "ORD-WL-2026Q4",
                "planning_days": 5,
                "start_datetime": "2026-10-12T07:00:00",
                "stock_overrides": {"WA14208": 0},
            },
        }
        id2 = self.client.post("/api/scenarios", json=p2).json()["id"]
        self.client.post(f"/api/scenarios/{id2}/solve")

        cmp_resp = self.client.post("/api/scenarios/compare", json={"scenario_ids": [id1, id2]})
        self.assertEqual(cmp_resp.status_code, 200)
        matrix = cmp_resp.json()
        self.assertEqual(len(matrix), 2)
        self.assertEqual(matrix[0]["id"], id1)
        self.assertEqual(matrix[1]["id"], id2)
        self.assertIn("otd_rate", matrix[0])
        self.assertIn("avg_utilization", matrix[0])

        # Clean up
        self.client.delete(f"/api/scenarios/{id1}")
        self.client.delete(f"/api/scenarios/{id2}")

    def test_05_scenarios_html_page_served(self):
        """Verify /scenarios serves the Frontend Workbench UI HTML page."""
        resp = self.client.get("/scenarios")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("APS 模块化多场景推演工作台", resp.text)

    def test_06_manual_building_blocks_crud(self):
        """Verify manual creation, query, and deletion of custom building blocks (Lines, BOM, Calendar, Inventory)."""
        # 1. Manual Line creation & deletion
        line_payload = {
            "line_code": "CUSTOM_LINE_01",
            "site_code": "WL",
            "workshop_code": "WE34",
            "stage": "FG",
            "default_calendar_id": "WL_5_2",
            "max_util_rate": 1.05,
            "default_crew": 2,
        }
        resp = self.client.post("/api/scenarios/blocks/lines", json=line_payload)
        self.assertEqual(resp.status_code, 201)

        bb_resp = self.client.get("/api/scenarios/building-blocks?site=WL")
        self.assertTrue(any(l["line_code"] == "CUSTOM_LINE_01" for l in bb_resp.json()["lines"]))

        del_line = self.client.delete("/api/scenarios/blocks/lines/CUSTOM_LINE_01")
        self.assertEqual(del_line.status_code, 200)

        # 2. Manual BOM Package creation & query
        bom_payload = {
            "package_id": "BOM-WL-CUSTOM-01",
            "name": "WL 自定义测试 BOM",
            "site_code": "WL",
            "version_no": 1,
            "items": [
                {"parent_part_no": "PARENT_A", "child_part_no": "CHILD_1", "usage_ratio": 2.0},
                {"parent_part_no": "PARENT_A", "child_part_no": "CHILD_2", "usage_ratio": 1.0},
            ],
        }
        resp_bom = self.client.post("/api/scenarios/blocks/bom-packages", json=bom_payload)
        self.assertEqual(resp_bom.status_code, 201)

        get_bom = self.client.get("/api/scenarios/blocks/bom-packages/BOM-WL-CUSTOM-01")
        self.assertEqual(get_bom.status_code, 200)
        self.assertEqual(len(get_bom.json()["items"]), 2)

        del_bom = self.client.delete("/api/scenarios/blocks/bom-packages/BOM-WL-CUSTOM-01")
        self.assertEqual(del_bom.status_code, 200)

        # 3. Manual Calendar Pattern creation & deletion
        cal_payload = {
            "pattern_id": "WL_4_3_CUSTOM",
            "site_code": "WL",
            "name": "WL 做四休三节能制",
            "workdays_per_week": 4,
            "shifts_per_day": 3,
            "shift_hours": 8.0,
            "shift_start_times": "07:00,15:00,23:00",
            "shift_names": "早班,中班,晚班",
            "note": "测试工作制",
        }
        resp_cal = self.client.post("/api/scenarios/blocks/calendar-patterns", json=cal_payload)
        self.assertEqual(resp_cal.status_code, 201)

        del_cal = self.client.delete("/api/scenarios/blocks/calendar-patterns/WL_4_3_CUSTOM")
        self.assertEqual(del_cal.status_code, 200)

        # 4. Manual Inventory Package creation
        inv_payload = {
            "package_id": "INV-WL-CUSTOM-01",
            "name": "WL 自定义期初库存",
            "site_code": "WL",
            "items": [
                {"part_no": "WA14208", "stage": "FG", "on_hand_qty": 500.0, "safety_stock": 50.0},
            ],
        }
        resp_inv = self.client.post("/api/scenarios/blocks/inventory-packages", json=inv_payload)
        self.assertEqual(resp_inv.status_code, 201)

        get_inv = self.client.get("/api/scenarios/blocks/inventory-packages/INV-WL-CUSTOM-01")
        self.assertEqual(get_inv.status_code, 200)
        self.assertEqual(len(get_inv.json()["items"]), 1)

        del_inv = self.client.delete("/api/scenarios/blocks/inventory-packages/INV-WL-CUSTOM-01")
        self.assertEqual(del_inv.status_code, 200)

    def test_07_upload_excel_endpoint(self):
        """Verify uploading an Excel file unbundles into building blocks via API."""
        if not os.path.exists("dataSource.xlsx"):
            self.skipTest("dataSource.xlsx not present")

        with open("dataSource.xlsx", "rb") as f:
            files = {"file": ("dataSource.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
            data = {"site": "WL", "version_tag": "UPLOAD_TEST"}
            resp = self.client.post("/api/scenarios/upload-excel", files=files, data=data)

        self.assertEqual(resp.status_code, 200)
        res_json = resp.json()
        self.assertEqual(res_json["status"], "success")
        self.assertIn("bom_package_id", res_json["ingested"])
        self.assertEqual(res_json["ingested"]["bom_package_id"], "BOM-WL-UPLOAD_TEST")

    def test_08_get_scenario_panorama_endpoint(self):
        """Verify GET /api/scenarios/{id}/panorama returns full balance and loads matrix."""
        # 1. Create and solve scenario
        payload = {
            "name": "WL Panorama Test",
            "site": "WL",
            "manifest": {
                "lines": ["PGLINKAGE"],
                "planning_days": 5,
                "start_datetime": "2026-10-12T07:00:00",
                "calendar_pattern_id": "WL_5_2",
                "routing_version": "ROUTING-WL-2026Q4",
                "bom_package_id": "BOM-WL-2026Q4",
                "rule_set_id": "WL_Phase1",
                "inventory_batch_id": "INV-WL-2026Q4",
                "demand_batch_id": "ORD-WL-2026Q4",
                "setup_matrix_id": "SETUP-WL-DEFAULT",
            },
        }
        create_resp = self.client.post("/api/scenarios", json=payload)
        self.assertEqual(create_resp.status_code, 201)
        scn_id = create_resp.json()["id"]

        solve_resp = self.client.post(f"/api/scenarios/{scn_id}/solve")
        self.assertEqual(solve_resp.status_code, 200)

        # 2. Query panorama
        pano_resp = self.client.get(f"/api/scenarios/{scn_id}/panorama")
        self.assertEqual(pano_resp.status_code, 200)
        pano = pano_resp.json()
        self.assertEqual(pano["status"], "success")
        self.assertIn("line_loads", pano)
        self.assertIn("fg_balance", pano)
        self.assertIn("tasks", pano)
        self.assertGreater(len(pano["line_loads"]), 0)
        self.assertGreater(len(pano["fg_balance"]), 0)
        self.assertGreater(len(pano["tasks"]), 0)


if __name__ == "__main__":
    unittest.main()
