"""Unit tests for consumer SHA synchronization (no network, no Dagger required)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from engineering_ci.tools import consumer as sync

UPSTREAM = "dymzz/engineering-ci"
OLD_SHA = "a" * 40
NEW_SHA = "b" * 40
WORKFLOW = (
    "jobs:\n  python:\n    uses: "
    "dymzz/engineering-ci/.github/workflows/reusable-python-ci.yml@"
    + OLD_SHA
    + "\n    secrets:\n      ENGINEERING_CI_READ_TOKEN: ${{ secrets.ENGINEERING_CI_READ_TOKEN }}\n"
)
MANIFEST = json.dumps(
    {
        "name": "smoke",
        "dependencies": [
            {
                "name": "engineering-ci",
                "source": "github.com/dymzz/engineering-ci@" + OLD_SHA,
                "pin": OLD_SHA,
            },
            {
                "name": "other",
                "source": "github.com/example/other@" + OLD_SHA,
                "pin": OLD_SHA,
            },
        ],
    }
)


class VersionSyncTests(unittest.TestCase):
    def test_both_refs_and_other_dependencies_preserved(self):
        wf, man, notes = sync.update_references(WORKFLOW, MANIFEST, UPSTREAM, NEW_SHA)
        self.assertIn("reusable-python-ci.yml@" + NEW_SHA, wf)
        self.assertIn("${{ secrets.ENGINEERING_CI_READ_TOKEN }}", wf)
        deps = json.loads(man)["dependencies"]
        self.assertEqual(
            deps[0]["source"], "github.com/dymzz/engineering-ci@" + NEW_SHA
        )
        self.assertEqual(deps[0]["pin"], NEW_SHA)
        self.assertEqual(deps[1]["pin"], OLD_SHA)
        self.assertEqual(len(notes), 2)

    def test_idempotent(self):
        wf, man, _ = sync.update_references(WORKFLOW, MANIFEST, UPSTREAM, OLD_SHA)
        self.assertEqual(wf, WORKFLOW)
        self.assertEqual(json.loads(man), json.loads(MANIFEST))

    def test_wrong_source_rejected(self):
        bad = MANIFEST.replace(
            "github.com/dymzz/engineering-ci@", "github.com/other/engineering-ci@"
        )
        with self.assertRaises(sync.SyncError):
            sync.update_references(WORKFLOW, bad, UPSTREAM, NEW_SHA)

    def test_missing_and_ambiguous_workflow_rejected(self):
        for value in ["name: empty\n", WORKFLOW + WORKFLOW]:
            with self.subTest(value=value), self.assertRaises(sync.SyncError):
                sync.update_references(value, MANIFEST, UPSTREAM, NEW_SHA)

    def test_missing_dependency_rejected(self):
        bad = MANIFEST.replace('"name": "engineering-ci"', '"name": "missing"')
        with self.assertRaises(sync.SyncError):
            sync.update_references(WORKFLOW, bad, UPSTREAM, NEW_SHA)

    def test_invalid_sha_rejected(self):
        for value in ["main", "1234", "z" * 40, "A" * 40]:
            with self.subTest(value=value), self.assertRaises(sync.SyncError):
                sync.update_references(WORKFLOW, MANIFEST, UPSTREAM, value)

    def test_git_root_normalizes_path_and_resolves(self):
        original = sync.git
        root = Path(__file__).resolve().parent.parent
        try:
            sync.git = lambda repo, *args: root.as_posix()
            self.assertEqual(sync.git_root(root), root)
        finally:
            sync.git = original

    def test_missing_remote_branch_rejected(self):
        original = sync.git
        try:
            sync.git = lambda repo, *args: ""
            with self.assertRaises(sync.SyncError):
                sync.remote_branch_sha(Path("/tmp"), "main")
        finally:
            sync.git = original


if __name__ == "__main__":
    unittest.main()
