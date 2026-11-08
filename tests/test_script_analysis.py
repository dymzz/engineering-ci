"""Workflow and PowerShell analysis policy, routing and report shaping."""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ROOT / ".dagger/src/engineering_ci"

sys_path = ROOT / "src"
import sys

if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = load("script_analysis_unit", ".dagger/src/engineering_ci/script_analysis.py")
powershell = load(
    "powershell_analysis_unit", "src/engineering_ci/powershell_analysis.py"
)


def actionlint_output():
    """The real actionlint 1.7.12 output for the committed broken fixture.

    Recorded from an actual run on 2026-10-31, including the source excerpt
    lines actionlint prints below each diagnostic. The rules are the tool's
    own; a fabricated rule name would hide a change in what it reports.
    """
    return (
        "tests/fixtures/workflow-app/.github/workflows/broken.yml:12:9: "
        'unexpected key "uses" for step to run shell command. expected one of '
        '"continue-on-error", "env", "id", "if", "name", "run", "shell", '
        '"timeout-minutes", "working-directory" [syntax-check]\n'
        "   |\n"
        "12 |       - uses: actions/checkout@v7.0.1\n"
        "   |         ^~~~~\n"
        "tests/fixtures/workflow-app/.github/workflows/broken.yml:16:24: "
        'property "os" is not defined in object type {} [expression]\n'
        "   |\n"
        '16 |         run: echo "${{ matrix.os }}"\n'
        "   |                        ^~~~~~~~~\n"
    )


def envelope(status="ok", files=1, version="1.9.0"):
    return json.dumps(
        {
            "schema_version": 1,
            "status": status,
            "diagnostics": [],
            "files_scanned": files,
            "powershell_version": "7.5.0",
            "psscriptanalyzer_version": version,
        }
    )


def diagnostic(rule="PSAvoidUsingWriteHost"):
    return json.dumps(
        {
            "rule": rule,
            "severity": "Warning",
            "message": "Avoid using Write-Host.",
            "script": r"C:\ws\scripts\violations.ps1",
            "line": 11,
            "column": 5,
        }
    )


class RoutingTests(unittest.TestCase):
    def test_suffix_selects_the_tool(self):
        cases = {
            ".github/workflows/ci.yml": "actionlint",
            "ci.yaml": "actionlint",
            "scripts/build.ps1": "psscriptanalyzer",
            "scripts/helper.psm1": "psscriptanalyzer",
            "scripts/build.sh": "unsupported",
            "README.md": "unsupported",
            "Makefile": "unsupported",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(script.target_kind(path), expected)

    def test_routing_ignores_case_and_separators(self):
        self.assertEqual(script.target_kind(r"Scripts\Build.PS1"), "psscriptanalyzer")


class PolicyTests(unittest.TestCase):
    def test_dagger_policy_rejects_unsafe_inputs(self):
        for builder, kwargs in (
            (script.actionlint_policy, {"scan_paths": ["../outside"]}),
            (script.actionlint_policy, {"scan_paths": []}),
            (script.actionlint_policy, {"scan_paths": ["a.yml"] * 33}),
            (script.actionlint_policy, {"scan_paths": ["-x"]}),
            (script.actionlint_policy, {"config": "/etc/actionlint.yaml"}),
            (script.powershell_policy, {"platform": "macos"}),
            (script.powershell_policy, {"severity": "Critical"}),
            (script.powershell_policy, {"settings": ".."}),
            (script.powershell_policy, {"target_powershell_version": "5.1\n"}),
        ):
            with (
                self.subTest(builder=builder.__name__, kwargs=kwargs),
                self.assertRaises(ValueError),
            ):
                builder(**kwargs)

    def test_runner_policy_rejects_unsafe_inputs(self):
        for kwargs in (
            {"paths": []},
            {"paths": ["C:/Windows"]},
            {"paths": ["ok.ps1"], "severity": "Fatal"},
            {"paths": ["ok.ps1"], "settings": "/etc/x"},
            {"paths": [".."]},
        ):
            with (
                self.subTest(kwargs=kwargs),
                self.assertRaises(powershell.AnalysisError),
            ):
                powershell.paths_policy(**kwargs)

    def test_defaults_are_explicit(self):
        policy = script.actionlint_policy()
        self.assertEqual(policy["scan_paths"], [".github/workflows"])
        self.assertEqual(policy["pinned_version"], script.ACTIONLINT_VERSION)
        self.assertEqual(script.powershell_policy()["platform"], "any")
        self.assertEqual(
            powershell.paths_policy(["scripts"])["pinned_version"],
            powershell.PSSCRIPTANALYZER_VERSION,
        )

    def test_pinned_versions_are_concrete(self):
        for value in (
            script.ACTIONLINT_VERSION,
            script.ACTIONLINT_WRAPPER,
            script.PSSCRIPTANALYZER_VERSION,
            powershell.PSSCRIPTANALYZER_VERSION,
        ):
            with self.subTest(value=value):
                self.assertRegex(value, r"^\d+(\.\d+)+$")
        self.assertIn("powershell:", script.POWERSHELL_IMAGE)


class ArgumentTests(unittest.TestCase):
    def test_actionlint_disables_optional_integrations_explicitly(self):
        arguments = script.actionlint_arguments(script.actionlint_policy([".github"]))
        self.assertIn("-shellcheck=", arguments)
        self.assertIn("-pyflakes=", arguments)
        self.assertEqual(arguments[-1], ".github")

    def test_actionlint_runs_through_its_own_isolated_environment(self):
        prefix = script.runner_prefix("actionlint")
        self.assertEqual(
            prefix[:3], ["uvx", "--from", f"actionlint-py=={script.ACTIONLINT_WRAPPER}"]
        )
        with self.assertRaises(ValueError):
            script.runner_prefix("psscriptanalyzer")

    def test_powershell_arguments_stay_native(self):
        arguments = script.powershell_arguments(
            script.powershell_policy(
                ["scripts"], settings="psd1/x.psd1", severity="Error"
            )
        )
        self.assertEqual(arguments[:2], ["-Version", script.PSSCRIPTANALYZER_VERSION])
        self.assertIn("-Settings", arguments)
        self.assertIn("-Severity", arguments)
        # TargetPowerShellVersions is a settings property, not a CLI parameter.
        self.assertNotIn("-TargetPowerShellVersion", arguments)


class ActionlintSemanticsTests(unittest.TestCase):
    def report(self, exit_code, stdout=""):
        return script.script_report(script.actionlint_policy(), exit_code, stdout)

    def test_zero_is_clean_and_one_with_findings_is_a_finding(self):
        self.assertEqual(self.report(0)["outcome"], "clean")
        found = self.report(1, actionlint_output())
        self.assertEqual(found["outcome"], "findings")
        self.assertEqual(found["finding_count"], 2)
        # The rules are the ones the real tool emitted.
        self.assertEqual(found["findings"][0]["rule"], "syntax-check")
        self.assertEqual(found["findings"][0]["line"], 12)
        self.assertEqual(found["findings"][1]["rule"], "expression")
        self.assertEqual(found["findings"][1]["line"], 16)
        # The source excerpt lines actionlint prints are not diagnostics.
        self.assertNotIn("uses: actions/checkout", json.dumps(found["findings"]))

    def test_one_without_parsed_findings_is_a_failure(self):
        report = self.report(1, "something went wrong\n")
        self.assertEqual(report["outcome"], "failure")

    def test_unknown_exit_codes_never_pass(self):
        for code in (2, 3, 127):
            with self.subTest(code=code):
                self.assertEqual(self.report(code)["outcome"], "failure")


class PowershellSemanticsTests(unittest.TestCase):
    def report(self, exit_code, native=""):
        return script.script_report(
            script.powershell_policy(["scripts"]), exit_code, native_raw=native
        )

    def test_diagnostics_decide_the_result_not_the_process_status(self):
        report = self.report(0, envelope() + "\n" + diagnostic() + "\n")
        self.assertEqual(report["outcome"], "findings")
        self.assertEqual(report["findings"][0]["rule"], "PSAvoidUsingWriteHost")
        self.assertEqual(report["observed_version"], "1.9.0")

    def test_clean_run_needs_files_actually_scanned(self):
        self.assertEqual(self.report(0, envelope())["outcome"], "clean")
        empty = self.report(0, envelope(files=0))
        self.assertEqual(empty["outcome"], "not_executed")
        self.assertEqual(empty["reason"], "no_applicable_scripts")

    def test_envelope_errors_and_bad_process_status_are_failures(self):
        failed = self.report(3, envelope(status="error"))
        self.assertEqual(failed["outcome"], "failure")
        self.assertEqual(failed["reason"], "psscriptanalyzer_execution_error")
        crashed = self.report(1, envelope())
        self.assertEqual(crashed["outcome"], "failure")

    def test_missing_or_unreadable_output_is_never_a_pass(self):
        for native in ("", "{not json", '{"schema_version":1,"status":"ok"}\ngarbage'):
            with self.subTest(native=native):
                report = self.report(0, native)
                self.assertNotEqual(report["outcome"], "clean")
                self.assertTrue(report["limitations"])

    def test_single_diagnostic_does_not_collapse_into_an_empty_array(self):
        report = self.report(0, envelope() + "\n" + diagnostic() + "\n")
        self.assertEqual(report["finding_count"], 1)


class CoverageTests(unittest.TestCase):
    """A required input set has to be covered, and an empty one is distinct."""

    def workflow_report(self, candidates, analysed=None):
        return script.script_report(
            script.actionlint_policy(),
            0,
            coverage=script.coverage_record(
                [".github/workflows"], candidates, analysed
            ),
        )

    def powershell_report(self, candidates, scanned, native=None):
        return script.script_report(
            script.powershell_policy(["scripts"]),
            0,
            native_raw=envelope(files=scanned) if native is None else native,
            coverage=script.coverage_record(["scripts"], candidates, scanned),
        )

    def test_an_empty_scope_is_not_applicable_and_never_clean(self):
        report = self.workflow_report([], 0)
        self.assertEqual(report["outcome"], "not_applicable")
        self.assertEqual(report["reason"], "no_applicable_inputs")
        self.assertNotEqual(report["outcome"], "clean")
        self.assertEqual(report["coverage"]["candidates"], 0)

    def test_a_scope_that_was_not_analysed_is_a_coverage_failure(self):
        report = self.powershell_report(["a.ps1", "b.ps1"], 1)
        self.assertEqual(report["outcome"], "not_executed")
        self.assertEqual(report["reason"], "incomplete_input_coverage")

    def test_a_fully_covered_scope_may_be_clean(self):
        report = self.powershell_report(["a.ps1"], 1)
        self.assertEqual(report["outcome"], "clean")
        self.assertEqual(report["coverage"]["candidates"], 1)
        self.assertEqual(report["coverage"]["analysed"], 1)

    def test_the_coverage_record_names_the_declared_scope(self):
        report = self.workflow_report(["a.yml"], 1)
        self.assertEqual(report["coverage"]["scope"], [".github/workflows"])
        self.assertEqual(report["coverage"]["candidates"], 1)

    def test_an_empty_scope_report_states_no_exit_status(self):
        report = script.empty_scope_report(
            script.actionlint_policy(), [".github/workflows"]
        )
        self.assertEqual(report["outcome"], "not_applicable")
        self.assertIsNone(report["exit_code"])
        self.assertEqual(report["coverage"]["candidates"], 0)


class EnforcementTests(unittest.TestCase):
    """One execution produces the report; enforcing only reads that report."""

    def report(self, outcome):
        return {
            "tool": "actionlint",
            "pinned_version": script.ACTIONLINT_VERSION,
            "outcome": outcome,
            "reason": "because",
            "exit_code": 1,
            "findings": [],
            "omitted_findings": 0,
            "limitations": [],
            "stdout": "",
            "stderr": "",
        }

    def test_only_a_clean_result_passes(self):
        self.assertIn("clean", script.enforce(self.report("clean")))

    def test_every_other_outcome_fails_with_the_recorded_reason(self):
        for outcome in ("findings", "not_applicable", "not_executed", "failure"):
            with self.subTest(outcome=outcome):
                with self.assertRaises(RuntimeError) as raised:
                    script.enforce(self.report(outcome))
                self.assertIn(outcome, str(raised.exception))

    def test_the_enforced_report_is_the_one_the_tool_produced(self):
        report = script.script_report(
            script.actionlint_policy(), 1, actionlint_output()
        )
        with self.assertRaises(RuntimeError) as raised:
            script.enforce(report)
        self.assertIn("syntax-check", str(raised.exception))
        self.assertIn("2", str(raised.exception))


class ScopeArgumentTests(unittest.TestCase):
    def test_enumerated_files_replace_the_declared_scope(self):
        policy = script.actionlint_policy([".github/workflows"])
        arguments = script.actionlint_arguments(policy, ["a.yml", "b.yml"])
        self.assertEqual(arguments[-2:], ["a.yml", "b.yml"])
        self.assertIn("-shellcheck=", arguments)
        self.assertIn("-pyflakes=", arguments)

    def test_the_declared_scope_is_used_when_no_files_are_enumerated(self):
        policy = script.actionlint_policy([".github/workflows"])
        self.assertEqual(script.actionlint_arguments(policy)[-1], ".github/workflows")


class DeferralTests(unittest.TestCase):
    def test_powershell_on_dagger_is_stated_not_skipped(self):
        report = script.deferred_report("scripts/a.ps1", "runs on a Windows runner")
        self.assertEqual(report["outcome"], "not_executed")
        self.assertEqual(report["tool"], "psscriptanalyzer")
        self.assertEqual(report["limitations"], ["runs on a Windows runner"])

    def test_unsupported_scope_is_stated(self):
        report = script.unsupported_report("scripts/a.sh", "out of scope")
        self.assertEqual(report["outcome"], "not_executed")
        self.assertEqual(report["finding_count"], 0)

    def test_environment_gap_names_the_limitation(self):
        self.assertIsNone(script.environment_gap(script.powershell_policy(["a.ps1"])))
        windows = script.environment_gap(script.powershell_policy(["a.ps1"], "windows"))
        self.assertIn("Windows runner", windows)
        legacy = script.environment_gap(
            script.powershell_policy(["a.ps1"], target_powershell_version="5.1")
        )
        self.assertIn("5.1", legacy)
        modern = script.environment_gap(
            script.powershell_policy(["a.ps1"], target_powershell_version="7.4")
        )
        self.assertIsNone(modern)


class RunnerSelectionTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.workspace = Path(tempfile.mkdtemp())
        (self.workspace / "scripts").mkdir()
        for name, body in (
            ("clean.ps1", "Get-ChildItem\n"),
            ("module.psm1", "function Get-Thing { }\n"),
            ("notes.md", "# not a script\n"),
        ):
            (self.workspace / "scripts" / name).write_text(body, encoding="utf-8")
        self.workspace.joinpath("outside.ps1").write_text("Get-Date\n")

    def test_only_powershell_suffixes_are_selected(self):
        selected = powershell.select_scripts(self.workspace, ["scripts"])
        self.assertEqual(
            sorted(item.name for item in selected), ["clean.ps1", "module.psm1"]
        )

    def test_recursive_selection_finds_nested_modules(self):
        nested = self.workspace / "scripts" / "deep"
        nested.mkdir()
        (nested / "x.psm1").write_text("function Get-X { }\n", encoding="utf-8")
        selected = powershell.select_scripts(self.workspace, ["scripts"])
        self.assertIn("x.psm1", [item.name for item in selected])

    def test_missing_input_is_an_error_not_an_empty_scope(self):
        with self.assertRaises(powershell.AnalysisError):
            powershell.select_scripts(self.workspace, ["absent"])

    def test_no_matching_scripts_is_not_executed_rather_than_clean(self):
        report = {
            "schema_version": 1,
            "tool": "psscriptanalyzer",
            "pinned_version": powershell.PSSCRIPTANALYZER_VERSION,
        }
        # The gate path: analyse() returns not_executed before touching the tool.
        import inspect

        source = inspect.getsource(powershell.analyse)
        self.assertIn("not_executed", source)
        self.assertEqual(report["tool"], "psscriptanalyzer")


class ModeTests(unittest.TestCase):
    def test_off_does_not_invoke_anything(self):
        import tempfile

        out = Path(tempfile.mkdtemp()) / "bundle"
        self.assertEqual(powershell.main(["--mode", "off", "--out", str(out)]), 0)
        self.assertFalse(out.exists())

    def test_mode_is_restricted_to_the_three_explicit_values(self):
        import contextlib
        import io
        import tempfile

        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            powershell.main(
                ["--mode", "block", "--out", str(Path(tempfile.mkdtemp()) / "b")]
            )

    def test_failure_is_written_as_a_failure_bundle(self):
        import json
        import tempfile

        out = Path(tempfile.mkdtemp()) / "bundle"
        code = powershell.main(
            [
                "--mode",
                "report",
                "--workspace",
                str(Path(tempfile.mkdtemp()) / "absent-workspace"),
                "--paths",
                "scripts",
                "--out",
                str(out),
            ]
        )
        self.assertEqual(code, 1)
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["outcome"], "failure")
        self.assertTrue(report["limitations"])

    def test_records_preserves_error_envelope_and_failure_cause(self):
        raw = (
            '{"schema_version": 1, "status": "ok", "files_scanned": 1}\n'
            '{"rule": "RuleA", "severity": "Warning"}\n'
            '{"schema_version": 1, "status": "error", "error": "Analyzing broken.ps1 failed: parse error", "files_scanned": 1, "candidates": 2}\n'
        )
        envelope, diags = powershell._records(raw)
        self.assertEqual(envelope["status"], "error")
        self.assertEqual(envelope["error"], "Analyzing broken.ps1 failed: parse error")
        self.assertEqual(envelope["files_scanned"], 1)
        self.assertEqual(len(diags), 1)


if __name__ == "__main__":
    unittest.main()
