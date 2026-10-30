"""Unified test runner for APS MVP.

Discovers and runs all test suites under the `tests/` directory.

Usage:
    python run_tests.py           # Run all unit and integration tests
    python run_tests.py --fast    # Run fast unit tests (skip heavy multi-minute solvers)
"""
import argparse
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def main():
    parser = argparse.ArgumentParser(description="APS MVP Test Runner")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Run only fast unit tests (kernel, audit mutations, rule_sets, labor, domain, split_batch)",
    )
    args = parser.parse_args()

    tests_dir = os.path.join(BASE_DIR, "tests")
    loader = unittest.TestLoader()

    if args.fast:
        fast_modules = [
            "tests.test_domain",
            "tests.test_kernel",
            "tests.test_rule_sets",
            "tests.test_labor",
            "tests.test_split_batch",
            "tests.test_independent_audit",
            "tests.test_capacity_kernel",
            "tests.test_operator_wiring",
            "tests.test_micro_scheduler",
            "tests.test_regressions",
            "tests.test_rule_catalog",
            "tests.test_rule_mapping_and_validator",
            "tests.test_scenario_repository",
            "tests.test_scenario_ingestion",
            "tests.test_scenario_builder",
            "tests.test_scenario_api",
            "tests.test_telemetry",
        ]
        suite = unittest.TestSuite()
        for mod in fast_modules:
            suite.addTests(loader.loadTestsFromName(mod))
    else:
        suite = loader.discover(start_dir=tests_dir, pattern="test_*.py")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
