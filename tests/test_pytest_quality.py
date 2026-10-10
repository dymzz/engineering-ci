"""Quality policy, exact comparisons and real pytest-cov regressions; no Dagger."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ROOT / ".dagger/src/engineering_ci"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, MODULES / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


quality = load("pytest_quality_unit", "pytest_quality.py")
reporting = load("pytest_quality_reporting_unit", "pytest_report.py")


def evidence(covered=3, total=4):
    return {"xml": f'<coverage lines-covered="{covered}" lines-valid="{total}"/>',
            "json": json.dumps({"totals": {"covered_lines": covered, "num_statements": total,
                                         "percent_covered": 100}}), "diagnostic": None}


class QualityPolicyTests(unittest.TestCase):
    def test_default_arguments_unchanged(self):
        policy = quality.test_policy()
        self.assertEqual(quality.pytest_arguments(policy, "/tmp/junit.xml"),
                         ["uv", "run", "--no-sync", "pytest", "-q", "--tb=short",
                          "--junitxml=/tmp/junit.xml", "tests"])
        result = quality.evaluate_quality(policy, 0)
        self.assertTrue(result["gate"]["passed"])
        self.assertEqual(result["coverage"]["status"], "disabled")

    def test_explicit_markers_are_one_native_argument(self):
        expression = 'fast and not integration'
        args = quality.pytest_arguments(quality.test_policy("tests/unit", expression), "x.xml")
        self.assertEqual(args[-3:], ["-m", expression, "tests/unit"])
        for code in (1, 2, 4, 5):
            self.assertFalse(quality.evaluate_quality(quality.test_policy(markers="missing"), code)["gate"]["passed"])

    def test_invalid_configuration(self):
        for kwargs in ({"test_path": ""}, {"test_path": "../tests"}, {"test_path": "/tests"},
                       {"test_path": "D:/tests"}, {"test_path": "-q"}, {"markers": "fast\nslow"},
                       {"min_coverage": "80"}, {"coverage_source": " "},
                       {"coverage_source": "../src"}, {"markers": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                quality.test_policy(**kwargs)
        for threshold in ("-1", "101", "NaN", "inf", "eighty", "1e-999999999", "80.1234567890123"):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                quality.test_policy(coverage_source="src", min_coverage=threshold)

    def test_threshold_pass_fail_and_exact_boundary(self):
        for threshold, passed in (("74", True), ("75", True), ("75.000000000001", False),
                                  ("0", True), ("100", False)):
            policy = quality.test_policy(coverage_source="src", min_coverage=threshold)
            result = quality.evaluate_quality(policy, 0, evidence())
            self.assertEqual(result["gate"]["passed"], passed)
        for threshold, passed in (("83.333333333333", True), ("83.333333333334", False)):
            result = quality.evaluate_quality(quality.test_policy(coverage_source="src", min_coverage=threshold),
                                              0, evidence(5, 6))
            self.assertEqual(result["gate"]["passed"], passed)

    def test_test_failure_and_coverage_failure_independent(self):
        for code, threshold, expected in ((1, "75", ["pytest_failed"]),
                                          (0, "80", ["coverage_below_threshold"]),
                                          (1, "80", ["pytest_failed", "coverage_below_threshold"])):
            result = quality.evaluate_quality(quality.test_policy(coverage_source="src", min_coverage=threshold),
                                              code, evidence())
            self.assertEqual(result["gate"]["failure_reasons"], expected)

    def test_unavailable_corrupt_empty_or_mismatched_coverage_fails(self):
        policy = quality.test_policy(coverage_source="src")  # collection-only still needs valid data
        cases = [None, {"xml": "", "json": ""}, evidence(0, 0), evidence(5, 4),
                 {**evidence(), "xml": "<broken"}, {**evidence(), "json": "{broken"},
                 {**evidence(), "json": '{"totals":{"covered_lines":true,"num_statements":4}}'},
                 {**evidence(), "xml": '<coverage lines-covered="2" lines-valid="4"/>'}]
        for case in cases:
            with self.subTest(case=case):
                result = quality.evaluate_quality(policy, 0, case)
                self.assertEqual(result["coverage"]["status"], "error")
                self.assertFalse(result["gate"]["passed"])
                self.assertIsNone(result["coverage"]["line_percent"])

    def test_coverage_reader_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            xml, js = Path(tmp) / "coverage.xml", Path(tmp) / "coverage.json"
            self.assertIn("pytest-cov", quality.read_coverage(xml, js)["diagnostic"])
            xml.write_bytes(b"x" * (quality.MAX_COVERAGE_BYTES + 1))
            js.write_text("{}", encoding="utf-8")
            self.assertIn("unavailable", quality.read_coverage(xml, js)["diagnostic"])
            xml.write_bytes(b"\xff")
            self.assertIn("UnicodeDecodeError", quality.read_coverage(xml, js)["diagnostic"])


class RealPytestQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory()
        cls.results = {}
        for scenario in ("baseline", "coverage", "assertion", "markers", "empty", "invalid-markers", "missing-plugin", "invalid-path"):
            directory = Path(cls.workspace.name) / scenario
            shutil.copytree(ROOT / "tests/fixtures/coverage-app", directory,
                            ignore=shutil.ignore_patterns(".venv", "__pycache__", "uv.lock"))
            if scenario == "assertion":
                (directory / "tests/test_negative.py").write_text("def test_failure():\n    assert 1 == 2\n")
            args = [sys.executable, "-m", "pytest", "-q", "--tb=short", "--junitxml=junit.xml"]
            if scenario != "baseline":
                args += ["--cov=quality_sample", "--cov-fail-under=0", "--cov-report=xml:coverage.xml",
                         "--cov-report=json:coverage.json"]
            if scenario in ("markers", "empty", "invalid-markers"):
                args += ["-m", "fast" if scenario == "markers" else "not fast and not slow" if scenario == "empty" else "fast and ("]
            args += ["missing-tests" if scenario == "invalid-path" else "tests"]
            env = {**os.environ, "PYTEST_ADDOPTS": "", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1" if scenario == "missing-plugin" else ""}
            # An empty variable still disables autoload, so remove it for the normal runs.
            if scenario != "missing-plugin":
                env.pop("PYTEST_DISABLE_PLUGIN_AUTOLOAD")
            process = subprocess.run(args, cwd=directory, env=env, capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", timeout=60)
            xml = (directory / "junit.xml").read_text(encoding="utf-8") if (directory / "junit.xml").exists() else ""
            data = reporting.summarize_pytest(xml, process.returncode, process.stdout + process.stderr)
            cls.results[scenario] = (directory, process, data)

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def evaluate(self, scenario, threshold="80", markers=""):
        directory, process, report = self.results[scenario]
        policy = quality.test_policy(markers=markers, coverage_source="quality_sample", min_coverage=threshold)
        result = quality.evaluate_quality(policy, process.returncode,
            quality.read_coverage(directory / "coverage.xml", directory / "coverage.json"))
        return result, report, policy

    def test_default_pass_and_no_coverage_outputs(self):
        directory, process, report = self.results["baseline"]
        self.assertEqual(process.returncode, 0)
        self.assertEqual(report["counts"]["tests"], 2)
        self.assertNotIn("quality", report)
        self.assertFalse((directory / "coverage.xml").exists())

    def test_passing_tests_below_coverage_do_not_become_failed_tests(self):
        result, report, _ = self.evaluate("coverage", "100")
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["counts"]["failures"], 0)
        self.assertEqual(result["gate"]["failure_reasons"], ["coverage_below_threshold"])
        report["quality"] = result
        summary = reporting.markdown_summary(report)
        self.assertIn("Test execution: **PASSED**", summary)
        self.assertIn("Overall test quality gate | **FAILED**", summary)
        self.assertTrue(self.evaluate("coverage", "80")[0]["gate"]["passed"])

    def test_failing_tests_and_both_failures(self):
        result, report, _ = self.evaluate("assertion", "80")
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["failures"][0]["category"], "assertion_failure")
        self.assertEqual(result["coverage"]["status"], "passed")
        self.assertEqual(result["gate"]["failure_reasons"], ["pytest_failed"])
        self.assertEqual(self.evaluate("assertion", "100")[0]["gate"]["failure_reasons"],
                         ["pytest_failed", "coverage_below_threshold"])

    def test_marker_selection_and_empty_selection(self):
        result, report, _ = self.evaluate("markers", "50", "fast")
        self.assertEqual(report["counts"]["tests"], 1)
        self.assertTrue(result["gate"]["passed"])
        empty, report, _ = self.evaluate("empty", markers="not fast and not slow")
        self.assertEqual(report["exit_code"], 5)
        self.assertFalse(empty["gate"]["passed"])

    def test_configuration_errors_never_pass(self):
        for scenario in ("missing-plugin", "invalid-path", "invalid-markers"):
            with self.subTest(scenario=scenario):
                result, report, _ = self.evaluate(scenario)
                self.assertEqual(report["exit_code"], 4)
                self.assertFalse(result["gate"]["passed"])
        self.assertIn("unrecognized arguments", self.results["missing-plugin"][1].stderr)

    def test_ci_recomputes_gate_from_evidence_not_report_json(self):
        result, report, policy = self.evaluate("coverage", "100")
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        directory = Path(scratch.name) / "bundle"
        shutil.copytree(self.results["coverage"][0], directory)
        (directory / "exit-code.txt").write_text("0\n", encoding="ascii")
        (directory / "quality-policy.json").write_text(json.dumps(policy), encoding="utf-8")
        report["quality"] = {"gate": {"passed": True}}  # false presentation must not bypass the gate
        (directory / "report.json").write_text(json.dumps(report), encoding="utf-8")
        command = [sys.executable, str(MODULES / "pytest_ci.py"), "gate", str(directory)]
        gate = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(gate.returncode, 1)
        self.assertIn("required 100%", gate.stdout)
        policy["min_coverage"] = "80"
        (directory / "quality-policy.json").write_text(json.dumps(policy), encoding="utf-8")
        (directory / "report.json").unlink()  # report delivery cannot manufacture a failure either
        self.assertEqual(subprocess.run(command, capture_output=True, timeout=30).returncode, 0)
        (directory / "coverage.json").unlink()
        self.assertEqual(subprocess.run(command, capture_output=True, timeout=30).returncode, 1)


if __name__ == "__main__":
    unittest.main()
