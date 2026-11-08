"""Pinned scanner policy, native exit-code semantics and report shaping."""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ROOT / ".dagger/src/engineering_ci_module"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, MODULES / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


static = load("python_static_unit", "python_static.py")


def vulture_output(line=9, name="never_called", confidence=60):
    return (
        f"dead.py:{line}: unused function '{name}' ({confidence}% confidence)\n"
        f"dead.py:{line + 1}: unused variable 'left' (60% confidence)\n"
    )


def pyright_payload(errors=1, files=1, rule="reportReturnType"):
    diagnostics = [
        {
            "file": "typed.py",
            "severity": "error",
            "message": 'Type "str" is not assignable to return type "int"',
            "range": {"start": {"line": 5, "character": 11}},
            "rule": rule,
        }
    ][:errors]
    return json.dumps(
        {
            "version": static.SCANNER_VERSIONS["pyright"],
            "generalDiagnostics": diagnostics,
            "summary": {
                "filesAnalyzed": files,
                "errorCount": errors,
                "warningCount": 0,
                "informationCount": 0,
            },
        }
    )


def deptry_payload(code="DEP002", module="requests"):
    return json.dumps(
        [
            {
                "error": {
                    "code": code,
                    "message": f"'{module}' defined as a dependency but not used",
                },
                "module": module,
                "location": {"file": "pyproject.toml", "line": None, "column": None},
            }
        ]
    )


def pylint_payload(path="dup_a.py"):
    return json.dumps(
        [
            {
                "type": "refactor",
                "module": "dup_a",
                "obj": "build_record",
                "line": 5,
                "column": 0,
                "path": path,
                "symbol": "build_record",
                "message": "Similar lines in 2 files",
                "message-id": "R0801",
            }
        ]
    )


class PinnedVersionsTests(unittest.TestCase):
    def test_every_scanner_has_one_pinned_version(self):
        self.assertEqual(
            set(static.SCANNER_VERSIONS),
            {"vulture", "pyright", "deptry", "pylint"},
        )
        for tool, version in static.SCANNER_VERSIONS.items():
            with self.subTest(tool=tool):
                self.assertRegex(version, r"^\d+\.\d+(\.\d+)?$")
                self.assertEqual(
                    static.runner_prefix(tool, version),
                    ["uvx", "--from", f"{tool}=={version}", tool],
                )

    def test_runner_cannot_drift_from_the_pinned_version(self):
        with self.assertRaises(ValueError):
            static.runner_prefix("vulture", "2.15")
        with self.assertRaises(ValueError):
            static.runner_prefix("bandit", "1.0")

    def test_runner_keeps_the_consumer_environment_untouched(self):
        prefix = static.runner_prefix("deptry", static.SCANNER_VERSIONS["deptry"])
        self.assertNotIn("sync", prefix)
        self.assertNotIn("run", prefix)


class PolicyTests(unittest.TestCase):
    def test_unsafe_paths_and_options_are_rejected(self):
        for builder, kwargs in (
            (static.vulture_policy, {"scan_path": "../src"}),
            (static.vulture_policy, {"scan_path": "/etc"}),
            (static.vulture_policy, {"scan_path": "-q"}),
            (static.vulture_policy, {"scan_path": "D:/src"}),
            (static.vulture_policy, {"scan_path": "  "}),
            (static.vulture_policy, {"min_confidence": 101}),
            (static.vulture_policy, {"min_confidence": True}),
            (static.vulture_policy, {"exclude": "a\nb"}),
            (static.vulture_policy, {"whitelist_path": ".."}),
            (static.pyright_policy, {"config": "../pyproject.toml"}),
            (static.pyright_policy, {"level": "verbose"}),
            (static.pyright_policy, {"python_path": "relative/python"}),
            (static.pyright_policy, {"python_path": "/workspace/../etc/python"}),
            (static.pyright_policy, {"python_version": "3.14\n"}),
            (static.deptry_policy, {"scan_path": "/workspace/src"}),
            (static.deptry_policy, {"config": "-c"}),
            (static.deptry_policy, {"per_rule_ignores": "DEP001=x\n"}),
            (static.pylint_policy, {"scan_paths": []}),
            (static.pylint_policy, {"scan_paths": ["src"] * 33}),
            (static.pylint_policy, {"scan_paths": ["--rcfile"]}),
            (static.pylint_policy, {"min_similarity_lines": 0}),
            (static.pylint_policy, {"ignore_paths": ["src"] * 33}),
        ):
            with (
                self.subTest(builder=builder.__name__, kwargs=kwargs),
                self.assertRaises(ValueError),
            ):
                builder(**kwargs)

    def test_container_interpreter_path_is_accepted(self):
        policy = static.pyright_policy(python_path="/workspace/.venv/bin/python")
        self.assertEqual(policy["python_path"], "/workspace/.venv/bin/python")

    def test_default_policies_match_the_documented_scope(self):
        self.assertEqual(static.vulture_policy()["scan_path"], ".")
        self.assertEqual(static.deptry_policy()["scan_path"], "src")
        self.assertEqual(static.pylint_policy()["scan_paths"], ["src"])
        self.assertEqual(static.pylint_policy()["min_similarity_lines"], 4)
        self.assertEqual(static.pyright_policy()["level"], "")


class ArgumentTests(unittest.TestCase):
    def test_vulture_uses_native_confidence_exclude_and_whitelist(self):
        policy = static.vulture_policy(
            scan_path="src",
            min_confidence=80,
            exclude="tests/*",
            whitelist_path="tools/whitelist.py",
        )
        self.assertEqual(
            static.vulture_arguments(policy),
            [
                "--min-confidence",
                "80",
                "--exclude",
                "tests/*",
                "tools/whitelist.py",
                "src",
            ],
        )

    def test_pyright_requests_one_json_execution(self):
        policy = static.pyright_policy(
            scan_path="src",
            config="pyrightconfig.json",
            level="error",
            python_path="/workspace/.venv/bin/python",
            python_version="3.14",
        )
        arguments = static.pyright_arguments(policy)
        self.assertEqual(arguments[0], "--outputjson")
        self.assertEqual(arguments.count("--outputjson"), 1)
        self.assertEqual(arguments[-1], "src")
        self.assertIn("/workspace/.venv/bin/python", arguments)

    def test_deptry_keeps_native_root_semantics(self):
        self.assertEqual(
            static.deptry_arguments(static.deptry_policy("src")),
            ["--no-ansi", "src"],
        )

    def test_pylint_enables_only_duplicate_code(self):
        arguments = static.pylint_arguments(
            static.pylint_policy(["src", "worker"], min_similarity_lines=6)
        )
        self.assertEqual(arguments[:2], ["--disable=all", "--enable=duplicate-code"])
        self.assertNotIn("--enable=all", arguments)
        self.assertEqual(arguments[-2:], ["src", "worker"])
        self.assertIn("6", arguments)
        # One run has to emit both the exit status and the native diagnostics.
        self.assertIn("--output-format=json", arguments)


class VultureSemanticsTests(unittest.TestCase):
    def report(self, exit_code, stdout=""):
        return static.static_report(static.vulture_policy("."), exit_code, stdout)

    def test_zero_is_clean_and_three_is_a_finding(self):
        self.assertEqual(self.report(0)["outcome"], "clean")
        found = self.report(3, vulture_output())
        self.assertEqual(found["outcome"], "findings")
        self.assertEqual(found["reason"], "dead_code_found")
        self.assertEqual(found["exit_code"], 3)

    def test_parse_and_input_errors_never_become_findings(self):
        for code in (1, 2, 127):
            with self.subTest(code=code):
                report = self.report(code)
                self.assertEqual(report["outcome"], "failure")
                self.assertEqual(report["finding_count"], 0)

    def test_findings_keep_native_text_location_and_confidence(self):
        report = self.report(3, vulture_output())
        self.assertEqual(report["finding_count"], 2)
        first = report["findings"][0]
        self.assertEqual(first["path"], "dead.py")
        self.assertEqual(first["line"], 9)
        self.assertEqual(first["confidence"], 60)
        self.assertIn("never_called", first["message"])

    def test_native_stdout_survives_into_the_report(self):
        report = self.report(3, vulture_output())
        self.assertIn("60% confidence", report["stdout"])
        self.assertIsNone(report["native_json"])


class PyrightSemanticsTests(unittest.TestCase):
    def report(self, exit_code, native=""):
        return static.static_report(
            static.pyright_policy("."), exit_code, native_raw=native
        )

    def test_one_is_type_errors_and_zero_is_clean(self):
        self.assertEqual(self.report(1, pyright_payload())["outcome"], "findings")
        self.assertEqual(self.report(0, pyright_payload(errors=0))["outcome"], "clean")

    def test_configuration_and_input_errors_stay_failures(self):
        cases = {2: "fatal_error", 3: "configuration_error", 4: "invalid_input"}
        for code, reason in cases.items():
            with self.subTest(code=code):
                report = self.report(code)
                self.assertEqual(report["outcome"], "failure")
                self.assertEqual(report["reason"], reason)

    def test_zero_analysed_files_is_not_executed_not_clean(self):
        report = self.report(0, pyright_payload(errors=0, files=0))
        self.assertEqual(report["outcome"], "not_executed")
        self.assertEqual(report["reason"], "no_applicable_source")
        self.assertTrue(any("analyzed 0 files" in x for x in report["limitations"]))

    def test_unresolved_imports_are_reported_as_a_limitation(self):
        report = self.report(1, pyright_payload(rule="reportMissingImports"))
        self.assertEqual(report["outcome"], "findings")
        self.assertTrue(
            any("did not resolve" in item for item in report["limitations"])
        )

    def test_native_diagnostics_and_version_are_preserved(self):
        report = self.report(1, pyright_payload())
        self.assertTrue(report["native_json_available"])
        self.assertEqual(report["observed_version"], static.SCANNER_VERSIONS["pyright"])
        self.assertEqual(report["findings"][0]["line"], 6)
        self.assertEqual(report["findings"][0]["rule"], "reportReturnType")

    def test_unparsable_native_output_is_a_limitation_not_a_crash(self):
        report = self.report(1, "{not json")
        self.assertFalse(report["native_json_available"])
        self.assertTrue(any("not valid JSON" in x for x in report["limitations"]))


class DeptrySemanticsTests(unittest.TestCase):
    def report(self, exit_code, native=""):
        return static.static_report(
            static.deptry_policy("src"), exit_code, native_raw=native
        )

    def test_one_with_a_native_report_is_a_finding(self):
        report = self.report(1, deptry_payload())
        self.assertEqual(report["outcome"], "findings")
        self.assertEqual(report["findings"][0]["rule"], "DEP002")
        self.assertEqual(report["findings"][0]["module"], "requests")

    def test_one_without_a_native_report_is_a_crash_not_a_finding(self):
        # deptry exits 1 from an uncaught exception too.
        report = self.report(1, "")
        self.assertEqual(report["outcome"], "failure")
        self.assertEqual(report["reason"], "deptry_execution_error")
        self.assertEqual(report["finding_count"], 0)

    def test_two_is_an_input_error_and_zero_is_clean(self):
        self.assertEqual(self.report(2)["reason"], "invalid_input")
        self.assertEqual(self.report(0, "[]")["outcome"], "clean")


class PylintSemanticsTests(unittest.TestCase):
    def report(self, exit_code, native=""):
        return static.static_report(
            static.pylint_policy(["src"]), exit_code, native_raw=native
        )

    def test_refactor_bit_is_the_duplicate_code_finding(self):
        report = self.report(8, pylint_payload())
        self.assertEqual(report["outcome"], "findings")
        self.assertEqual(report["reason"], "duplicate_code_found")
        self.assertEqual(report["findings"][0]["rule"], "R0801")

    def test_warning_and_convention_bits_are_findings_too(self):
        for code in (4, 16, 12):
            with self.subTest(code=code):
                self.assertEqual(
                    self.report(code, pylint_payload())["outcome"], "findings"
                )

    def test_fatal_error_and_usage_bits_stay_failures(self):
        for code in (1, 2, 32, 3):
            with self.subTest(code=code):
                self.assertEqual(self.report(code)["outcome"], "failure")

    def test_findings_without_the_native_report_do_not_pass(self):
        report = self.report(8)
        self.assertEqual(report["outcome"], "failure")
        self.assertEqual(report["reason"], "pylint_report_unavailable")
        self.assertEqual(report["finding_count"], 0)

    def test_failure_bit_wins_over_a_finding_bit(self):
        report = self.report(2 | 8, pylint_payload())
        self.assertEqual(report["outcome"], "failure")

    def test_zero_is_clean_and_unknown_bits_are_failures(self):
        self.assertEqual(self.report(0)["outcome"], "clean")
        self.assertEqual(self.report(64)["reason"], "unknown_pylint_exit_code")


class ReportShapeTests(unittest.TestCase):
    def test_policy_keeps_only_the_caller_selectable_options(self):
        report = static.static_report(static.vulture_policy("src", 90), 0)
        self.assertEqual(
            set(report["policy"]),
            {"scan_path", "min_confidence", "exclude", "whitelist_path"},
        )
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["pinned_version"], static.SCANNER_VERSIONS["vulture"])

    def test_invalid_exit_codes_are_rejected(self):
        for code in (-1, 256, "0", 1.0, True):
            with self.subTest(code=code), self.assertRaises(ValueError):
                static.static_report(static.vulture_policy("."), code)

    def test_finding_lists_are_bounded_without_losing_the_count(self):
        output = "".join(
            f"a.py:{index}: unused variable 'x' (60% confidence)\n"
            for index in range(static.MAX_FINDINGS + 25)
        )
        report = static.static_report(static.vulture_policy("."), 3, output)
        self.assertEqual(len(report["findings"]), static.MAX_FINDINGS)
        self.assertEqual(report["finding_count"], static.MAX_FINDINGS + 25)
        self.assertEqual(report["omitted_findings"], 25)

    def test_streams_are_bounded_from_the_end(self):
        noise = "x" * (static.MAX_STREAM_CHARS + 500)
        report = static.static_report(static.vulture_policy("."), 1, "", noise)
        self.assertEqual(len(report["stderr"]), static.MAX_STREAM_CHARS)

    def test_oversized_native_output_is_flagged_not_silently_dropped(self):
        raw = json.dumps([{"padding": "y" * (static.MAX_NATIVE_BYTES + 10)}])
        report = static.static_report(static.deptry_policy("src"), 1, native_raw=raw)
        self.assertTrue(report["native_truncated"])
        self.assertIsNone(report["native_json"])
        self.assertTrue(any("1 MiB" in x for x in report["limitations"]))


class SummaryTests(unittest.TestCase):
    def test_summary_keeps_native_locations_and_limitations(self):
        report = static.static_report(
            static.pyright_policy("."),
            1,
            native_raw=pyright_payload(errors=1, files=0),
        )
        summary = static.findings_summary(report)
        self.assertIn("pyright 1.1.414 exit 1 (findings)", summary)
        self.assertIn("typed.py:6", summary)
        self.assertIn("reportReturnType", summary)
        self.assertIn("limitation:", summary)

    def test_finding_free_failure_still_shows_native_output(self):
        report = static.static_report(
            static.pylint_policy(["src"]), 1, stderr="ImportError: no astroid\n"
        )
        summary = static.findings_summary(report)
        self.assertIn("(failure)", summary)
        self.assertIn("ImportError", summary)


if __name__ == "__main__":
    unittest.main()
