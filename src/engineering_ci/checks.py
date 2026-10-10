#!/usr/bin/env python3
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
from pathlib import Path

from ._paths import ROOT
from .toolchain import PYTHON_VERSION, RUFF_VERSION, UV_VERSION, ruff_command

CI_STEPS = {
    "ruff-lint": "Ruff Lint",
    "ruff-format": "Ruff Format Check",
    "pytest": "Run all core unit and process tests",
    "version": "Validate release metadata and root lock consistency",
}
RUFF_SCOPE = (
    "tests/fixtures/python-app",
    "scripts/release_version.py",
    "scripts/manual_release.py",
    "scripts/release_manifest.py",
    "scripts/checks.py",
    "scripts/ghwf.py",
    "src/engineering_ci/__init__.py",
    "src/engineering_ci/checks.py",
    "src/engineering_ci/toolchain.py",
    "src/engineering_ci/_paths.py",
    "src/engineering_ci/cli/__init__.py",
    "src/engineering_ci/cli/__main__.py",
    "src/engineering_ci/github_cli.py",
    "src/engineering_ci/cli",
    "src/engineering_ci/release",
    "tests/test_release_version.py",
    "tests/test_manual_release.py",
    "tests/test_release_manifest.py",
    "tests/test_release_transport.py",
    "tests/test_checks.py",
    "tests/test_unified_cli.py",
)
PR_CHECKS = ("ruff-lint", "ruff-format", "pytest", "version", "lock")
GROUPS = {"pr": PR_CHECKS, "ruff": PR_CHECKS[:2], "tests": ("pytest",)}
SCOPES = {
    "ruff-lint": RUFF_SCOPE,
    "ruff-format": RUFF_SCOPE,
    "pytest": (
        "tests",
        "src",
        "scripts",
        ".dagger/src",
        ".python-version",
        ".github/workflows",
        ".dagger/pyproject.toml",
        ".dagger/uv.lock",
        "dagger.json",
        ".dagger/dagger.json",
        "docs/release-manifest.schema.json",
    ),
    "version": (
        "scripts/release_version.py",
        "src/engineering_ci/release/version.py",
        "src/engineering_ci/release/__init__.py",
        ".dagger/pyproject.toml",
        ".dagger/uv.lock",
    ),
    "lock": (".python-version", "uv.toml"),
}
COMMON_INPUTS = (
    "scripts/checks.py",
    "scripts/ghwf.py",
    "src/engineering_ci/__init__.py",
    "src/engineering_ci/checks.py",
    "src/engineering_ci/toolchain.py",
    "src/engineering_ci/_paths.py",
    "src/engineering_ci/cli/__init__.py",
    "src/engineering_ci/cli/__main__.py",
    ".github/workflows/ci.yml",
    "pyproject.toml",
    "uv.lock",
    "conftest.py",
    "pytest.ini",
    "setup.cfg",
    "tox.ini",
    "uv.toml",
)
IGNORED_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv", ".git"}
VERSION = re.compile(r"\b\d+\.\d+(?:\.\d+)?(?:[a-zA-Z0-9.+-]*)")


class PreconditionError(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason
        super().__init__(reason)


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
            "py311",
            *RUFF_SCOPE,
        ]
    if name == "ruff-format":
        return [
            *ruff_command(),
            "format",
            "--isolated",
            "--target-version",
            "py311",
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
            "-q",
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
            return (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino)

        if metadata(before) != metadata(after):
            raise PreconditionError(
                "stale", "Check inputs changed while fingerprinting"
            )
        content.append((relative, hashlib.sha256(data).hexdigest()))
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
    if name == "version":
        return tools
    uv = which("uv")
    if not uv:
        raise PreconditionError("missing_tool", "uv is required")
    tools["uv"] = tool(uv, root, run, environ)
    if tools["uv"]["version"] != UV_VERSION:
        raise PreconditionError(
            "environment_error", f"PR parity requires uv {UV_VERSION}"
        )
    if name.startswith("ruff-"):
        uvx = which("uvx")
        if not uvx:
            raise PreconditionError("missing_tool", "uvx is required")
        args = ruff_command("--version")
        args[0] = uvx
        text = probe(args, root, run, environ)
        if not re.fullmatch(rf"ruff {re.escape(RUFF_VERSION)}\s*", text):
            raise PreconditionError(
                "environment_error", "Pinned Ruff version could not be verified"
            )
        tools["ruff"] = {"executable": uvx, "version": RUFF_VERSION}
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
        for dependency in ("aria2c", "git", "gh"):
            path = environ.get("ARIA2C") if dependency == "aria2c" else None
            path = path or which(dependency)
            if not path or not Path(path).is_file():
                raise PreconditionError(
                    "missing_tool",
                    f"{dependency} is required; ARIA2C must name an executable file",
                )
            tools[dependency] = tool(path, root, run, environ)
        if os.name != "nt":
            bash = which("bash")
            if not bash:
                raise PreconditionError(
                    "missing_tool", "bash is required for Linux process tests"
                )
            tools["bash"] = tool(bash, root, run, environ)
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
            result = run(before["command"], cwd=root, env=environ, check=False)
            entry["exit_code"] = result.returncode
            code = (
                result.returncode if result.returncode >= 0 else 128 - result.returncode
            )
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
                    or counts["errors"]
                    or counts["failures"]
                ):
                    raise PreconditionError(
                        "report_unavailable",
                        "JUnit does not support a complete successful execution",
                    )
                entry["counts"] = counts
                if counts["skipped"]:
                    entry.update(
                        status="incomplete",
                        reason="Tests were skipped; use the Linux CI environment (WSL2 on Windows) for complete parity",
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
            codes.append(entry["exit_code"] or (127 if status == "missing_tool" else 3))
        print(
            f"[{name}] {entry['status']} (tool exit: {entry['exit_code']})", flush=True
        )
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
