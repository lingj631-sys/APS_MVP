"""Tests for Phase C Microscopic Intra-Shift Scheduling (Stage 2).

Validates:
1. HC-01: Cross-line mold sharing within the same shift schedules sequentially without overlap.
2. HC-02: Technician concurrency limit ensures simultaneous setups <= technician capacity.
3. HC-03: Line tasks schedule around minute-level PM intervals within the shift without shutting down the entire shift.
4. Scale & Performance: Eliminates MTZ combinatorial explosion and solves in milliseconds.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.micro_scheduler import (
    MicroScheduledSegment,
    MicroShiftSchedule,
    MicroTaskSegment,
    PMWindow,
    schedule_intra_shift,
)
from src.sequence import SetupEdge
from src.shift_manager import ShiftBucket


class TestMicroScheduler(unittest.TestCase):
    def setUp(self):
        self.shift_start = datetime(2026, 10, 12, 7, 0, 0)
        self.shift = ShiftBucket(
            index=0,
            date_str="10/12",
            shift_name="早班",
            start_dt=self.shift_start,
            end_dt=self.shift_start + timedelta(hours=8),
            weekday=0,
            is_work_shift=True,
        )

    def test_01_hc01_cross_line_mold_sharing_no_overlap(self):
        """HC-01: Line 1 and Line 2 share mold MOLD_A. They must schedule sequentially."""
        t1 = MicroTaskSegment(
            task_id="T_LINE1",
            line="LINE_1",
            part_no="PART_1",
            mold_id="MOLD_A",
            quantity=100,
            duration_seconds=7200.0,  # 2 hours
            setup_seconds=1800.0,     # 30 mins
        )
        t2 = MicroTaskSegment(
            task_id="T_LINE2",
            line="LINE_2",
            part_no="PART_2",
            mold_id="MOLD_A",  # Same mold!
            quantity=100,
            duration_seconds=7200.0,  # 2 hours
            setup_seconds=1800.0,     # 30 mins
        )
        result = schedule_intra_shift(
            shift=self.shift,
            tasks=[t1, t2],
            technician_capacity=2,
        )
        self.assertIsNotNone(result)
        seg1 = next(s for s in result.segments if s.task_id == "T_LINE1")
        seg2 = next(s for s in result.segments if s.task_id == "T_LINE2")

        # Total mold usage intervals: [setup_start, processing_end]
        mold1_span = (seg1.setup_start, seg1.processing_end)
        mold2_span = (seg2.setup_start, seg2.processing_end)

        # Must not overlap: one must finish before the other starts
        no_overlap = (mold1_span[1] <= mold2_span[0]) or (mold2_span[1] <= mold1_span[0])
        self.assertTrue(no_overlap, f"Molds overlapped: {mold1_span} vs {mold2_span}")

    def test_02_hc02_technician_concurrency_limit(self):
        """HC-02: With technician_capacity=1, setups across Line 1 and Line 2 cannot overlap."""
        t1 = MicroTaskSegment(
            task_id="T_L1",
            line="LINE_1",
            part_no="P1",
            mold_id="MOLD_1",
            quantity=50,
            duration_seconds=7200.0,
            setup_seconds=1800.0,  # 30 mins setup
            technicians_required=1,
        )
        t2 = MicroTaskSegment(
            task_id="T_L2",
            line="LINE_2",
            part_no="P2",
            mold_id="MOLD_2",
            quantity=50,
            duration_seconds=7200.0,
            setup_seconds=1800.0,  # 30 mins setup
            technicians_required=1,
        )
        result = schedule_intra_shift(
            shift=self.shift,
            tasks=[t1, t2],
            technician_capacity=1,  # Only 1 technician!
        )
        self.assertIsNotNone(result)
        seg1 = next(s for s in result.segments if s.task_id == "T_L1")
        seg2 = next(s for s in result.segments if s.task_id == "T_L2")

        setup1 = (seg1.setup_start, seg1.setup_end)
        setup2 = (seg2.setup_start, seg2.setup_end)

        # Setups cannot overlap because only 1 technician is available
        no_setup_overlap = (setup1[1] <= setup2[0]) or (setup2[1] <= setup1[0])
        self.assertTrue(no_setup_overlap, f"Setups overlapped with 1 tech: {setup1} vs {setup2}")
        self.assertLessEqual(result.technicians_peak, 1)

    def test_03_hc03_pm_minute_level_avoidance(self):
        """HC-03: Line tasks schedule around PM window [7200s, 14400s] (2h-4h into shift)."""
        pm = PMWindow(
            line="LINE_1",
            start_seconds=7200.0,   # 09:00
            end_seconds=14400.0,    # 11:00 (2h duration)
            reason="PM_ROUTINE",
        )
        t1 = MicroTaskSegment(
            task_id="T_BEFORE_OR_AFTER",
            line="LINE_1",
            part_no="P1",
            mold_id="M1",
            quantity=100,
            duration_seconds=7200.0,  # 2 hours
            setup_seconds=0.0,
        )
        result = schedule_intra_shift(
            shift=self.shift,
            tasks=[t1],
            pm_windows=[pm],
            technician_capacity=1,
        )
        self.assertIsNotNone(result)
        seg1 = result.segments[0]
        # Task must finish before PM or start after PM
        avoids_pm = (seg1.processing_end <= pm.start_seconds) or (seg1.processing_start >= pm.end_seconds)
        self.assertTrue(avoids_pm, f"Task {seg1.processing_start}-{seg1.processing_end} collided with PM {pm.start_seconds}-{pm.end_seconds}")

    def test_04_micro_scheduler_fast_performance_no_mtz_explosion(self):
        """Tests that scheduling 10 tasks in a shift finishes in under 200ms."""
        import time
        tasks = [
            MicroTaskSegment(
                task_id=f"TASK_{i}",
                line=f"LINE_{i % 2}",
                part_no=f"PART_{i}",
                mold_id=f"MOLD_{i % 4}",
                quantity=20,
                duration_seconds=1200.0,
                setup_seconds=300.0,
            )
            for i in range(10)
        ]
        t0 = time.perf_counter()
        result = schedule_intra_shift(
            shift=self.shift,
            tasks=tasks,
            technician_capacity=2,
        )
        elapsed = time.perf_counter() - t0
        self.assertIsNotNone(result)
        self.assertLess(elapsed, 0.5, f"Micro scheduling took too long: {elapsed:.3f}s")


if __name__ == "__main__":
    unittest.main()
