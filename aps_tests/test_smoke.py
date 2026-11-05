from __future__ import annotations

import importlib
import unittest

FAST = True


class TestPackageScaffold(unittest.TestCase):
    def test_aps_package_importable(self):
        mod = importlib.import_module("aps")
        self.assertTrue(hasattr(mod, "__version__"))

    def test_subpackages_importable(self):
        for name in ("aps.schema", "aps.data", "aps.calendar"):
            with self.subTest(pkg=name):
                importlib.import_module(name)


if __name__ == "__main__":
    unittest.main()
