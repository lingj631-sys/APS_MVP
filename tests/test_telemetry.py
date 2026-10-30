"""TDD Tests for Option A: Automatic Function Telemetry and Database Tracing."""
from __future__ import annotations

import logging
import sqlite3
import time
import unittest
from io import StringIO

from src.infrastructure.telemetry import (
    summarize_value,
    trace,
    wrap_traced_connection,
    get_logger,
)


class TestTelemetry(unittest.TestCase):
    def setUp(self):
        self.logger = get_logger("aps.test")
        self.log_stream = StringIO()
        self.handler = logging.StreamHandler(self.log_stream)
        self.handler.setLevel(logging.DEBUG)
        self.logger.addHandler(self.handler)
        self.logger.setLevel(logging.DEBUG)

    def tearDown(self):
        self.logger.removeHandler(self.handler)

    def test_01_summarize_value_prevents_log_explosion(self):
        """Verify summarize_value formats primitives, truncates large lists and dicts, and formats domain objects."""
        # Primitives
        self.assertEqual(summarize_value(123), 123)
        self.assertEqual(summarize_value(True), True)
        self.assertEqual(summarize_value("short"), "short")
        self.assertTrue(summarize_value("a" * 100).endswith("..."))

        # Large list
        large_list = list(range(100))
        summary_list = summarize_value(large_list)
        self.assertIn("len=100", str(summary_list))

        # Large dict
        large_dict = {f"k{i}": i for i in range(50)}
        summary_dict = summarize_value(large_dict)
        self.assertIn("keys_count=50", str(summary_dict))

        # Domain-like object
        class DummyTask:
            task_id = "TASK_001"
            line = "PGLINE"
            quantity = 50.0
        self.assertEqual(summarize_value(DummyTask()), "Task(TASK_001, line=PGLINE, qty=50.0)")

    def test_02_trace_decorator_logs_inputs_outputs_and_duration(self):
        """Verify @trace decorator logs function entry with args, exit with result, and elapsed ms."""
        @trace(stage="COMPUTE", logger_name="aps.test")
        def add_numbers(a: int, b: int) -> int:
            time.sleep(0.01)
            return a + b

        res = add_numbers(10, 20)
        self.assertEqual(res, 30)

        logs = self.log_stream.getvalue()
        self.assertIn("[COMPUTE]", logs)
        self.assertIn("ENTER", logs)
        self.assertIn("add_numbers", logs)
        self.assertIn("EXIT", logs)
        self.assertIn("耗时:", logs)
        self.assertIn("返回: 30", logs)

    def test_03_trace_decorator_logs_exceptions(self):
        """Verify @trace decorator logs failures with elapsed ms and re-raises cleanly."""
        @trace(stage="RISK", logger_name="aps.test")
        def failing_calc():
            raise ValueError("Invalid calculation input")

        with self.assertRaises(ValueError):
            failing_calc()

        logs = self.log_stream.getvalue()
        self.assertIn("[RISK]", logs)
        self.assertIn("FAIL", logs)
        self.assertIn("Invalid calculation input", logs)

    def test_04_database_proxy_traces_queries_and_batches(self):
        """Verify wrap_traced_connection logs SQL execution, parameters, and batch rows."""
        raw_conn = sqlite3.connect(":memory:")
        traced_conn = wrap_traced_connection(raw_conn, logger_name="aps.test")

        # Execute single statement
        traced_conn.execute("CREATE TABLE items (id INT, name TEXT)")
        traced_conn.execute("INSERT INTO items VALUES (?, ?)", (1, "Apple"))

        # Executemany batch
        batch = [(2, "Banana"), (3, "Orange")]
        traced_conn.executemany("INSERT INTO items VALUES (?, ?)", batch)

        # Query
        rows = traced_conn.execute("SELECT * FROM items").fetchall()
        self.assertEqual(len(rows), 3)

        logs = self.log_stream.getvalue()
        self.assertIn("[DB:EXEC]", logs)
        self.assertIn("CREATE TABLE items", logs)
        self.assertIn("[DB:BATCH]", logs)
        self.assertIn("batch_rows=2", logs)


if __name__ == "__main__":
    unittest.main()
