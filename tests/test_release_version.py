"""Tests for the release version contract (no uv/network required)."""

import unittest
from unittest.mock import patch

from engineering_ci.release import version as mod


class ReleaseVersionTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(mod.normalize("v0.4.2"), "0.4.2")
        self.assertEqual(mod.normalize("0.4.2"), "0.4.2")
        for value in ("v0.4", "main", "1.2.3-beta", "v1.2.3\nother"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                mod.normalize(value)

    def test_repository_versions_consistent(self):
        self.assertEqual(mod.checked_version(), "0.4.10")

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
