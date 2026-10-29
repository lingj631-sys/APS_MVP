"""Tests for the Phase C sequence primitive (C1-a)."""
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.sequence import (
    SequenceSegment,
    load_setup_matrix,
    sequence_segments,
)


class TestSequencePrimitive(unittest.TestCase):
    def test_sequence_prefers_same_mold_and_uses_matrix(self):
        matrix = {}
        # only A->B has an explicit edge; same mold is always 0
        from src.sequence import SetupEdge
        matrix[("WL", "L1", "M_A", "M_B")] = SetupEdge("WL", "L1", "M_A", "M_B", 30.0, 1)
        segments = {
            "10/12 早班": [
                SequenceSegment("T-A1", "L1", "P-A", "M_A", 10, "10/12 早班", processing_minutes=10),
                SequenceSegment("T-B1", "L1", "P-B", "M_B", 10, "10/12 早班", processing_minutes=10),
                SequenceSegment("T-A2", "L1", "P-A", "M_A", 10, "10/12 早班", processing_minutes=10),
            ]
        }
        plan = sequence_segments(
            segments, matrix, site="WL", line="L1", default_minutes=60.0,
            capacity_minutes_by_shift={"10/12 早班": 100.0},
        )
        ids = [s.task_id for s in plan.steps]
        self.assertEqual(len(ids), 3)
        self.assertEqual(abs(ids.index("T-A1") - ids.index("T-A2")), 1)
        self.assertEqual(plan.setup_minutes_by_shift["10/12 早班"], 30.0)
        self.assertEqual(plan.production_minutes_by_shift["10/12 早班"], 30.0)
        self.assertEqual(plan.overload_shifts, [])

    def test_mock_setup_matrix_is_loadable_and_covers_a_pair(self):
        path = os.path.join(BASE_DIR, "data", "master", "Setup-Matrix.csv")
        matrix = load_setup_matrix(path)
        self.assertGreater(len(matrix), 0)
        self.assertTrue(any(key[0] == "WL" and key[1] == "轴套6" for key in matrix))


if __name__ == "__main__":
    unittest.main()
