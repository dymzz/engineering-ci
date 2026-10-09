"""Delivery/gate contract tests use stdlib and real child pytest, no Dagger."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci = load("pytest_ci_unit", ROOT / ".dagger/src/engineering_ci/pytest_ci.py")
parser = load("pytest_report_delivery_unit", ROOT / ".dagger/src/engineering_ci/pytest_report.py")


class PytestDeliveryTests(unittest.TestCase):
    def bundle(self, directory, data, code):
        (directory / "exit-code.txt").write_text(str(code) + "\n", encoding="ascii")
        (directory / "report.json").write_text(json.dumps(data), encoding="utf-8")
        (directory / "summary.md").write_text(parser.markdown_summary(data), encoding="utf-8")

    def test_gate_uses_only_original_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            data = parser.summarize_pytest('<testsuite tests="1"/>', 0, "")
            self.bundle(directory, data, 1)  # false JSON cannot turn the execution green
            self.assertEqual(ci.main(["gate", tmp]), 1)
            with self.assertRaisesRegex(ValueError, "does not match"):
                ci.publish_summary(directory, directory / "job-summary")
            (directory / "report.json").unlink()  # even missing JSON cannot hide exit 1
            self.assertEqual(ci.main(["gate", tmp]), 1)

    def test_report_unavailable_is_separate_from_pytest_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            data = parser.summarize_pytest("", 0, "pytest passed")
            self.bundle(directory, data, 0)
            self.assertEqual(ci.main(["gate", tmp]), 0)
            summary = directory / "job-summary"
            with self.assertRaisesRegex(ValueError, "unavailable"):
                ci.publish_summary(directory, summary)
            self.assertIn("Test execution: **PASSED**; report status: **ERROR**", summary.read_text(encoding="utf-8"))
            self.assertEqual(ci.execution_exit_code(directory), 0)

    def test_missing_or_invalid_execution_is_never_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ci.main(["gate", tmp]), 1)
            for value in ("-1", "256", "0 extra", "zero"):
                (Path(tmp) / "exit-code.txt").write_text(value)
                self.assertEqual(ci.main(["gate", tmp]), 1)

    def test_remaining_checks_preserve_every_other_check(self):
        listing = "Name Description\napp:test Test suite\napp:lint Ruff\napp:format Formatting\n\nGenerators\napp:generate Generated files\n"
        self.assertEqual(ci.remaining_checks(listing, "app:test"),
                         ["app:lint", "app:format", "app:generate"])
        with self.assertRaises(ValueError):
            ci.remaining_checks(listing, "missing:test")
        self.assertEqual(ci.remaining_checks("app:test Tests", "app:test"), [])

    def test_bounded_xml_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.xml"
            self.assertIn("did not produce", ci.read_xml(path)["diagnostic"])
            path.write_bytes(b"\xff")
            self.assertIn("UnicodeDecodeError", ci.read_xml(path)["diagnostic"])
            path.write_bytes(b"x" * (ci.MAX_XML_BYTES + 1))
            self.assertIn("limit", ci.read_xml(path)["diagnostic"])
            path.write_text("<testsuite/>")
            self.assertIsNone(ci.read_xml(path)["diagnostic"])

    def test_summary_escaping_and_byte_limit(self):
        data = parser.summarize_pytest("", 1, "")
        data["counts"] = {"tests": 51, "passed": 0, "failures": 51, "errors": 0, "skipped": 0}
        data["failures"] = [{"test": "<script>|`\n# forged" * 30, "category": "assertion_failure",
                             "message": "<img>|`\n" * 90} for _ in range(50)]
        data["omitted_failures"] = 1
        text = parser.markdown_summary(data)
        self.assertNotIn("<script>", text)
        self.assertNotIn("<img>", text)
        self.assertNotIn("\n# forged", text)
        self.assertIn("&#124;", text)
        self.assertIn("additional failures omitted", text)
        self.assertLess(len(text.encode("utf-8")), ci.MAX_SUMMARY_BYTES)

    def test_real_pytest_to_files_summary_and_gate(self):
        # Reuse the existing real-pytest fixture generator, not fabricated XML.
        source_tests = load("pytest_source_tests", ROOT / "tests/test_pytest_report.py")
        fixture = source_tests.RealPytestOutputTests()
        for source, expected in (("def test_ok():\n    assert True\n", 0),
                                 ("def test_bad():\n    assert 1 == 2\n", 1)):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                xml, code, output = fixture.run_pytest(source)
                data = parser.summarize_pytest(xml, code, output)
                directory = Path(tmp)
                self.bundle(directory, data, code)
                ci.publish_summary(directory, directory / "job-summary")
                self.assertEqual(ci.main(["gate", tmp]), expected)
                self.assertIn("Original pytest exit code", (directory / "job-summary").read_text(encoding="utf-8"))
                if expected:
                    self.assertEqual(data["failures"][0]["category"], "assertion_failure")


if __name__ == "__main__":
    unittest.main()
