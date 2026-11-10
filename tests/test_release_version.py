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
        self.assertEqual(len(found), 5)
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


def test_set_version_updates_module_and_its_shared_dependency(tmp_path, monkeypatch):
    root = tmp_path / "release"
    module = root / ".dagger"
    module.mkdir(parents=True)
    for project, name in ((root, "engineering-ci"), (module, "engineering-ci-module")):
        (project / "pyproject.toml").write_text(
            (f'[project]\\nname = "{name}"\\nversion = "0.7.0"\\n'
             + ('dependencies = ["engineering-ci"]\\n'
                '[tool.uv.sources]\\nengineering-ci = { path = ".." }\\n'
                if project == module else "")),
            encoding="utf-8",
        )
        packages = [name] if project == root else ["engineering-ci", name]
        lock_text = "\n".join(
            f'[[package]]\\nname = "{package}"\\nversion = "0.7.0"\\n'
            + ('source = { directory = "../" }\\n'
               if project == module and package == "engineering-ci" else "")
            for package in packages
        )
        (project / "uv.lock").write_text(lock_text, encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", root)
    monkeypatch.setattr(mod, "PROJECTS", (root, module))
    monkeypatch.setattr(
        mod, "PROJECT_NAMES", {root: "engineering-ci", module: "engineering-ci-module"}
    )
    mod.set_version("v0.8.0")
    assert set(mod.versions().values()) == {"0.8.0"}
    lock = module / "uv.lock"
    lock.write_text(
        lock.read_text(encoding="utf-8").replace(
            'name = "engineering-ci"\nversion = "0.8.0"',
            'name = "engineering-ci"\nversion = "0.7.0"',
        ),
        encoding="utf-8",
    )
    with unittest.TestCase().assertRaisesRegex(ValueError, "Release version mismatch"):
        mod.checked_version()


if __name__ == "__main__":
    unittest.main()
