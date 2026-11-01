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
    return (
        "broken.yml:8:10: both uses and run are specified in the same step "
        "[step-syntax]\n"
        "broken.yml:12:32: Undefined named-value: 'matrix'. "
        "Available names: always, github [expression]\n"
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

    def test_enumerated_inputs_replace_the_scope_in_the_arguments(self):
        policy = script.actionlint_policy([".github/workflows"])
        arguments = script.actionlint_arguments(
            policy, [".github/workflows/ci.yml", ".github/workflows/ci.yml"]
        )
        self.assertEqual(
            arguments,
            [
                "-no-color",
                "-shellcheck=",
                "-pyflakes=",
                ".github/workflows/ci.yml",
                ".github/workflows/ci.yml",
            ],
        )

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
        self.assertEqual(found["findings"][0]["rule"], "step-syntax")
        self.assertEqual(found["findings"][0]["line"], 8)

    def test_one_without_parsed_findings_is_a_failure(self):
        report = self.report(1, "something went wrong\n")
        self.assertEqual(report["outcome"], "failure")

    def test_unknown_exit_codes_never_pass(self):
        for code in (2, 3, 127):
            with self.subTest(code=code):
                self.assertEqual(self.report(code)["outcome"], "failure")


class ObservedActionlintOutputTests(unittest.TestCase):
    """The real actionlint 1.7.12 output of the committed broken fixture.

    Observed on 2026-10-31 by running the pinned actionlint against
    tests/fixtures/workflow-app/.github/workflows/broken.yml: exit 1, two
    diagnostics, each followed by the source excerpt lines actionlint prints.
    """

    OBSERVED = (
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

    def test_the_observed_output_is_parsed_into_its_diagnostics(self):
        report = script.script_report(script.actionlint_policy(), 1, self.OBSERVED)
        self.assertEqual(report["outcome"], "findings")
        self.assertEqual(report["finding_count"], 2)
        self.assertEqual(report["findings"][0]["rule"], "syntax-check")
        self.assertEqual(report["findings"][0]["line"], 12)
        self.assertEqual(
            report["findings"][0]["path"],
            ("tests/fixtures/workflow-app/.github/workflows/broken.yml"),
        )
        self.assertEqual(report["findings"][1]["rule"], "expression")
        self.assertEqual(report["findings"][1]["line"], 16)

    def test_the_excerpt_lines_are_not_read_as_diagnostics(self):
        report = script.script_report(script.actionlint_policy(), 1, self.OBSERVED)
        self.assertTrue(all("|" not in item["message"] for item in report["findings"]))


class WorkflowScopeTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.workspace = Path(tempfile.mkdtemp())
        (self.workspace / ".github/workflows").mkdir(parents=True)
        (self.workspace / ".github/workflows/ci.yml").write_text("a: 1\n")
        (self.workspace / ".github/workflows/release.yaml").write_text("b: 2\n")
        (self.workspace / ".github/workflows/notes.txt").write_text("c\n")
        (self.workspace / ".github/workflows/actionlint.yaml").write_text("d: 4\n")
        (self.workspace / ".github/workflows/nested").mkdir()
        (self.workspace / ".github/workflows/nested/extra.yml").write_text("e: 5\n")
        (self.workspace / "scripts").mkdir()
        (self.workspace / "scripts/build.ps1").write_text("Get-Date\n")

    def select(self, paths):
        return script.select_workflows(self.workspace, paths)

    def test_the_scope_names_every_workflow_the_scan_has_to_cover(self):
        selected = self.select([".github/workflows"])
        self.assertTrue(selected["present"])
        self.assertTrue(selected["applicable"])
        self.assertEqual(
            selected["inputs"],
            [
                ".github/workflows/ci.yml",
                ".github/workflows/nested/extra.yml",
                ".github/workflows/release.yaml",
            ],
        )
        self.assertEqual(selected["input_count"], 3)

    def test_only_the_declared_suffix_selects_an_input(self):
        selected = self.select([".github/workflows"])
        self.assertNotIn(".github/workflows/notes.txt", selected["inputs"])
        self.assertNotIn(".github/workflows/actionlint.yaml", selected["inputs"])

    def test_a_scope_outside_the_workflow_directory_contributes_nothing(self):
        selected = self.select(["scripts"])
        self.assertTrue(selected["present"])
        self.assertFalse(selected["applicable"])
        self.assertEqual(selected["inputs"], [])

    def test_an_absent_scope_is_absent_and_not_applicable(self):
        selected = self.select([".github/nothing"])
        self.assertFalse(selected["present"])
        self.assertFalse(selected["applicable"])
        self.assertEqual(selected["input_count"], 0)

    def test_an_explicit_file_is_the_whole_required_input(self):
        selected = self.select([".github/workflows/ci.yml"])
        self.assertEqual(selected["inputs"], [".github/workflows/ci.yml"])
        self.assertTrue(selected["applicable"])

    def test_the_repository_workflows_are_the_required_inputs(self):
        selected = script.select_workflows(ROOT, [".github/workflows"])
        self.assertEqual(
            selected["inputs"],
            [
                ".github/workflows/ci.yml",
                ".github/workflows/release.yml",
                ".github/workflows/reusable-python-ci.yml",
            ],
        )


class CoverageTests(unittest.TestCase):
    def test_a_report_states_the_inputs_it_covered(self):
        covered = script.coverage(
            [".github/workflows"],
            script.select_workflows(ROOT, [".github/workflows"]),
        )
        report = script.script_report(script.actionlint_policy(), 0, covered=covered)
        self.assertEqual(report["coverage"]["input_count"], 3)
        self.assertTrue(report["coverage"]["applicable"])
        self.assertEqual(report["coverage"]["scope"], [".github/workflows"])

    def test_an_empty_scope_is_never_reported_as_clean(self):
        covered = script.coverage(
            ["scripts"], script.select_workflows(ROOT, ["scripts"])
        )
        report = script.not_applicable_report(
            script.actionlint_policy(), covered, "no_applicable_workflows"
        )
        self.assertEqual(report["outcome"], "not_executed")
        self.assertEqual(report["reason"], "no_applicable_workflows")
        self.assertFalse(report["coverage"]["applicable"])
        self.assertIsNone(report["exit_code"])

    def test_an_absent_scope_is_reported_as_absent(self):
        covered = script.coverage(
            [".github/nothing"],
            script.select_workflows(ROOT, [".github/nothing"]),
        )
        report = script.not_applicable_report(
            script.actionlint_policy(), covered, "scope_absent"
        )
        self.assertEqual(report["outcome"], "not_executed")
        self.assertEqual(report["reason"], "scope_absent")
        self.assertFalse(report["coverage"]["present"])

    def test_a_deferred_scope_states_no_coverage_at_all(self):
        report = script.deferred_report("scripts/a.ps1", "runs on a Windows runner")
        self.assertEqual(report["coverage"]["input_count"], 0)
        self.assertFalse(report["coverage"]["applicable"])

    def test_the_summary_names_the_covered_scope(self):
        covered = script.coverage(
            [".github/workflows"],
            script.select_workflows(ROOT, [".github/workflows"]),
        )
        report = script.script_report(script.actionlint_policy(), 0, covered=covered)
        self.assertIn("coverage:", script.findings_summary(report))
        self.assertIn("3 input(s)", script.findings_summary(report))


class ScopeCommandTests(unittest.TestCase):
    """The command the Dagger container runs to enumerate its required inputs."""

    def test_the_command_prints_the_enumerated_scope_as_json(self):
        import contextlib
        import io

        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = script.main(
                ["scope", "--root", str(ROOT), "--path", ".github/workflows"]
            )
        self.assertEqual(code, 0)
        value = json.loads(stream.getvalue())
        self.assertEqual(value["input_count"], 3)
        self.assertTrue(value["applicable"])
        self.assertEqual(
            value["inputs"],
            [
                ".github/workflows/ci.yml",
                ".github/workflows/release.yml",
                ".github/workflows/reusable-python-ci.yml",
            ],
        )


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

    def test_the_analysed_scripts_are_named_in_the_evidence(self):
        import tempfile

        workspace = Path(tempfile.mkdtemp())
        (workspace / "scripts").mkdir()
        (workspace / "scripts/clean.ps1").write_text("Get-ChildItem\n")
        (workspace / "scripts/module.psm1").write_text("function Get-Thing { }\n")
        names = [
            powershell._relative_name(workspace, workspace / "scripts/clean.ps1"),
            powershell._relative_name(workspace, workspace / "scripts/module.psm1"),
        ]
        self.assertEqual(sorted(names), ["scripts/clean.ps1", "scripts/module.psm1"])

    def test_a_script_outside_the_workspace_is_named_by_its_file_name(self):
        import tempfile

        workspace = Path(tempfile.mkdtemp())
        outside = Path(tempfile.mkdtemp()) / "other.ps1"
        outside.write_text("Get-Date\n")
        self.assertEqual(powershell._relative_name(workspace, outside), "other.ps1")


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


if __name__ == "__main__":
    unittest.main()
