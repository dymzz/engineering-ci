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
        found = mod.versions()
        self.assertEqual(len(found), 4)
        self.assertEqual(set(found.values()), {mod.checked_version()})
        self.assertEqual(mod.normalize(mod.checked_version()), mod.checked_version())

    def test_consistent_metadata_accepts_future_versions(self):
        for version in ("0.4.11", "0.4.12", "1.2.3"):
            with (
                self.subTest(version=version),
                patch.object(
                    mod, "versions", return_value={"project": version, "lock": version}
                ),
            ):
                self.assertEqual(mod.checked_version(), version)

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
