from __future__ import annotations

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from aps.data.importer import import_workbook
from aps.data.template_v3 import write_v3_template
from aps.web.server import create_app

FAST = True
SITE = "WL"


class TestWebServer(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = os.path.join(self._tmp.name, "aps.db")
        self.xlsx = os.path.join(self._tmp.name, "v3.xlsx")

        write_v3_template(self.xlsx, SITE)
        import_workbook(self.xlsx, self.db, SITE)

        self.app = create_app()
        self.client = TestClient(self.app)

    def test_get_index_html(self):
        res = self.client.get("/")
        self.assertEqual(200, res.status_code)
        self.assertIn("APS 2.0 智能排产与决策中枢", res.text)

    def test_get_api_status(self):
        res = self.client.get(f"/api/status?db_path={self.db}&site={SITE}")
        self.assertEqual(200, res.status_code)
        data = res.json()
        self.assertEqual("ready", data["status"])
        self.assertGreaterEqual(data["total_rows"], 0)
        self.assertIn("line", data["entity_counts"])

    def test_get_api_dataset(self):
        res = self.client.get(f"/api/dataset?db_path={self.db}&site={SITE}&start_date=2026-11-03&days=5")
        self.assertEqual(200, res.status_code)
        data = res.json()
        self.assertEqual(SITE, data["site"])
        self.assertEqual(5, data["horizon"]["days"])
        self.assertIn("stats", data)

    def test_post_api_solve_empty_template(self):
        res = self.client.post("/api/solve", json={
            "site": SITE,
            "start_date": "2026-11-03",
            "days": 3,
            "timeout": 10.0,
            "calendar_mode": "CONTINUOUS",
            "db_path": self.db,
        })
        self.assertEqual(200, res.status_code)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertIn(data["status"], ("OPTIMAL", "FEASIBLE"))
        self.assertIn("plan_rows", data)
        self.assertIn("line_shifts", data)


if __name__ == "__main__":
    unittest.main()
