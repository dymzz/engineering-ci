"""Pure unit tests of the Dagger pytest-report parser (no Dagger dependency)."""

import importlib.util
import sys
import unittest
from pathlib import Path

PARSER = (
    Path(__file__).resolve().parent.parent
    / ".dagger" / "src" / "engineering_ci" / "pytest_report.py"
)
spec = importlib.util.spec_from_file_location("pytest_report_unit", PARSER)
assert spec is not None and spec.loader is not None
report = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = report
spec.loader.exec_module(report)


class PytestReportTests(unittest.TestCase):
    def test_passing_suite(self):
        xml = '<testsuites><testsuite tests="2" failures="0" errors="0" skipped="1"><testcase classname="a" name="ok"/><testcase classname="a" name="skip"><skipped/></testcase></testsuite></testsuites>'
        data = report.summarize_pytest(xml, 0, "1 passed 1 skipped")
        self.assertEqual(data["status"], "passed")
        self.assertEqual(data["counts"]["passed"], 1)
        self.assertEqual(data["counts"]["skipped"], 1)
        self.assertEqual(data["failures"], [])

    def test_assertion_failure(self):
        xml = '<testsuite tests="1" failures="1" errors="0" skipped="0"><testcase classname="suite" name="test_no"><failure message="AssertionError">assert 1 == 2\nAssertionError</failure></testcase></testsuite>'
        data = report.summarize_pytest(xml, 1, "FAILED")
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["failures"][0]["category"], "assertion_failure")
        self.assertIn("suite::test_no", report.failure_summary(data))

    def test_collection_error(self):
        xml = '<testsuite tests="1" failures="0" errors="1" skipped="0"><testcase classname="" name="tests/test_bad.py"><error message="collection failure">SyntaxError: invalid syntax</error></testcase></testsuite>'
        data = report.summarize_pytest(xml, 2, "collection failed")
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["reason"], "interrupted")
        self.assertEqual(data["failures"][0]["category"], "collection_error")

    def test_missing_and_invalid_xml(self):
        for xml in ("", "<html/>", "<testsuite"):
            with self.subTest(xml=xml):
                data = report.summarize_pytest(xml, 0, "pytest crashed")
                self.assertEqual(data["status"], "error")
                self.assertFalse(data["report_available"])

    def test_no_tests(self):
        data = report.summarize_pytest(
            '<testsuite tests="0" failures="0" errors="0" skipped="0"/>',
            5,
            "no tests ran",
        )
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["reason"], "no_tests_collected")

    def test_bounds(self):
        xml = '<testsuite tests="2" failures="2" errors="0" skipped="0">' + "".join(
            f'<testcase classname="a" name="f{i}"><failure message="x">{"z" * 3000}</failure></testcase>'
            for i in range(2)
        ) + '</testsuite>'
        data = report.summarize_pytest(xml, 1, "", max_failures=1)
        self.assertEqual(len(data["failures"]), 1)
        self.assertEqual(data["omitted_failures"], 1)
        self.assertLessEqual(len(data["failures"][0]["trace_excerpt"]), 1613)
        with self.assertRaises(ValueError):
            report.summarize_pytest(xml, 1, "", max_failures=0)


if __name__ == "__main__":
    unittest.main()
