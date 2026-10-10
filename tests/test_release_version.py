"""Tests for the release version contract (no uv/network required)."""

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parent.parent / "scripts/release_version.py"
spec = importlib.util.spec_from_file_location("release_version", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class ReleaseVersionTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(mod.normalize("v0.4.2"), "0.4.2")
        self.assertEqual(mod.normalize("0.4.2"), "0.4.2")
        for value in ("v0.4", "main", "1.2.3-beta", "v1.2.3\nother"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                mod.normalize(value)

    def test_repository_versions_consistent(self):
        self.assertEqual(mod.checked_version(), "0.4.8")

    def test_mismatch_detected(self):
        with (
            patch.object(
                mod,
                "versions",
                return_value={
                    "./pyproject.toml": "0.4.2",
                    "./uv.lock": "0.4.1",
                },
            ),
            self.assertRaises(ValueError),
        ):
            mod.checked_version()


if __name__ == "__main__":
    unittest.main()
