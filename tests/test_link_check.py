"""Policy tests are doubles; native cache evidence is recorded separately."""

import ast
import asyncio
import base64
import dataclasses
import importlib.util
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / ".dagger/src/engineering_ci_module"
spec = importlib.util.spec_from_file_location("link_check", MODULE / "link_check.py")
link_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(link_check)


@dataclasses.dataclass(frozen=True)
class ContainerDouble:
    env: tuple = ()

    def with_directory(self, path, source):
        return self

    def with_workdir(self, path):
        return self

    def with_env_variable(self, name, value):
        return dataclasses.replace(self, env=(*self.env, (name, value)))


class DirectoryDouble:
    def __init__(self):
        self.excluded = None

    def with_directory(self, path, source, *, exclude):
        self.excluded = exclude
        return self

    async def digest(self):
        return "controlled-source-digest"


def test_online_observation_changes_with_remote_state_but_offline_reuses(monkeypatch):
    directory = DirectoryDouble()
    client = type("ClientDouble", (), {"directory": lambda _: directory})()
    remote = {"exit": 0}
    cached = {}
    executions = []
    ticks = iter([1234567890000000001, 1234567890000000002])
    monkeypatch.setattr(link_check.time, "time_ns", lambda: next(ticks))

    async def capture(container, argv):
        key = (container.env, tuple(argv))
        if key not in cached:
            executions.append(key)
            cached[key] = {
                "exit_code": 0 if "--offline" in argv else remote["exit"],
                "stdout": "original stdout",
                "stderr": "original stderr",
                "process_started_at": f"execution-{len(executions)}",
                "process_finished_at": f"execution-{len(executions)}-end",
            }
        return dict(cached[key])

    async def run(args):
        return await link_check.run(
            client,
            ContainerDouble(),
            directory,
            args,
            capture=capture,
            source_noise=(".venv",),
        )

    first = asyncio.run(run(["--cache", "docs/link.md"]))
    remote["exit"] = 2
    second = asyncio.run(run(["--cache", "docs/link.md"]))
    offline = asyncio.run(run(["--offline", "docs/link.md"]))
    repeated_offline = asyncio.run(run(["--offline", "docs/link.md"]))
    assert (first["exit_code"], second["exit_code"]) == (0, 2)
    assert (
        first["observation"]["requested_at_unix_ns"]
        != second["observation"]["requested_at_unix_ns"]
    )
    assert first["stderr"] == second["stderr"] == "original stderr"
    assert offline == repeated_offline
    assert len(executions) == 3
    assert executions[-1][0] == ()
    assert directory.excluded == [".venv"]
    assert "--cache=false" in first["observation"]["argv"]


@pytest.mark.parametrize(
    ("args", "offline"),
    [
        (["--offline", "docs/a.md"], True),
        (["--offline=true", "docs/a.md"], True),
        (["--offline=false", "docs/a.md"], False),
        (["--offline", "--offline=false", "docs/a.md"], False),
        (["--", "--offline"], False),
        (["docs/a.md"], False),
    ],
)
def test_only_explicit_offline_cli_mode_allows_deterministic_cache(args, offline):
    assert link_check.arguments(args)[0] is offline


def test_online_disables_response_cache_without_changing_positional_targets():
    offline, args = link_check.arguments(
        ["--config", "lychee.toml", "--cache=true", "--", "--cache", "a.md"]
    )
    assert not offline
    assert args == ["--cache=false", "--config", "lychee.toml", "--", "--cache", "a.md"]


def test_invalid_cache_option_is_left_for_native_diagnosis():
    assert "--cache=invalid" in link_check.arguments(["--cache=invalid", "a.md"])[1]


@pytest.mark.parametrize("code", [0, 1, 128, 191, 255])
def test_capture_preserves_exit_bytes_and_actual_process_times(code):
    module = ast.parse((MODULE / "main.py").read_text(encoding="utf-8"))
    script = next(
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            getattr(target, "id", None) == "_EXEC_CAPTURE_SCRIPT"
            for target in node.targets
        )
    )
    run = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            script,
            sys.executable,
            "-I",
            "-c",
            f"import os,sys;os.write(1,b'out\\x00\\xff');os.write(2,b'err\\xfe');sys.exit({code})",
        ],
        capture_output=True,
        check=True,
    )
    payload = json.loads(run.stdout)
    assert payload["exit_code"] == code
    assert base64.b64decode(payload["stdout_b64"]) == b"out\x00\xff"
    assert base64.b64decode(payload["stderr_b64"]) == b"err\xfe"
    assert datetime.fromisoformat(
        payload["process_started_at"]
    ) <= datetime.fromisoformat(payload["process_finished_at"])


def test_public_plan_entrypoints_cannot_hide_network_or_command_work_in_function_cache():
    module = ast.parse((MODULE / "main.py").read_text(encoding="utf-8"))
    root = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "EngineeringCi"
    )
    for name in ("plan_execution", "execute_plan", "fixture_plan_execution"):
        method = next(node for node in root.body if getattr(node, "name", None) == name)
        assert any(
            isinstance(decorator, ast.Call)
            and getattr(decorator.func, "id", None) == "function"
            and any(
                keyword.arg == "cache" and ast.literal_eval(keyword.value) == "never"
                for keyword in decorator.keywords
            )
            for decorator in method.decorator_list
        ), name


def test_reusable_source_functions_scope_ignore_while_dual_view_and_plan_keep_raw_source():
    module = ast.parse((MODULE / "main.py").read_text(encoding="utf-8"))
    alias = next(
        node
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            getattr(target, "id", None) == "_FilteredSource" for target in node.targets
        )
    )
    assert "Ignore(tooling.SOURCE_NOISE)" in ast.unparse(alias.value)

    root = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "EngineeringCi"
    )
    methods = {node.name: node for node in root.body if hasattr(node, "name")}
    expected_annotations = {
        "_FilteredSource": (
            "python_lint",
            "python_format_check",
            "python_vulture",
            "tach_check",
            "lychee_check",
            "python_pyright",
            "python_deptry",
            "python_pylint",
            "python_test_artifacts",
            "python_test",
            "python_test_report",
            "python_test_matrix",
            "python_test_matrix_report",
            "python_test_matrix_artifacts",
        ),
        "dagger.Directory": (
            "plan_execution",
            "execute_plan",
            "fixture_plan_execution",
            "actionlint_workflows",
            "shellcheck_scripts",
            "zizmor_workflows",
            "analyze_script_scope",
            "weaver_artifacts",
            "weaver_report",
            "weaver_enforce",
        ),
    }
    for expected, names in expected_annotations.items():
        for name in names:
            source_arg = next(
                arg for arg in methods[name].args.args if arg.arg == "source"
            )
            assert ast.unparse(source_arg.annotation) == expected, name
