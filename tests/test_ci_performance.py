"""Timing arithmetic and workflow failure propagation need no engine or network."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "ci_performance", ROOT / "scripts/ci_performance.py"
)
performance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(performance)


def job(identifier, start, end):
    return {
        "id": identifier,
        "name": str(identifier),
        "status": "completed",
        "conclusion": "success",
        "started_at": "2026-10-10T00:00:" + start + "Z",
        "completed_at": "2026-10-10T00:00:" + end + "Z",
        "steps": [],
    }


class PerformanceTests(unittest.TestCase):
    def test_parallel_jobs_are_not_added_to_critical_path(self):
        run = {
            "id": 1,
            "name": "CI",
            "event": "pull_request",
            "head_sha": "sha",
            "html_url": "https://github.com/example/project/actions/runs/1",
            "run_attempt": 1,
            "created_at": "2026-10-10T00:00:00Z",
        }
        report = performance.summarize(run, [job(1, "02", "12"), job(2, "03", "11")])
        self.assertEqual(report["checks_execution_window_seconds"], 10)
        self.assertEqual(report["submission_to_checks_complete_seconds"], 12)
        self.assertEqual(report["parallel_runner_seconds"], 18)
        self.assertEqual(report["queued_before_first_job_seconds"], 2)
        self.assertIsNone(report["jobs"][0]["queue_or_scheduling_seconds"])

    def test_failure_feedback_is_separate_from_final_completion(self):
        run = {
            "id": 1,
            "name": "CI",
            "event": "pull_request",
            "head_sha": "sha",
            "html_url": "https://github.com/example/project/actions/runs/1",
            "run_attempt": 1,
            "created_at": "2026-10-10T00:00:00Z",
        }
        failed = job(1, "02", "07")
        failed["conclusion"] = "failure"
        failed["steps"] = [
            {
                "name": "gate",
                "conclusion": "failure",
                "started_at": "2026-10-10T00:00:03Z",
                "completed_at": "2026-10-10T00:00:05Z",
            }
        ]
        report = performance.summarize(run, [failed, job(2, "03", "12")])
        self.assertEqual(report["first_failed_step_completion_seconds"], 5)
        self.assertEqual(report["submission_to_checks_complete_seconds"], 12)

    def test_incomplete_jobs_are_not_comparable(self):
        with self.assertRaises(ValueError):
            performance.summarize({}, [{"status": "in_progress"}])

    def test_nested_native_spans_are_not_summed_or_repainted(self):
        log = "\n".join(
            [
                "1 : connect",
                "1 : connect DONE [1.1s]",
                "5 : load workspace: . DONE [50.1s]",
                "6 : ┆ loading type definitions DONE [50.1s]",
                "16 : ┆ [4.5s] | Prepared 6 packages in 1.92s",
                "16 : ┆ [4.5s] | Prepared 6 packages in 1.92s",
                '6 : | CI_TIMING {"phase":"check-list","seconds":0.14,"exit_code":3}',
            ]
        )
        report = performance.dagger_timings(log)
        self.assertEqual(len(report["native_spans"]), 2)
        self.assertEqual(len(report["uv_preparations_observed"]), 1)
        self.assertEqual(report["commands"][0]["exit_code"], 3)
        self.assertAlmostEqual(performance.duration("1m5s"), 65)

    def test_injected_marker_cannot_claim_unbounded_duration(self):
        with self.assertRaises(ValueError):
            performance.dagger_timings(
                'CI_TIMING {"phase":"all-checks","seconds":NaN,"exit_code":0}'
            )

    def test_collection_is_get_only_and_pagination_complete(self):
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            data = (
                [
                    {"total_count": 2, "jobs": [job(1, "01", "02")]},
                    {"total_count": 2, "jobs": [job(2, "01", "03")]},
                ]
                if "/jobs?" in args[-1]
                else [{"id": 1}]
            )
            return subprocess.CompletedProcess(
                args, 0, json.dumps(data), "secret-not-persisted"
            )

        run, jobs = performance.collect("example/project", 1, runner=runner)
        self.assertEqual(len(jobs), 2)
        self.assertTrue(
            all(args[args.index("--method") + 1] == "GET" for args in calls)
        )
        self.assertEqual(run["id"], 1)
        with self.assertRaises(ValueError):
            performance.collect("https://token@example", 1, runner=runner)


FAKE = r"""
import json,os,shutil,subprocess,sys
from pathlib import Path
args=sys.argv[1:]
if args[:2]==["--progress","plain"]: args=args[2:]
with open(os.environ["PERF_EVENTS"],"a",encoding="utf-8") as stream:
    stream.write(json.dumps({"args":args,"in_session":"DAGGER_SESSION_PORT" in os.environ})+"\n")
if args[0]=="version": raise SystemExit(0)
if args[0]=="run":
    assert args[1:4]==["--load-workspace-modules","--","bash"]
    env=os.environ.copy()
    env["DAGGER_SESSION_PORT"]="12345"
    env["DAGGER_SESSION_TOKEN"]="synthetic-session-token"
    result=subprocess.run([env["PERF_BASH"],*args[4:]],input=sys.stdin.read(),text=True,env=env)
    raise SystemExit(result.returncode)
assert "DAGGER_SESSION_PORT" in os.environ
if args[0]=="call":
    out=Path(args[args.index("--path")+1])
    out.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(os.environ["PERF_CI_HELPER"],out/"ci.py")
    sys.path.insert(0,str(Path(os.environ["PERF_CI_HELPER"]).parent))
    from pytest_report import summarize_pytest,markdown_summary
    code=int(os.environ["PERF_PYTEST_EXIT"])
    xml='<testsuite tests="1"><testcase name="test_real_gate"><failure message="assert 1 == 2"/></testcase></testsuite>' if code else '<testsuite tests="1"><testcase name="test_pass"/></testsuite>'
    report=summarize_pytest(xml,code,"")
    (out/"report.json").write_text(json.dumps(report),encoding="utf-8")
    (out/"exit-code.txt").write_text(str(code),encoding="ascii")
    (out/"summary.md").write_text(markdown_summary(report),encoding="utf-8")
    raise SystemExit(0)
if args[0]=="check":
    if "-l" in args:
        if os.environ["PERF_FAIL_LIST"]=="1": raise SystemExit(3)
        print("sample:format\nsample:lint\nsample:test")
    else:
        assert "sample:test" not in args[1:]
        raise SystemExit(int(os.environ["PERF_CHECK_EXIT"]))
else: raise SystemExit(29)
"""


class WorkflowExecutionTests(unittest.TestCase):
    def setUp(self):
        if os.name == "nt":
            self.skipTest("Ubuntu workflow subprocess contracts run in Linux CI")
        self.bash = shutil.which("bash")
        if not self.bash or not Path(self.bash).is_file():
            self.skipTest("Bash unavailable")

    def execute(
        self,
        directory,
        *,
        test_exit=0,
        check_exit=0,
        replacement="sample:test",
        fail_list=0,
        report=True,
    ):
        workflow = (ROOT / ".github/workflows/reusable-python-ci.yml").read_text(
            encoding="utf-8"
        )
        text = workflow.split("      - name: Run Dagger Checks\n", 1)[1].split(
            "\n      - name:", 1
        )[0]
        code = text.split("        run: |\n", 1)[1]
        code = "\n".join(line[10:] for line in code.splitlines()) + "\n"
        directory = Path(directory)
        script, fake, events = (
            directory / "workflow.sh",
            directory / "fake.py",
            directory / "events",
        )
        script.write_text(code, encoding="utf-8", newline="\n")
        fake.write_text(FAKE, encoding="utf-8")
        executable = directory / "dagger"
        executable.write_text(
            '#!/usr/bin/env bash\nexec "$PERF_PYTHON" "$PERF_FAKE" "$@"\n',
            encoding="utf-8",
            newline="\n",
        )
        executable.chmod(0o755)
        launcher = directory / "launch.sh"
        launcher.write_text(
            'export PATH="$PERF_BIN:$PATH"\nexec bash "$PERF_SCRIPT"\n',
            encoding="utf-8",
            newline="\n",
        )

        def posix(path):
            value = str(Path(path).resolve()).replace("\\", "/")
            return "/" + value[0].lower() + value[2:] if os.name == "nt" else value

        env = os.environ.copy()
        env.update(
            {
                "PERF_PYTHON": str(Path(sys.executable)),
                "PERF_FAKE": str(fake),
                "PERF_BASH": self.bash,
                "PERF_EVENTS": str(events),
                "PERF_BIN": posix(directory),
                "PERF_SCRIPT": posix(script),
                "PERF_CI_HELPER": str(ROOT / ".dagger/src/engineering_ci/pytest_ci.py"),
                "PERF_PYTEST_EXIT": str(test_exit),
                "PERF_CHECK_EXIT": str(check_exit),
                "PERF_FAIL_LIST": str(fail_list),
                "RUNNER_TEMP": posix(directory),
                "PYTEST_REPORT_FUNCTION": "pytest-results" if report else "",
                "PYTEST_CHECK": replacement if report else "",
                "PYTEST_REPORT_SCENARIO": "",
            }
        )
        result = subprocess.run(
            [self.bash, str(launcher)],
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        entries = [
            json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()
        ]
        return result, entries

    def test_reporting_runs_once_and_retains_all_other_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, entries = self.execute(tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sum(e["args"][0] == "run" for e in entries), 1)
            self.assertEqual(sum(e["args"][0] == "call" for e in entries), 1)
            actual = next(
                e["args"]
                for e in entries
                if e["args"][0] == "check" and "-l" not in e["args"]
            )
            self.assertEqual(set(actual[1:]), {"sample:format", "sample:lint"})
            self.assertTrue(
                all(
                    e["in_session"]
                    for e in entries
                    if e["args"][0] in {"check", "call"}
                )
            )

    def test_native_check_failure_survives_outer_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = self.execute(tmp, check_exit=17)
            self.assertEqual(result.returncode, 17)
            self.assertTrue((Path(tmp) / "pytest-report/report.json").is_file())

    def test_default_mode_runs_all_checks_and_preserves_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, entries = self.execute(tmp, report=False, check_exit=17)
            self.assertEqual(result.returncode, 17, result.stderr)
            self.assertFalse(any(e["args"][0] == "call" for e in entries))
            self.assertIn(["check"], [e["args"] for e in entries])

    def test_failed_test_report_does_not_release_the_quality_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = self.execute(tmp, test_exit=1)
            self.assertEqual(result.returncode, 0, result.stderr)
            gate = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / ".dagger/src/engineering_ci/pytest_ci.py"),
                    "gate",
                    str(Path(tmp) / "pytest-report"),
                ],
                capture_output=True,
            )
            self.assertEqual(gate.returncode, 1)

    def test_discovery_failure_and_bad_replacement_fail_closed(self):
        for kwargs in ({"fail_list": 1}, {"replacement": "other:test"}):
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as tmp:
                result, entries = self.execute(tmp, **kwargs)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(
                    any(
                        e["args"][0] == "check" and "-l" not in e["args"]
                        for e in entries
                    )
                )
