"""Workflow YAML must parse structurally, and must not repeat a key.

A duplicate key under one mapping makes the whole Workflow file invalid, and
GitHub then fails the run with no jobs at all and no actionable log. That
failure mode is silent from inside this repository: nothing in the test suite
or the lint checks reads these files.

This module deliberately uses no YAML dependency. It tracks indentation and
key names, which is enough to catch a repeated key at one level and is immune
to the version differences a real parser brings.
"""

import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github/workflows"

_KEY = re.compile(
    r"^(?P<indent> *)(?P<list>-\s+)?(?P<key>[A-Za-z_][\w.-]*)\s*:(?P<value>.*)$"
)
_JOBS = re.compile(r"^jobs:\s*$", re.MULTILINE)


def duplicate_keys(text: str) -> list[tuple[int, str]]:
    """Return (line number, key) for every key repeated at one level."""
    found: list[tuple[int, str]] = []
    # One entry per open level: (indent, keys already seen at that indent).
    levels: list[tuple[int, set[str]]] = []
    block_indent: int | None = None

    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        indent = len(raw) - len(raw.lstrip(" "))

        if block_indent is not None:
            # Literal block content belongs to the key above it.
            if indent > block_indent:
                continue
            block_indent = None
        if stripped.startswith("#"):
            continue

        match = _KEY.match(raw)
        if not match:
            continue
        key = match.group("key")
        is_item = bool(match.group("list"))

        while levels and levels[-1][0] > indent:
            levels.pop()
        if levels and levels[-1][0] == indent:
            seen = levels[-1][1]
        else:
            seen = set()
            levels.append((indent, seen))

        if is_item:
            # Each list item is its own scope, so its key is never a repeat.
            seen = set()
            levels[-1] = (indent, seen)
        elif key in seen:
            found.append((number, key))
        else:
            seen.add(key)

        value = raw[match.end() :].strip()
        if value[:1] in {"|", ">"}:
            block_indent = indent
    return found


class WorkflowStructureTests(unittest.TestCase):
    def setUp(self):
        self.files = sorted(WORKFLOWS.glob("*.yml"))
        if not self.files:
            self.skipTest("no workflow files found")

    def test_repository_has_workflows(self):
        self.assertTrue(self.files)

    def test_no_workflow_repeats_a_key_at_one_level(self):
        for path in self.files:
            with self.subTest(workflow=path.name):
                duplicates = duplicate_keys(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    duplicates,
                    [],
                    f"{path.name} repeats a key at one level; a duplicate mapping "
                    "key makes the whole workflow invalid",
                )

    def test_indentation_is_consistent(self):
        for path in self.files:
            with self.subTest(workflow=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("\t", text, f"{path.name} contains a tab")
                for number, line in enumerate(text.splitlines(), start=1):
                    if not line.strip() or line.lstrip().startswith("#"):
                        continue
                    leading = len(line) - len(line.lstrip(" "))
                    self.assertEqual(
                        leading % 2,
                        0,
                        f"{path.name}:{number} indents by an odd number of spaces",
                    )

    def test_every_workflow_declares_jobs(self):
        for path in self.files:
            with self.subTest(workflow=path.name):
                self.assertTrue(
                    _JOBS.search(path.read_text(encoding="utf-8")),
                    f"{path.name} declares no jobs mapping",
                )

    def test_the_detector_actually_catches_a_repeat(self):
        self.assertEqual(
            duplicate_keys("workflow_call:\n  inputs:\n    a: 1\n    a: 2\n"),
            [(4, "a")],
        )

    def test_the_detector_ignores_list_items_and_block_scalars(self):
        text = (
            "jobs:\n"
            "  build:\n"
            "    steps:\n"
            "      - name: one\n"
            "        run: |\n"
            "          echo a:\n"
            "          name: not-a-key\n"
            "      - name: two\n"
        )
        self.assertEqual(duplicate_keys(text), [])


if __name__ == "__main__":
    unittest.main()
