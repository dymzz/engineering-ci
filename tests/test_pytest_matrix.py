"""Matrix policy and delivery tests independent of the Dagger Engine."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MODULES = Path(__file__).resolve().parent.parent / ".dagger/src/engineering_ci"
sys.path.insert(0, str(MODULES))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, MODULES / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


matrix = load("pytest_matrix_unit", "pytest_matrix.py")
reporting = load("pytest_matrix_reporting_unit", "pytest_report.py")
quality = load("pytest_matrix_quality_unit", "pytest_quality.py")
runtime_module = load("pytest_matrix_runtime_unit", "pytest_matrix_runtime.py")


def runtime(version="3.14", code=0):
    return {"schema_version": 1, "requested_python": version,
            "actual_python": version + ".7", "pytest_started": True,
            "pytest_exit_code": code, "executable": "/workspace/.venv/bin/python",
            "prefix": "/workspace/.venv", "base_prefix": "/usr/local"}


XML = '<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="test_ok"/></testsuite>'
FAILED_XML = ('<testsuite tests="1" failures="1" errors="0" skipped="0">'
              '<testcase name="test_bad"><failure message="assert 1 == 2">E   assert 1 == 2</failure>'
              '</testcase></testsuite>')


class MatrixPolicyTests(unittest.TestCase):
    def test_invalid_versions_duplicates_and_bounds(self):
        for versions in ([], "3.12", ["3.12", "3.12"], ["3.12.1"], ["3.9"],
                         ["3.15"], ["/tmp"], [312], [True]):
            with self.subTest(versions=versions), self.assertRaises(ValueError):
                matrix.matrix_policy(versions)
        for concurrency in (0, 4, True, "2"):
            with self.subTest(concurrency=concurrency), self.assertRaises(ValueError):
                matrix.matrix_policy(["3.12"], concurrency)
        for limit in (0, 51, True):
            with self.assertRaises(ValueError):
                matrix.matrix_policy(["3.12"], max_failures=limit)

    def test_defaults_order_and_existing_supported_versions(self):
        self.assertEqual(matrix.matrix_policy(["3.14", "3.12"])["requested_versions"], ["3.14", "3.12"])
        self.assertEqual(matrix.matrix_policy(["3.12"])["max_concurrency"], 1)
        self.assertEqual(len(matrix.matrix_policy(list(matrix.SUPPORTED_PYTHONS))["requested_versions"]), 5)

    def test_mature_requires_python_patch_and_exclusions(self):
        for spec, actual, supported in ((">=3.12", "3.12.7", True), (">=3.14", "3.13.8", False),
                                       (">=3.12.5,<3.14,!=3.13.*", "3.12.7", True),
                                       (">=3.12.5", "3.12.4", False),
                                       (">=3.12,!=3.13.*", "3.13.7", False), ("", "3.14.1", True)):
            with self.subTest(spec=spec, actual=actual):
                self.assertEqual(matrix.supports_python(spec, actual), supported)
        with self.assertRaises(ValueError):
            matrix.supports_python("not a constraint", "3.14.1")

    def test_runtime_mismatch_missing_and_exit_disagreement(self):
        for value, expected in (({}, "runtime_unavailable"), (runtime("3.13"), "interpreter_mismatch"),
                                (runtime(code=1), "runtime_unavailable")):
            self.assertEqual(matrix.runtime_problem(value, "3.14", 0), expected)
        evidence = runtime()
        evidence["pytest_started"] = False
        self.assertEqual(matrix.runtime_problem(evidence, "3.14", 0), "runtime_unavailable")


class MatrixResultTests(unittest.TestCase):
    def row(self, version="3.14", code=0, xml=XML):
        return matrix.version_result(version, {"phase": "pytest", "process_exit_code": code},
                                     runtime(version, code), reporting.summarize_pytest(xml, code, ""))

    def test_all_required_and_missing_result(self):
        plan = matrix.matrix_policy(["3.12", "3.14"])
        self.assertEqual(matrix.aggregate(plan, [self.row("3.14"), self.row("3.12")])["overall_status"], "passed")
        incomplete = matrix.aggregate(plan, [self.row("3.12")])
        self.assertEqual(incomplete["overall_status"], "error")
        self.assertEqual(incomplete["versions"][1]["failure_category"], "report_unavailable")
        with self.assertRaises(ValueError):
            matrix.aggregate(plan, [self.row("3.12"), self.row("3.12")])

    def test_original_pytest_exit_cannot_be_hidden_by_missing_report(self):
        for code in (1, 2, 3, 4, 5):
            row = self.row(code=code, xml="")
            self.assertEqual(row["pytest_exit_code"], code)
            self.assertEqual(row["status"], "failed")
        self.assertEqual(self.row(xml="")["failure_category"], "report_unavailable")
        self.assertEqual(self.row(xml="<broken")["failure_category"], "report_unavailable")

    def test_preflight_errors_are_not_pytest_failures(self):
        for category in ("python_unsupported", "dependency_installation", "environment_initialization"):
            row = matrix.version_result("3.12", {"phase": "preflight", "failure_category": category})
            self.assertEqual(row["failure_category"], category)
            self.assertIsNone(row["pytest_exit_code"])
            self.assertIsNone(row["pytest"])

    def test_collection_and_assertion_classification(self):
        xml = '<testsuite tests="1" errors="1"><testcase name="collect"><error message="SyntaxError"/></testcase></testsuite>'
        self.assertEqual(self.row(code=2, xml=xml)["failure_category"], "collection_error")
        row = self.row(code=1, xml=FAILED_XML)
        self.assertEqual(row["failure_category"], "pytest_failure")
        self.assertEqual(row["pytest"]["failures"][0]["category"], "assertion_failure")

    def test_coverage_failure_is_independent_of_pytest_exit(self):
        report = reporting.summarize_pytest(XML, 0, "")
        policy = quality.test_policy(coverage_source="sample", min_coverage="80")
        report["quality"] = quality.evaluate_quality(policy, 0, {
            "xml": '<coverage lines-covered="3" lines-valid="4"/>',
            "json": json.dumps({"totals": {"covered_lines": 3, "num_statements": 4}}), "diagnostic": None})
        row = matrix.version_result("3.14", {"phase": "pytest", "process_exit_code": 0}, runtime(), report)
        self.assertEqual(row["pytest_exit_code"], 0)
        self.assertEqual(row["failure_category"], "coverage_failure")
        self.assertEqual(row["status"], "failed")

    def test_conflicting_junit_failure_with_zero_exit_blocks(self):
        row = self.row(code=0, xml=FAILED_XML)
        self.assertEqual(row["pytest_exit_code"], 0)
        self.assertEqual(row["failure_category"], "report_unavailable")

    def test_failure_truncation_retains_omitted_count(self):
        xml = '<testsuite tests="4" failures="4">' + ''.join(
            f'<testcase name="test_{i}"><failure message="assert False"/></testcase>' for i in range(4)) + '</testsuite>'
        report = reporting.summarize_pytest(xml, 1, "", max_failures=2)
        self.assertEqual(len(report["failures"]), 2)
        self.assertEqual(report["counts"]["failures"], 4)
        self.assertEqual(report["omitted_failures"], 2)


class MatrixDeliveryTests(unittest.TestCase):
    def make_bundle(self, folder, code=0, xml=XML):
        plan = matrix.matrix_policy(["3.14"], policy=quality.test_policy())
        (folder / "matrix-policy.json").write_text(json.dumps(plan), encoding="utf-8")
        version = folder / "versions/3.14"
        version.mkdir(parents=True)
        (version / "execution.json").write_text(json.dumps({"phase": "pytest", "process_exit_code": code}), encoding="utf-8")
        (version / "runtime.json").write_text(json.dumps(runtime(code=code)), encoding="utf-8")
        (version / "exit-code.txt").write_text(str(code) + "\n", encoding="ascii")
        (version / "junit.xml").write_text(xml, encoding="utf-8")
        return version

    def test_gate_ignores_presentation_json(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            version = self.make_bundle(folder, 1, FAILED_XML)
            (folder / "matrix.json").write_text('{"overall_status":"passed"}')
            (version / "report.json").write_text('{"exit_code":0,"status":"passed"}')
            result = matrix.verify_bundle(folder)
            self.assertEqual(result["overall_status"], "failed")
            self.assertEqual(result["versions"][0]["pytest_exit_code"], 1)

    def test_missing_or_inconsistent_raw_evidence_blocks(self):
        for target in ("runtime.json", "exit-code.txt", "junit.xml", "execution.json"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temp:
                folder = Path(temp)
                version = self.make_bundle(folder)
                (version / target).unlink()
                self.assertEqual(matrix.verify_bundle(folder)["overall_status"], "error")
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            version = self.make_bundle(folder)
            (version / "runtime.json").write_text(json.dumps(runtime(code=1)))
            self.assertEqual(matrix.verify_bundle(folder)["overall_status"], "error")

    def test_summary_lists_all_versions_and_safe_failure(self):
        plan = matrix.matrix_policy(["3.12", "3.14"])
        summary = matrix.matrix_summary(matrix.aggregate(plan, []))
        self.assertIn("3.12", summary)
        self.assertIn("3.14", summary)
        self.assertIn("report_unavailable", summary)


class RealInterpreterTests(unittest.TestCase):
    def invoke(self, expected, test_source):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "test_actual.py").write_text(test_source)
            runner = ("import sys; from pathlib import Path; "
                      f"sys.path.insert(0, {str(MODULES)!r}); "
                      "import pytest_matrix_runtime as m; "
                      f"raise SystemExit(m.main([{expected!r}, '-q', '--junitxml=junit.xml', 'test_actual.py'], Path('runtime.json')))")
            process = subprocess.run([sys.executable, "-c", runner], cwd=folder, capture_output=True, text=True, timeout=30)
            metadata = json.loads((folder / "runtime.json").read_text())
            xml = (folder / "junit.xml").read_text() if (folder / "junit.xml").exists() else ""
            return process.returncode, metadata, xml

    def test_actual_interpreter_pass_and_failure(self):
        expected = f"{sys.version_info.major}.{sys.version_info.minor}"
        for source, code in (("def test_pass():\n    assert 1 == 1\n", 0),
                             ("def test_fail():\n    assert 1 == 2\n", 1)):
            actual, metadata, xml = self.invoke(expected, source)
            self.assertEqual(actual, code)
            self.assertEqual(metadata["pytest_exit_code"], code)
            self.assertEqual(metadata["actual_python"], runtime_module.runtime_info()["actual_python"])
            self.assertTrue(metadata["pytest_started"])
            self.assertTrue(reporting.summarize_pytest(xml, code, "")["report_available"])

    def test_wrong_interpreter_never_starts_pytest(self):
        expected = "3.12" if sys.version_info.minor != 12 else "3.13"
        code, metadata, xml = self.invoke(expected, "raise RuntimeError('must not be imported')\n")
        self.assertEqual(code, 64)
        self.assertEqual(metadata["error"], "interpreter_mismatch")
        self.assertFalse(metadata["pytest_started"])
        self.assertIsNone(metadata["pytest_exit_code"])
        self.assertEqual(xml, "")


if __name__ == "__main__":
    unittest.main()
