"""Unified test runner for APS.

Discovers suites under both ``aps_tests/`` (new architecture) and ``tests/``
(legacy, retired at Stage F2).

Usage:
    python run_tests.py             # both directories
    python run_tests.py --fast      # modules declaring FAST = True
    python run_tests.py --new       # aps_tests/ only
    python run_tests.py --legacy    # tests/ only
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

NEW_DIR = "aps_tests"
LEGACY_DIR = "tests"


def _discover(pkg: str) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    path = os.path.join(BASE_DIR, pkg)
    if not os.path.isdir(path):
        return unittest.TestSuite()
    return loader.discover(start_dir=path, pattern="test_*.py", top_level_dir=BASE_DIR)


def _is_fast(suite: unittest.TestSuite) -> bool:
    for test in suite:
        module = importlib.import_module(test.__class__.__module__)
        return bool(getattr(module, "FAST", False))
    return False


def _filter_fast(suite: unittest.TestSuite) -> unittest.TestSuite:
    kept = unittest.TestSuite()
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            kept.addTests(_filter_fast(test))
        else:
            module = importlib.import_module(test.__class__.__module__)
            if getattr(module, "FAST", False):
                kept.addTest(test)
    return kept


def main() -> None:
    parser = argparse.ArgumentParser(description="APS test runner")
    parser.add_argument("--fast", action="store_true", help="modules declaring FAST = True")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--new", action="store_true", help="aps_tests/ only")
    group.add_argument("--legacy", action="store_true", help="tests/ only")
    args = parser.parse_args()

    pkgs = []
    if not args.legacy:
        pkgs.append(NEW_DIR)
    if not args.new:
        pkgs.append(LEGACY_DIR)

    suite = unittest.TestSuite()
    for pkg in pkgs:
        suite.addTests(_discover(pkg))

    if args.fast:
        suite = _filter_fast(suite)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
