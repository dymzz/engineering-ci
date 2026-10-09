"""Pure unit tests of the Dagger pytest-report parser (no Dagger dependency)."""

import importlib.util
import os
import subprocess
import sys
import tempfile
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
    def test_conservative_assertion_evidence(self):
        self.assertEqual(report._diagnosis("failure", "", "", "builtins.AssertionError"),
                         "assertion_failure")
        self.assertEqual(report._diagnosis("failure", "assert 1 == 2", ""), "assertion_failure")
        self.assertEqual(report._diagnosis("failure", "", "E   assert 1 == 2"), "assertion_failure")
        self.assertEqual(report._diagnosis("failure", "", "AssertionError: explicit"), "assertion_failure")
        for message, trace in (
            ("ValueError: assert statement failed", "    assert 1 == risky_call()\nE   ValueError: assert statement failed"),
            ("RuntimeError: AssertionError mentioned", "E   RuntimeError: AssertionError mentioned"),
            ("assertion failed", "source contains assert"),
            ("assert_value mismatch", "    assert value"),
        ):
            with self.subTest(message=message):
                self.assertEqual(report._diagnosis("failure", message, trace), "test_failure")

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


class RealPytestOutputTests(unittest.TestCase):
    """Generate JUnit with a real child pytest, without importing Dagger."""

    def run_pytest(self, source):
        with tempfile.TemporaryDirectory(prefix="engineering-ci-pytest-") as directory:
            root = Path(directory)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_sample.py").write_text(source, encoding="utf-8")
            xml = root / "report.xml"
            env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
            for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH"):
                env.pop(key, None)
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--tb=short",
                 f"--junitxml={xml}", "tests"],
                cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertTrue(xml.is_file(), result.stdout + result.stderr)
            return xml.read_text(encoding="utf-8"), result.returncode, result.stdout + result.stderr

    def test_real_success(self):
        data = report.summarize_pytest(*self.run_pytest("def test_ok():\n    assert 1 == 1\n"))
        self.assertEqual(data["exit_code"], 0)
        self.assertEqual(data["status"], "passed")
        self.assertTrue(data["report_available"])
        self.assertEqual(data["counts"]["passed"], 1)

    def test_real_assert_failures(self):
        for source in (
            "def test_bad():\n    assert 1 == 2\n",
            "def test_bad():\n    assert False, 'explicit message'\n",
            "def test_bad():\n    raise AssertionError('explicit exception')\n",
        ):
            with self.subTest(source=source):
                xml, code, output = self.run_pytest(source)
                data = report.summarize_pytest(xml, code, "console result deliberately omitted")
                self.assertEqual(code, 1, output)
                self.assertEqual(data["status"], "failed")
                self.assertEqual(data["counts"]["failures"], 1)
                self.assertEqual(data["failures"][0]["category"], "assertion_failure")
                self.assertIn("test_bad", report.failure_summary(data))

    def test_real_non_assertion_exception(self):
        source = ("def dangerous():\n    raise ValueError('assert and AssertionError are text')\n"
                  "def test_bad():\n    assert 1 == dangerous()\n")
        data = report.summarize_pytest(*self.run_pytest(source))
        self.assertEqual(data["exit_code"], 1)
        self.assertEqual(data["failures"][0]["category"], "test_failure")

    def test_real_setup_and_teardown_errors(self):
        for phase, fixture in (
            ("setup", "    raise ValueError('setup failed')\n"),
            ("teardown", "    yield 1\n    raise ValueError('teardown failed')\n"),
        ):
            with self.subTest(phase=phase):
                source = "import pytest\n@pytest.fixture\ndef resource():\n" + fixture
                source += "def test_fixture(resource):\n    assert resource == 1\n"
                data = report.summarize_pytest(*self.run_pytest(source))
                self.assertEqual(data["exit_code"], 1)
                self.assertEqual(data["counts"]["errors"], 1)
                self.assertEqual(data["failures"][0]["category"], "setup_teardown_error")

    def test_real_collection_error(self):
        data = report.summarize_pytest(*self.run_pytest("raise RuntimeError('collection failed')\n"))
        self.assertEqual(data["exit_code"], 2)
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["counts"]["errors"], 1)
        self.assertEqual(data["failures"][0]["category"], "collection_error")

    def test_real_no_tests(self):
        data = report.summarize_pytest(*self.run_pytest("def helper():\n    pass\n"))
        self.assertEqual(data["exit_code"], 5)
        self.assertEqual(data["reason"], "no_tests_collected")
        self.assertNotEqual(data["status"], "passed")

    def test_real_xml_missing_or_corrupt(self):
        xml, code, output = self.run_pytest("def test_ok():\n    pass\n")
        for unavailable in ("", xml[:len(xml) // 2]):
            with self.subTest(xml=unavailable):
                data = report.summarize_pytest(unavailable, code, output)
                self.assertFalse(data["report_available"])
                self.assertEqual(data["status"], "error")
                self.assertIn("diagnostic", data)

    def test_real_multiple_failures_and_bounds(self):
        source = "\n".join(f"def test_bad_{i}():\n    assert 1 == 2\n" for i in range(3))
        data = report.summarize_pytest(*self.run_pytest(source), max_failures=2)
        self.assertEqual(data["exit_code"], 1)
        self.assertEqual(data["counts"]["failures"], 3)
        self.assertEqual(len(data["failures"]), 2)
        self.assertEqual(data["omitted_failures"], 1)
        self.assertEqual({item["category"] for item in data["failures"]}, {"assertion_failure"})
        self.assertIn("1 additional failures omitted", report.failure_summary(data))


if __name__ == "__main__":
    unittest.main()
