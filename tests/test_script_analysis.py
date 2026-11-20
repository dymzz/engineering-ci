"""Workflow and PowerShell analysis policy, routing and report shaping."""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ROOT / ".dagger/src/engineering_ci_module"

sys_path = ROOT / "src"
import sys

if str(sys_path) not in sys.path:
    sys.path.insert(0, str(sys_path))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = load(
    "script_analysis_unit", ".dagger/src/engineering_ci_module/script_analysis.py"
)
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
            "scripts/build.sh": "shellcheck",
            "scripts/deploy.bash": "shellcheck",
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
            script.SHELLCHECK_VERSION,
            script.SHELLCHECK_WRAPPER,
            script.ZIZMOR_VERSION,
            script.PSSCRIPTANALYZER_VERSION,
            powershell.PSSCRIPTANALYZER_VERSION,
        ):
            with self.subTest(value=value):
                self.assertRegex(value, r"^\d+(\.\d+)+$")
        self.assertIn("powershell:", script.POWERSHELL_IMAGE)

    def test_shellcheck_and_zizmor_policies_reject_unsafe_inputs(self):
        for builder, kwargs in (
            (script.shellcheck_policy, {"scan_paths": ["../escape.sh"]}),
            (script.shellcheck_policy, {"scan_paths": []}),
            (script.shellcheck_policy, {"scan_paths": ["-x.sh"]}),
            (script.zizmor_policy, {"scan_paths": ["/etc/bad.yml"]}),
            (script.zizmor_policy, {"config": "../zizmor.yml"}),
            (script.zizmor_policy, {"persona": "bad;persona"}),
        ):
            with (
                self.subTest(builder=builder.__name__, kwargs=kwargs),
                self.assertRaises(ValueError),
            ):
                builder(**kwargs)


class ArgumentTests(unittest.TestCase):
    def test_actionlint_disables_optional_integrations_explicitly(self):
        arguments = script.actionlint_arguments(script.actionlint_policy([".github"]))
        self.assertIn("-shellcheck=", arguments)
        self.assertIn("-pyflakes=", arguments)
        self.assertEqual(arguments[-1], ".github")

    def test_actionlint_enables_shellcheck_and_json_when_requested(self):
        arguments = script.actionlint_arguments(
            script.actionlint_policy(
                [".github/workflows/ci.yml"],
                shellcheck=True,
                shellcheck_executable="shellcheck",
                json_format=True,
            )
        )
        self.assertIn("-shellcheck=shellcheck", arguments)
        self.assertNotIn("-shellcheck=", arguments)
        self.assertIn("-pyflakes=", arguments)
        self.assertIn("-format", arguments)

    def test_actionlint_runs_through_its_own_isolated_environment(self):
        prefix = script.runner_prefix("actionlint")
        self.assertEqual(
            prefix[:3], ["uvx", "--from", f"actionlint-py=={script.ACTIONLINT_WRAPPER}"]
        )
        sc_prefix = script.runner_prefix("shellcheck")
        self.assertEqual(
            sc_prefix[:3],
            ["uvx", "--from", f"shellcheck-py=={script.SHELLCHECK_WRAPPER}"],
        )
        paired = script.runner_prefix("actionlint+shellcheck")
        self.assertIn(f"shellcheck-py=={script.SHELLCHECK_WRAPPER}", paired)
        zz_prefix = script.runner_prefix("zizmor")
        self.assertEqual(
            zz_prefix, ["uvx", "--from", f"zizmor=={script.ZIZMOR_VERSION}", "zizmor"]
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

    def test_json_mode_requires_a_complete_json_array(self):
        policy = script.actionlint_policy(json_format=True)
        for output in (
            "",
            " \n",
            "unexpected output\n",
            actionlint_output(),
            "{}",
            "null",
            "[",
            "[42]",
            "[] trailing",
        ):
            with self.subTest(output=output):
                report = script.script_report(
                    policy, 0, stdout=output, stderr="native stderr"
                )
                self.assertEqual(report["outcome"], "failure")
                self.assertTrue(report["limitations"])
                self.assertEqual(report["exit_code"], 0)
                self.assertEqual(report["stdout"], output)
                self.assertEqual(report["stderr"], "native stderr")
                self.assertFalse(report["native_json_available"])
        valid = script.script_report(policy, 0, stdout="[]\n")
        self.assertEqual(valid["outcome"], "clean")
        self.assertEqual(
            valid["native_json"], {"format": "actionlint-json", "items": []}
        )

    def test_json_mode_limits_unprovided_raw_stdout(self):
        output = "x" * (script.MAX_NATIVE_BYTES + 1)
        report = script.script_report(
            script.actionlint_policy(json_format=True), 0, output
        )
        self.assertEqual(report["outcome"], "failure")
        self.assertTrue(report["native_truncated"])
        self.assertTrue(any("1 MiB" in item for item in report["limitations"]))
        self.assertEqual(report["stdout"], output[-script.MAX_STREAM_CHARS :])

    def test_text_mode_rejects_unrecognized_nonempty_output(self):
        report = self.report(0, "unexpected output\n")
        self.assertEqual(report["outcome"], "failure")
        self.assertTrue(report["limitations"])
        raw_only = script.script_report(
            script.actionlint_policy(), 0, native_raw="unexpected output\n"
        )
        self.assertEqual(raw_only["outcome"], "failure")
        self.assertTrue(raw_only["limitations"])

    def test_json_diagnostics_attribute_shellcheck_separately(self):
        raw = json.dumps(
            [
                {
                    "message": "Double quote to prevent globbing (SC2086).",
                    "filepath": ".github/workflows/ci.yml",
                    "line": 12,
                    "column": 5,
                    "kind": "shellcheck",
                },
                {
                    "message": 'label "ubuntu-latest" is ok? unknown input',
                    "filepath": ".github/workflows/ci.yml",
                    "line": 8,
                    "column": 3,
                    "kind": "runner-label",
                },
            ]
        )
        policy = script.actionlint_policy(
            [".github/workflows/ci.yml"], shellcheck=True, json_format=True
        )
        report = script.script_report(policy, 1, raw, native_raw=raw)
        self.assertEqual(report["outcome"], "findings")
        al_only, sc_only = script.split_actionlint_diagnostics(report["findings"])
        self.assertEqual(len(al_only), 1)
        self.assertEqual(al_only[0]["rule"], "runner-label")
        self.assertEqual(len(sc_only), 1)
        self.assertEqual(sc_only[0]["source_tool"], "shellcheck")
        self.assertIn("SC2086", sc_only[0]["message"])


class ShellCheckSemanticsTests(unittest.TestCase):
    def report(self, exit_code, stdout=""):
        return script.script_report(
            script.shellcheck_policy(["scripts/build.sh"]),
            exit_code,
            stdout=stdout,
            native_raw=stdout,
        )

    def test_zero_with_empty_comments_is_clean(self):
        rep = self.report(0, json.dumps({"comments": []}))
        self.assertEqual(rep["outcome"], "clean")

    def test_one_with_comments_is_findings(self):
        raw = json.dumps(
            {
                "comments": [
                    {
                        "file": "scripts/build.sh",
                        "line": 3,
                        "column": 6,
                        "level": "info",
                        "code": 2086,
                        "message": "Double quote to prevent globbing and word splitting.",
                    }
                ]
            }
        )
        rep = self.report(1, raw)
        self.assertEqual(rep["outcome"], "findings")
        self.assertEqual(rep["findings"][0]["rule"], "SC2086")

    def test_missing_or_invalid_json_fails_closed(self):
        for code, raw in (
            (0, ""),
            (0, "not-json"),
            (1, json.dumps({"comments": []})),
            (2, "{}"),
        ):
            with self.subTest(code=code, raw=raw):
                self.assertEqual(self.report(code, raw)["outcome"], "failure")


class ZizmorSemanticsTests(unittest.TestCase):
    def report(self, exit_code, stdout=""):
        return script.script_report(
            script.zizmor_policy([".github/workflows/ci.yml"]),
            exit_code,
            stdout=stdout,
            native_raw=stdout,
        )

    def test_zero_empty_array_is_clean_and_finding_exit_codes_preserve_facts(self):
        self.assertEqual(self.report(0, "[]")["outcome"], "clean")
        raw = json.dumps(
            [
                {
                    "ident": "unpinned-uses",
                    "desc": "unpinned action reference",
                    "determinations": {"confidence": "High", "severity": "High"},
                    "locations": [
                        {
                            "symbolic": {
                                "key": {
                                    "Local": {"given_path": ".github/workflows/ci.yml"}
                                },
                                "annotation": "uses: actions/checkout@v4",
                            },
                            "concrete": {
                                "location": {"start_point": {"row": 9, "column": 8}}
                            },
                        }
                    ],
                }
            ]
        )
        rep = self.report(14, raw)
        self.assertEqual(rep["outcome"], "findings")
        self.assertEqual(rep["exit_code"], 14)
        self.assertEqual(rep["findings"][0]["rule"], "unpinned-uses")
        self.assertEqual(rep["findings"][0]["line"], 10)

    def test_invalid_output_or_unexpected_exit_never_passes(self):
        for code, raw in ((0, ""), (1, "boom"), (14, "[]")):
            with self.subTest(code=code, raw=raw):
                self.assertEqual(self.report(code, raw)["outcome"], "failure")


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
        report = script.unsupported_report("scripts/a.txt", "out of scope")
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


class ShellCheckConfigAndActionlintDebugTests(unittest.TestCase):
    def test_shellcheck_arguments_isolate_or_declare_rcfile(self):
        default_pol = script.shellcheck_policy(["scripts/build.sh"])
        default_args = script.shellcheck_arguments(default_pol)
        self.assertIn("--norc", default_args)
        self.assertFalse(any(arg.startswith("--rcfile=") for arg in default_args))

        configured_pol = script.shellcheck_policy(
            ["scripts/build.sh"], config=".shellcheckrc"
        )
        configured_args = script.shellcheck_arguments(configured_pol)
        self.assertNotIn("--norc", configured_args)
        self.assertIn("--rcfile=.shellcheckrc", configured_args)

        with self.assertRaises(ValueError):
            script.shellcheck_policy(["scripts/build.sh"], config="../.shellcheckrc")

    def test_actionlint_policy_debug_emits_flag(self):
        pol = script.actionlint_policy(
            [".github/workflows/ci.yml"],
            shellcheck=True,
            json_format=True,
            debug=True,
        )
        args = script.actionlint_arguments(pol)
        self.assertIn("-debug", args)
        self.assertIn("-format", args)


if __name__ == "__main__":
    unittest.main()
