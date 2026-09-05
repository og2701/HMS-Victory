"""Run every Skyrim check with disposable state; never start or connect the bot.

Usage: python scripts/test_skyrim_isolated.py [filename-or-test-substring]
Supports the historical plain test functions and new unittest cases without
requiring pytest. The desktop bundled interpreter supplies Pillow and pytz.
"""
from __future__ import annotations

import inspect
import os
from pathlib import Path
import runpy
import sys
import tempfile
import traceback
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Optional local dependency location, used only when discord isn't installed in
# the selected runtime. Ordinary project/CI interpreters need no path override.
try:
    import discord  # noqa: F401
except ImportError:
    local_packages = Path.home() / ".local/share/uv/python/cpython-3.12.13-macos-aarch64-none/lib/python3.12/site-packages"
    if local_packages.is_dir():
        sys.path.append(str(local_packages))

import config


def main() -> int:
    wanted = sys.argv[1:]
    plain_passed = 0
    failures = []
    suite = unittest.TestSuite()
    with tempfile.TemporaryDirectory(prefix="skyrim_suite_") as folder:
        for key in dir(config):
            if key.startswith("SKYRIM_") and key.endswith("_FILE") or key == "PERSISTENT_VIEWS_FILE":
                setattr(config, key, str(Path(folder) / (key.lower() + ".json")))
        for path in sorted((ROOT / "tests").glob("test_skyrim*.py")):
            if wanted and not any(w in path.name for w in wanted):
                # A test-name selection must still load its enclosing file.
                if not any(w in path.read_text() for w in wanted):
                    continue
            try:
                namespace = runpy.run_path(str(path), run_name=f"isolated_{path.stem}")
            except Exception:
                failures.append(f"{path.name}: import")
                traceback.print_exc()
                continue
            for name, item in namespace.items():
                selected = not wanted or any(w in path.name or w in name for w in wanted)
                if inspect.isfunction(item) and name.startswith("test_") and selected:
                    try:
                        item()
                        plain_passed += 1
                    except Exception:
                        failures.append(f"{path.name}:{name}")
                        traceback.print_exc()
                elif inspect.isclass(item) and issubclass(item, unittest.TestCase) and item is not unittest.TestCase:
                    for method in unittest.defaultTestLoader.getTestCaseNames(item):
                        if selected or any(w in method for w in wanted):
                            suite.addTest(item(method))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        print(f"Skyrim: {plain_passed} plain checks passed; {len(failures)} failed; "
              f"{result.testsRun} unittest checks; "
              f"{len(result.failures) + len(result.errors)} unittest failures/errors.")
        for failure in failures:
            print(f"FAIL {failure}")
        return 0 if not failures and result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
