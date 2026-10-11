"""Run engineering-ci's existing PR checks; historical reports never skip execution."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from ._paths import ROOT
from .pipeline import PIPELINE
from .toolchain import PYTHON_VERSION, RUFF_VERSION, UV_VERSION, ruff_command

CI_STEPS = {
    name: spec["ci_step"]
    for name, spec in PIPELINE["checks"].items()
    if "ci_step" in spec
}
RUFF_SCOPE = tuple(PIPELINE["scopes"]["ruff"])
PR_CI_JOBS = tuple(
    dict.fromkeys(
        spec["ci_job"] for spec in PIPELINE["checks"].values() if "ci_job" in spec
    )
)
PR_CHECKS = tuple(PIPELINE["profiles"]["pr"])
GROUPS = {name: tuple(values) for name, values in PIPELINE["profiles"].items()}
SCOPES = {
    name: tuple(PIPELINE["scopes"][spec["scope"]])
    for name, spec in PIPELINE["checks"].items()
}
COMMON_INPUTS = tuple(PIPELINE["inputs"]["common"])
IGNORED_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv", ".git"}
VERSION = re.compile(r"\b\d+\.\d+(?:\.\d+)?(?:[a-zA-Z0-9.+-]*)")


class PreconditionError(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason
        super().__init__(reason)


def aria2_executable(root=ROOT, *, which=shutil.which, environ=None):
    """Resolve the optional integration-test backend using existing download settings."""
    from .execution.config import DownloadConfigurationError, load_download_config

    environ = os.environ if environ is None else environ
    try:
        configured = (
            environ.get("ARIA2C")
            or load_download_config(environ=environ, project_root=root).executable
        )
    except DownloadConfigurationError as error:
        raise PreconditionError(
            "environment_error", "Invalid optional download configuration"
        ) from error
    path = which(configured)
    return path if path and Path(path).is_file() else None


def selected(selectors):
    result = []
    for selector in selectors:
        names = GROUPS.get(selector, (selector,))
        for name in names:
            if name not in PR_CHECKS:
                raise ValueError(f"Unsupported check: {selector}")
            if name not in result:
                result.append(name)
    return result


def command(name, coverage=False, junit="core-junit.xml"):
    if name == "ruff-lint":
        return [
            *ruff_command(),
            "check",
            "--isolated",
            "--target-version",
            PIPELINE["arguments"]["ruff_target"],
            *RUFF_SCOPE,
        ]
    if name == "ruff-format":
        return [
            *ruff_command(),
            "format",
            "--isolated",
            "--target-version",
            PIPELINE["arguments"]["ruff_target"],
            "--check",
            *RUFF_SCOPE,
        ]
    if name == "pytest":
        args = [
            "uv",
            "run",
            "--locked",
            "--group",
            "dev",
            "--python",
            PYTHON_VERSION,
            "pytest",
            *PIPELINE["arguments"]["pytest"],
            f"--junitxml={junit}",
        ]
        if coverage:
            args += [
                "--cov=src/engineering_ci",
                "--cov=.dagger/src/engineering_ci",
                "--cov-report=xml:core-coverage.xml",
                "--cov-report=json:core-coverage.json",
            ]
        return args
    if name == "version":
        return [sys.executable, "-m", "engineering_ci.cli", "version", "check"]
    if name == "lock":
        return ["uv", "lock", "--project", ".", "--check", "--python", PYTHON_VERSION]
    raise ValueError(f"Unsupported check: {name}")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def snapshot(root, name):
    """Content identifies inputs; file observations also catch change-and-restore."""
    paths = set()
    for scope in (*COMMON_INPUTS, *SCOPES[name]):
        start = root / scope
        if not start.is_dir():
            paths.add(start)
            continue
        for directory, dirs, files in os.walk(start, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
            if any((Path(directory) / d).is_symlink() for d in dirs):
                raise PreconditionError(
                    "environment_error",
                    "Symlink directories in check inputs require an explicit checkout",
                )
            paths.update(
                Path(directory) / file
                for file in files
                if not file.endswith((".pyc", ".pyo"))
            )
    content, observations = [], []
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        if not path.exists():
            content.append((relative, None))
            observations.append((relative, None))
            continue
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()

        def metadata(info):
            return (
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
                info.st_ino,
                info.st_mode,
            )

        if metadata(before) != metadata(after):
            raise PreconditionError(
                "stale", "Check inputs changed while fingerprinting"
            )
        content.append((relative, after.st_mode, hashlib.sha256(data).hexdigest()))
        observations.append((relative, *metadata(after)))
    return {"sha256": digest(content), "file_count": len(content)}, digest(observations)


def environment_identity(environ):
    # Only a digest is persisted: index/authentication values are never serialized.
    relevant = {
        key: value
        for key, value in environ.items()
        if key.startswith(("UV_", "PYTEST_", "PYTHON", "RUFF_"))
        or key in {"PATH", "ARIA2C"}
    }
    # uv's user/system configuration can change without an environment value changing.
    if environ.get("UV_CONFIG_FILE"):
        locations = [Path(environ["UV_CONFIG_FILE"])]
    elif os.name == "nt":
        locations = [
            Path(environ.get("APPDATA", Path.home() / "AppData/Roaming"))
            / "uv/uv.toml",
            Path(environ.get("PROGRAMDATA", "C:/ProgramData")) / "uv/uv.toml",
        ]
    else:
        locations = [
            Path(environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            / "uv/uv.toml",
            Path("/etc/uv/uv.toml"),
        ]
    configurations = [
        (
            str(path),
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
        )
        for path in locations
    ]
    return {
        "platform": platform.system(),
        "machine": platform.machine(),
        "configuration_sha256": digest([relevant, configurations]),
    }


def execution_environment(root, environ):
    result = dict(os.environ if environ is None else environ)
    source = str(root.resolve() / "src")
    paths = [
        part
        for part in result.get("PYTHONPATH", "").split(os.pathsep)
        if part and part != source
    ]
    result["PYTHONPATH"] = os.pathsep.join([source, *paths])
    return result


def probe(args, root, run, environ):
    result = run(
        args, cwd=root, env=environ, text=True, capture_output=True, check=False
    )
    if result.returncode:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        raise PreconditionError(
            "environment_error",
            f"Unable to inspect {Path(args[0]).name} tool environment",
        )
    return result.stdout


def tool(path, root, run, environ):
    text = probe([path, "--version"], root, run, environ)
    match = VERSION.search(text)
    if not match:
        raise PreconditionError(
            "environment_error", "Tool did not provide a recognizable version"
        )
    return {"executable": path, "version": match.group()}


def preflight(name, root, run, which, environ):
    tools = {
        "bootstrap_python": {
            "executable": sys.executable,
            "version": platform.python_version(),
        }
    }
    if name == "pytest":
        for key in (
            "PYTEST_ADDOPTS",
            "PYTEST_PLUGINS",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        ):
            if environ.get(key):
                raise PreconditionError(
                    "environment_error", f"Unset {key} for the PR pytest definition"
                )
    spec = PIPELINE["checks"][name]
    required = spec["required_tools"] + (spec["linux_tools"] if os.name != "nt" else [])
    for dependency in required:
        path = which(dependency)
        if not path or (
            dependency in {"git", "gh", "bash"} and not Path(path).is_file()
        ):
            raise PreconditionError("missing_tool", f"{dependency} is required")
        tools[dependency] = tool(path, root, run, environ)
    uv = tools.get("uv", {}).get("executable")
    if uv and tools["uv"]["version"] != UV_VERSION:
        raise PreconditionError(
            "environment_error", f"PR parity requires uv {UV_VERSION}"
        )
    if name.startswith("ruff-"):
        uvx = tools["uvx"]["executable"]
        args = ruff_command("--version")
        args[0] = uvx
        text = probe(args, root, run, environ)
        if not re.fullmatch(rf"ruff {re.escape(RUFF_VERSION)}\s*", text):
            raise PreconditionError(
                "environment_error", "Pinned Ruff version could not be verified"
            )
        tools["ruff"] = {"executable": uvx, "version": RUFF_VERSION}
    if name == "pytest":
        if "aria2" in spec["optional_backends"]:
            optional = aria2_executable(root, which=which, environ=environ)
            tools[PIPELINE["optional_backends"]["aria2"]["tool"]] = (
                tool(optional, root, run, environ)
                if optional
                else {"status": "not_enabled"}
            )
        args = [
            uv,
            "run",
            "--locked",
            "--group",
            "dev",
            "--python",
            PYTHON_VERSION,
            "python",
            "-c",
            "import json,platform,importlib.metadata as m; print(json.dumps({'python':platform.python_version(),'pytest':m.version('pytest'),'pytest-cov':m.version('pytest-cov')}))",
        ]
        try:
            resolved = json.loads(probe(args, root, run, environ))
            if (
                set(resolved) != {"python", "pytest", "pytest-cov"}
                or not resolved["python"].startswith(PYTHON_VERSION + ".")
                or not all(VERSION.fullmatch(v) for v in resolved.values())
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise PreconditionError(
                "environment_error",
                "Locked Python/pytest environment could not be verified",
            ) from None
        tools.update({key: {"version": value} for key, value in resolved.items()})
    return tools


def identity(root, name, parameters, tools, environ):
    inputs, observations = snapshot(root, name)
    value = {
        "root": str(root),
        "name": name,
        "command": command(name, **parameters),
        "parameters": parameters,
        "tools": tools,
        "inputs": inputs,
        "scope": list(dict.fromkeys((*COMMON_INPUTS, *SCOPES[name]))),
        "environment": environment_identity(environ),
    }
    value["sha256"] = digest(value)
    return value, observations


def pytest_counts(root, junit, exit_code):
    """Read the existing JUnit through the existing report parser, without rerunning pytest."""
    spec = importlib.util.spec_from_file_location(
        "checks_pytest_report", root / ".dagger/src/engineering_ci/pytest_report.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = root / junit
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("JUnit exceeds the existing report reader limit")
    return module.summarize_pytest(path.read_text(encoding="utf-8"), exit_code, "")[
        "counts"
    ]


def optional_pytest_skips(root, junit):
    """Read explicit optional aria2 skips from JUnit; other skips remain incomplete."""
    path = root / junit
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("JUnit exceeds the existing report reader limit")
    tree = ET.parse(path)
    return [
        f"{case.get('classname', '')}::{case.get('name', '')}"
        for case in tree.iter("testcase")
        if case.find("skipped") is not None
        and any(
            prop.get("name") == PIPELINE["optional_backends"]["aria2"]["junit_property"]
            and prop.get("value")
            == PIPELINE["optional_backends"]["aria2"]["junit_value"]
            for prop in case.findall("properties/property")
        )
    ]


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def report_path(path, root):
    path = path.resolve()
    scopes = set(COMMON_INPUTS)
    for values in SCOPES.values():
        scopes.update(values)
    for scope in scopes:
        source = (root / scope).resolve()
        if path == source or (source.is_dir() and path.is_relative_to(source)):
            raise ValueError("Report destination must not overwrite check inputs")
    return path


def _process_exit_code(code):
    return code if code >= 0 else 128 - code


def execute(
    selectors,
    *,
    root=ROOT,
    coverage=False,
    junit="core-junit.xml",
    output=None,
    run=subprocess.run,
    which=shutil.which,
    environ=None,
):
    root = root.resolve()
    junit_path = report_path(root / junit, root)
    if output:
        output = report_path(output, root)
        if output == junit_path or output in {
            root / "core-coverage.xml",
            root / "core-coverage.json",
        }:
            raise ValueError("Local JSON must not overwrite JUnit or Coverage evidence")
    environ = execution_environment(root, environ)
    names = selected(selectors)
    parameters = {"coverage": coverage, "junit": junit}
    report = {
        "schema_version": 1,
        "profile": "+".join(selectors),
        "selectors": selectors,
        "checks": [
            {
                "name": name,
                "status": "not_run",
                "executed": False,
                "exit_code": None,
                "input_stable": False,
                "passed_for_current_input": False,
                "identity": None,
            }
            for name in names
        ],
        "overall_status": "not_run",
        "exit_code": 3,
    }
    codes = []
    completed_observations = {}

    def save():
        if output:
            try:
                atomic_json(output, report)
            except OSError:
                report["report_error"] = "Local JSON could not be written"
                print(report["report_error"], file=sys.stderr)

    for entry in report["checks"]:
        name = entry["name"]
        launching = False
        try:
            # Observe inputs before preparation as well as before the real check.
            initial, initial_observations = snapshot(root, name)
            tools = preflight(name, root, run, which, environ)
            before, observations = identity(root, name, parameters, tools, environ)
            if initial != before["inputs"] or initial_observations != observations:
                raise PreconditionError(
                    "stale", "Inputs changed while preparing the check"
                )
            entry["identity"] = before
            junit_path = root / junit
            old_xml = (
                junit_path.stat() if name == "pytest" and junit_path.exists() else None
            )
            print(f"[{name}] {' '.join(before['command'])}", flush=True)
            entry["executed"] = True
            launching = True
            result = run(before["command"], cwd=root, env=environ, check=False)
            launching = False
            entry["exit_code"] = result.returncode
            code = _process_exit_code(result.returncode)
            entry["status"] = "passed" if code == 0 else "failed"
            after_tools = preflight(name, root, run, which, environ)
            after, after_observations = identity(
                root, name, parameters, after_tools, environ
            )
            stable = before == after and observations == after_observations
            entry["input_stable"] = stable
            if code == 0 and not stable:
                entry.update(
                    status="stale",
                    reason="Inputs or tool environment changed during execution",
                )
                code = 3
            if name == "pytest" and code == 0:
                new_xml = junit_path.stat()
                if old_xml and (old_xml.st_mtime_ns, old_xml.st_ctime_ns) == (
                    new_xml.st_mtime_ns,
                    new_xml.st_ctime_ns,
                ):
                    raise PreconditionError(
                        "report_unavailable", "pytest did not replace its JUnit report"
                    )
                counts = pytest_counts(root, junit, result.returncode)
                if (
                    counts is None
                    or not counts["tests"]
                    or not counts["passed"]
                    or counts["errors"]
                    or counts["failures"]
                ):
                    raise PreconditionError(
                        "report_unavailable",
                        "JUnit does not support a complete successful execution",
                    )
                entry["counts"] = counts
                if counts["skipped"]:
                    optional = optional_pytest_skips(root, junit)
                    if optional:
                        entry["optional_tests"] = {
                            "aria2": {"status": "not_enabled", "tests": optional}
                        }
                        print(
                            f"[pytest] Optional aria2 integration: not enabled ({len(optional)} tests skipped)"
                        )
                    if counts["skipped"] != len(optional):
                        entry.update(
                            status="incomplete",
                            reason="Required tests were skipped; inspect JUnit and the execution environment",
                        )
                        code = 3
            completed_observations[name] = after_observations
            entry["passed_for_current_input"] = entry["status"] == "passed" and stable
            codes.append(code)
        except KeyboardInterrupt:
            entry.update(status="interrupted", reason="Execution interrupted")
            codes.append(130)
            break
        except (PreconditionError, OSError, ValueError) as error:
            status = (
                error.status
                if isinstance(error, PreconditionError)
                else "environment_error"
            )
            if launching and isinstance(error, OSError):
                entry["executed"] = False
                if isinstance(error, FileNotFoundError):
                    status = "missing_tool"
            reason = (
                error.reason
                if isinstance(error, PreconditionError)
                else f"{type(error).__name__}: unable to execute or read check evidence"
            )
            entry.update(
                status="failed" if entry["exit_code"] else status,
                reason=reason,
                passed_for_current_input=False,
            )
            codes.append(
                _process_exit_code(entry["exit_code"])
                if entry["exit_code"]
                else (127 if status == "missing_tool" else 3)
            )
        print(
            f"[{name}] {entry['status']} (tool exit: {entry['exit_code']})", flush=True
        )
        if entry.get("reason"):
            print(f"[{name}] {entry['reason']}", flush=True)
        save()
    # A later check must not make an earlier success stale while the profile is running.
    for entry in report["checks"]:
        if not entry["passed_for_current_input"]:
            continue
        try:
            tools = preflight(entry["name"], root, run, which, environ)
            current, observations = identity(
                root, entry["name"], parameters, tools, environ
            )
            fresh = (
                current == entry["identity"]
                and observations == completed_observations[entry["name"]]
            )
        except (PreconditionError, OSError, ValueError):
            fresh = False
        if not fresh:
            entry.update(
                status="stale",
                passed_for_current_input=False,
                reason="Inputs or tools changed before the requested profile completed",
            )
            codes.append(3)
    code = next((code for code in codes if code), 0)
    report.update(
        overall_status="passed"
        if code == 0
        and all(entry["passed_for_current_input"] for entry in report["checks"])
        else "failed",
        exit_code=code,
    )
    save()
    if report.get("report_error") and code == 0:
        code = 3
        report.update(overall_status="failed", exit_code=code)
    return report, code


def inspect(path, *, root=ROOT, run=subprocess.run, which=shutil.which, environ=None):
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Local report exceeds 1 MiB")
    saved = json.loads(path.read_text(encoding="utf-8"))
    if (
        type(saved.get("schema_version")) is not int
        or saved.get("schema_version") != 1
        or not isinstance(saved.get("selectors"), list)
        or not saved["selectors"]
        or [item["name"] for item in saved["checks"]] != selected(saved["selectors"])
    ):
        raise ValueError("Invalid local check report")
    if saved["profile"] != "+".join(saved["selectors"]):
        raise ValueError("Invalid local check profile")
    result = {"schema_version": 1, "profile": saved["profile"], "checks": []}
    environ = execution_environment(root, environ)
    for entry in saved["checks"]:
        current = {
            "name": entry["name"],
            "status": entry["status"],
            "freshness": "not_run",
            "passed_for_current_input": False,
        }
        if entry.get("reason"):
            current["reason"] = entry["reason"]
        previous = entry.get("identity")
        if entry.get("executed") is True and previous:
            try:
                parameters = previous["parameters"]
                if (
                    set(parameters) != {"coverage", "junit"}
                    or type(parameters["coverage"]) is not bool
                    or not isinstance(parameters["junit"], str)
                ):
                    raise ValueError("Invalid execution parameters")
                inputs, observations = snapshot(root.resolve(), entry["name"])
                if inputs != previous["inputs"]:
                    current["freshness"] = "stale"
                else:
                    tools = preflight(entry["name"], root, run, which, environ)
                    now, after_observations = identity(
                        root.resolve(), entry["name"], parameters, tools, environ
                    )
                    current["freshness"] = (
                        "current"
                        if now == previous and observations == after_observations
                        else "stale"
                    )
                current["passed_for_current_input"] = (
                    current["freshness"] == "current"
                    and entry.get("input_stable") is True
                    and entry["status"] == "passed"
                    and type(entry["exit_code"]) is int
                    and entry["exit_code"] == 0
                )
            except (PreconditionError, OSError):
                current["freshness"] = "unavailable"
        result["checks"].append(current)
    result["overall_status"] = (
        "passed"
        if all(entry["passed_for_current_input"] for entry in result["checks"])
        else "failed"
    )
    return result, 0 if result["overall_status"] == "passed" else 3


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "checks",
        nargs="+",
        help="pr, ruff, tests, version, lock, ruff-lint, ruff-format; inspect REPORT; toolchain",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Export the existing core Coverage XML and JSON",
    )
    parser.add_argument("--junit", default="core-junit.xml")
    parser.add_argument(
        "--json", type=Path, help="Atomically write local results, including failures"
    )
    parser.add_argument(
        "--github-output", type=Path, help="toolchain only: export setup-uv inputs"
    )
    args = parser.parse_args(argv)
    try:
        if args.json:
            args.json = report_path(args.json, ROOT)
        if args.checks == ["toolchain"]:
            versions = {
                "uv_version": UV_VERSION,
                "python_version": PYTHON_VERSION,
                "ruff_version": RUFF_VERSION,
            }
            if args.github_output:
                with args.github_output.open("a", encoding="utf-8") as stream:
                    stream.writelines(
                        f"{key}={value}\n" for key, value in versions.items()
                    )
            print(json.dumps(versions, sort_keys=True))
            return 0
        if args.github_output:
            raise ValueError("--github-output requires toolchain")
        if args.checks[0] == "inspect":
            if len(args.checks) != 2:
                raise ValueError("inspect requires one report path")
            if args.json and args.json.resolve() == Path(args.checks[1]).resolve():
                raise ValueError("Inspection must not overwrite the original report")
            result, code = inspect(Path(args.checks[1]))
            print(json.dumps(result, indent=2, sort_keys=True))
            if args.json:
                atomic_json(args.json, result)
            return code
        selected(args.checks)
        _, code = execute(
            args.checks, coverage=args.coverage, junit=args.junit, output=args.json
        )
        return code
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"checks: {type(error).__name__}: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
