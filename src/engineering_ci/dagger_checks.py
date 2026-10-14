"""Bind Dagger's Linux check evidence to the original checkout and shared Pipeline."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path, PurePosixPath

from . import checks
from .checks_container import OMITTED_INPUTS

FACT_FIELDS = (
    "status",
    "executed",
    "exit_code",
    "input_stable",
    "execution_status",
    "completeness",
    "counts",
    "optional_tests",
)

GIT_SECRET_ENV = "ENGINEERING_CI_READ_TOKEN"


def git_secret_options(root, names, run, which, environ):
    """Supply an in-memory credential only for the pinned, approved private source."""
    if "pytest" not in names:
        return []
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    source = project.get("tool", {}).get("uv", {}).get("sources", {}).get("vesryn")
    if source is None:
        return []
    if (
        not isinstance(source, dict)
        or source.get("git") != "https://github.com/dymzz/vesryn.git"
        or not re.fullmatch(r"[0-9a-f]{40}", str(source.get("rev", "")))
    ):
        raise checks.PreconditionError(
            "environment_error",
            "Private Harness source must use the approved repository and a pinned commit",
        )
    if not environ.get(GIT_SECRET_ENV):
        executable = which("gh")
        if not executable:
            raise checks.PreconditionError(
                "missing_tool",
                "Private dependency installation requires gh or ENGINEERING_CI_READ_TOKEN",
            )
        try:
            result = run(
                [executable, "auth", "token", "--hostname", "github.com"],
                cwd=root,
                env=environ,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise checks.PreconditionError(
                "environment_error",
                "Unable to read the existing gh credential for private dependency installation",
            ) from None
        token = result.stdout.strip() if result.returncode == 0 else ""
        if not token or any(character.isspace() for character in token):
            raise checks.PreconditionError(
                "environment_error",
                "Existing gh authentication cannot supply the private dependency credential",
            )
        environ[GIT_SECRET_ENV] = token
    return ["--git-token", f"env:{GIT_SECRET_ENV}"]


def request(selectors, coverage, junit):
    checks.selected(selectors)
    path = PurePosixPath(junit)
    if path.is_absolute() or ".." in path.parts or "\\" in junit or ":" in junit:
        raise ValueError("Dagger JUnit must be a relative repository path")
    return {"selectors": selectors, "coverage": coverage, "junit": junit}


def source_modes(root, run, environ):
    """Git execute bits are explicit inputs when the host is Windows."""
    result = run(
        ["git", "ls-files", "--stage", "-z"],
        cwd=root,
        env=environ,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode:
        raise checks.PreconditionError(
            "environment_error", "Unable to inspect Git input modes in this checkout"
        )
    paths = []
    for record in result.stdout.split("\0"):
        if not record:
            continue
        header, path = record.split("\t", 1)
        mode, _, stage = header.split()
        if stage != "0" or mode in {"120000", "160000"}:
            raise checks.PreconditionError(
                "environment_error",
                "Resolve conflicts, symlinks or submodules before container checks",
            )
        if (
            mode == "100755"
            and (root / path).is_file()
            and not path.startswith(".dagger/sdk/")
        ):
            paths.append(path)
    return sorted(paths)


def invoke(root, args, run, environ, *, capture=False, timeout=None):
    try:
        result = run(
            args,
            cwd=root,
            env=environ,
            check=False,
            **({"timeout": timeout} if timeout else {}),
            **(
                {"capture_output": True, "text": True, "encoding": "utf-8"}
                if capture
                else {}
            ),
        )
    except subprocess.TimeoutExpired:
        raise checks.PreconditionError(
            "environment_error",
            "Dagger tool probe timed out; inspect the CLI environment",
        ) from None
    if result.returncode:
        if capture and result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        raise checks.PreconditionError(
            "environment_error",
            f"Dagger operation failed (exit {checks._process_exit_code(result.returncode)}); inspect the execution output",
        )
    return result


def prepare(root, names, parameters, run, which, environ):
    """Probe the actual Engine, prepared container and locked tools; no test execution."""
    executable = which("dagger")
    if not executable or not Path(executable).is_file():
        raise checks.PreconditionError(
            "missing_tool",
            "Dagger is required for Linux container checks; install it or explicitly select native",
        )
    from .toolchain import dagger_cli_version

    expected = json.loads((root / "dagger.json").read_text(encoding="utf-8"))[
        "engineVersion"
    ].removeprefix("v")
    environ.setdefault(
        "DAGGER_ENGINE", f"image://registry.dagger.io/engine:v{expected}"
    )
    # The beta's automatic background probe blocks on some Windows pipe hosts.
    # A theme fallback only affects CLI display; explicit light settings still win.
    environ["FORCE_DARK_MODE"] = environ.get("FORCE_DARK_MODE") or "1"
    cli = {
        "executable": executable,
        "version": dagger_cli_version(
            invoke(
                root, [executable, "version"], run, environ, capture=True, timeout=30
            ).stdout
        ),
    }
    if cli["version"] != expected:
        raise checks.PreconditionError(
            "environment_error",
            f"Container parity requires Dagger {expected}; found {cli['version']} at {executable}. Update this terminal's PATH to the installed version",
        )
    modes = source_modes(root, run, environ)
    options = [
        "--source",
        ".",
        "--dagger-lock",
        "dagger.lock",
        "--request",
        json.dumps(request(names, **parameters)),
        "--executable-paths",
        json.dumps(modes),
        *git_secret_options(root, names, run, which, environ),
    ]
    result = invoke(
        root,
        [executable, "api", "call", "repository-check-environment", *options],
        run,
        environ,
        capture=True,
    )
    remote = json.loads(result.stdout)
    if "error" in remote:
        raise checks.PreconditionError(
            remote["error"]["status"], remote["error"]["reason"]
        )
    runtime = remote["runtime"]
    if not runtime["engine_version"].removeprefix("v").split("+")[0] == expected:
        raise checks.PreconditionError(
            "environment_error",
            "Dagger Engine does not match the module's pinned version",
        )
    if remote["schema_version"] != 1 or remote["environment"]["platform"] != "Linux":
        raise checks.PreconditionError(
            "environment_error", "Dagger did not provide Linux execution evidence"
        )
    executor = {
        "backend": "dagger",
        "cli": cli,
        "runtime": runtime,
        "source_modes_sha256": checks.digest(modes),
    }
    return executable, options, executor, remote


def identity(root, name, parameters, executor, remote):
    inputs, observations = checks.snapshot(root, name, omit=OMITTED_INPUTS)
    portable = checks.snapshot(root, name, portable=True, omit=OMITTED_INPUTS)[0]
    if portable != remote["source_inputs"][name]:
        local_files = {item[0]: item[2:] for item in portable["files"]}
        remote_files = {
            item[0]: item[2:] for item in remote["source_inputs"][name].get("files", [])
        }
        changed = sorted(
            path
            for path in local_files.keys() | remote_files.keys()
            if local_files.get(path) != remote_files.get(path)
        )
        raise checks.PreconditionError(
            "stale",
            "Container source does not match the current checkout: "
            + ", ".join(changed[:5]),
        )
    value = {
        "root": str(root),
        "name": name,
        "command": checks.command(name, **parameters),
        "parameters": parameters,
        "tools": remote["tools"][name],
        "inputs": inputs,
        "scope": list(dict.fromkeys((*checks.COMMON_INPUTS, *checks.SCOPES[name]))),
        "environment": remote["environment"],
        "executor": executor,
    }
    value["sha256"] = checks.digest(value)
    return value, observations


def evidence_file(path, root):
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > 20 * 1024 * 1024
    ):
        raise ValueError("Invalid exported evidence file")
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def read_evidence(entry, root):
    """Validate immutable exported facts and reports before admitting a receipt."""
    evidence = entry["evidence"]
    for item in evidence.values():
        path = root / item["path"]
        if not path.resolve().is_relative_to(root) or evidence_file(path, root) != item:
            raise ValueError("Missing or changed Dagger evidence")
    raw = checks.read_report(root / evidence["container-report.json"]["path"])
    native = next(item for item in raw["checks"] if item["name"] == entry["name"])
    if any(native.get(key) != entry.get(key) for key in FACT_FIELDS):
        raise ValueError("Local receipt differs from original container execution")
    if native["identity"] != entry["container_identity"]:
        raise ValueError("Container identity was changed")
    remote = json.loads(
        (root / evidence["environment.json"]["path"]).read_text(encoding="utf-8")
    )
    if native["identity"]["tools"] != remote["tools"][entry["name"]]:
        raise ValueError("Container tools do not match their execution evidence")
    if native["identity"]["environment"] != remote["environment"]:
        raise ValueError("Container environment does not match its execution evidence")
    if native["identity"]["environment"]["platform"] != "Linux":
        raise ValueError("Container checks did not execute on Linux")
    if entry["name"] == "pytest" and entry["exit_code"] == 0:
        junit = evidence["core-junit.xml"]["path"]
        if checks.pytest_counts(root, junit, 0) != entry.get("counts"):
            raise ValueError("JUnit does not match the container execution counts")
    if (
        entry["identity"]["parameters"]["coverage"]
        and entry["name"] == "pytest"
        and entry["status"] == "passed"
        and not {"core-coverage.xml", "core-coverage.json"} <= set(evidence)
    ):
        raise ValueError("Requested Coverage evidence is missing")


def reusable(entry, now, root):
    if (
        not entry
        or entry.get("identity") != now
        or entry.get("input_stable") is not True
        or entry.get("executed") is not True
    ):
        return False
    if type(entry.get("exit_code")) is not int:
        return False
    if not (
        entry["status"] in {"passed", "incomplete"}
        and entry["exit_code"] == 0
        or entry["status"] == "failed"
        and entry["exit_code"] != 0
    ):
        return False
    try:
        read_evidence(entry, root)
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return False
    return True


def execute(
    selectors,
    *,
    root=checks.ROOT,
    coverage=False,
    junit="core-junit.xml",
    output=None,
    run=subprocess.run,
    which=shutil.which,
    environ=None,
    reuse=False,
):
    root = root.resolve()
    names = checks.selected(selectors)
    parameters = {"coverage": coverage, "junit": junit}
    request(selectors, **parameters)
    checks.report_path(root / junit, root)
    if output:
        output = checks.report_path(output, root)
        if output.resolve() in {
            root / junit,
            root / "core-coverage.xml",
            root / "core-coverage.json",
        }:
            raise ValueError("Local JSON must not overwrite JUnit or Coverage evidence")
    environ = checks.execution_environment(root, environ)
    report = {
        "schema_version": 1,
        "profile": "+".join(selectors),
        "selectors": selectors,
        "executor": {"backend": "dagger"},
        "checks": [
            {
                "name": name,
                "status": "not_run",
                "executed": False,
                "executed_this_invocation": False,
                "reused": False,
                "exit_code": None,
                "input_stable": False,
                "passed_for_current_input": False,
                "identity": None,
            }
            for name in names
        ],
        "overall_status": "failed",
        "exit_code": 3,
    }
    code = 3
    try:
        print(
            "Check executor: preparing Dagger / Linux and verifying source inputs",
            flush=True,
        )
        initial = {
            name: checks.snapshot(root, name, omit=OMITTED_INPUTS) for name in names
        }
        executable, _, executor, remote = prepare(
            root, names, parameters, run, which, environ
        )
        report.update(executor=executor, environment=remote["environment"])
        identities = {
            name: identity(root, name, parameters, executor, remote) for name in names
        }
        if any(
            initial[name] != (identities[name][0]["inputs"], identities[name][1])
            for name in names
        ):
            raise checks.PreconditionError(
                "stale", "Inputs changed while preparing the container"
            )
        previous = {}
        if reuse and output and output.is_file():
            try:
                saved = checks.read_report(output)
                if saved.get("executor", {}).get("backend") == "dagger":
                    previous = {entry["name"]: entry for entry in saved["checks"]}
            except (OSError, ValueError, KeyError, TypeError):
                print("Local checks: invalid saved report; executing requested checks")
        pending = []
        for entry in report["checks"]:
            name = entry["name"]
            now = identities[name][0]
            if reusable(previous.get(name), now, root):
                entry.update(
                    copy.deepcopy(previous[name]),
                    reused=True,
                    executed_this_invocation=False,
                )
                print(
                    f"[{name}] reused {entry['status']}; Linux inputs, tools and evidence match",
                    flush=True,
                )
            else:
                entry["identity"] = now
                pending.append(name)
        print("Check executor: Dagger / Linux; shared Pipeline commands", flush=True)
        if pending:
            base = root / ".test-tools/checks/dagger"
            if not base.resolve().is_relative_to(root):
                raise ValueError(
                    "Evidence directory must stay in the original checkout"
                )
            base.mkdir(parents=True, exist_ok=True)
            modes = source_modes(root, run, environ)
            if checks.digest(modes) != executor["source_modes_sha256"]:
                raise checks.PreconditionError(
                    "stale", "Git input modes changed during preparation"
                )
            with tempfile.TemporaryDirectory(prefix="export-", dir=base) as temporary:
                exported = Path(temporary)
                args = [
                    executable,
                    "api",
                    "call",
                    "repository-check-artifacts",
                    "--source",
                    ".",
                    "--dagger-lock",
                    "dagger.lock",
                    "--request",
                    json.dumps(request(pending, **parameters)),
                    "--executable-paths",
                    json.dumps(modes),
                    *git_secret_options(root, pending, run, which, environ),
                    "export",
                    "--path",
                    str(exported),
                ]
                invoke(root, args, run, environ)
                native = checks.read_report(exported / "container-report.json")
                actual = json.loads(
                    (exported / "environment.json").read_text(encoding="utf-8")
                )
                if (
                    [item["name"] for item in native["checks"]] != pending
                    or any(
                        actual[key] != remote[key] for key in ("runtime", "environment")
                    )
                    or any(
                        actual["tools"][name] != remote["tools"][name]
                        for name in pending
                    )
                ):
                    raise ValueError(
                        "Container execution differs from the prepared request"
                    )
                filenames = (
                    "container-report.json",
                    "environment.json",
                    "core-junit.xml",
                    "core-coverage.xml",
                    "core-coverage.json",
                    "stdout.log",
                    "stderr.log",
                )
                exported_files = {
                    filename: evidence_file(exported / filename, root)["sha256"]
                    for filename in filenames
                    if (exported / filename).is_file()
                }
                bundle = base / checks.digest(exported_files)
                bundle.mkdir(exist_ok=True)
                evidence = {}
                for filename in filenames:
                    source = exported / filename
                    if source.is_file():
                        evidence_file(source, root)
                        destination = bundle / filename
                        shutil.copyfile(source, destination)
                        evidence[filename] = evidence_file(destination, root)
                for filename, stream in (
                    ("stdout.log", sys.stdout),
                    ("stderr.log", sys.stderr),
                ):
                    if filename in evidence:
                        print(
                            (bundle / filename).read_text(encoding="utf-8"),
                            file=stream,
                            end="",
                            flush=True,
                        )
                for entry in report["checks"]:
                    if entry["name"] not in pending:
                        continue
                    child = next(
                        item
                        for item in native["checks"]
                        if item["name"] == entry["name"]
                    )
                    before = entry["identity"]
                    entry.update(
                        child,
                        identity=before,
                        container_identity=child["identity"],
                        evidence=evidence,
                    )
                    read_evidence(entry, root)
                    if (
                        actual["source_inputs"][entry["name"]]
                        != remote["source_inputs"][entry["name"]]
                    ):
                        raise checks.PreconditionError(
                            "stale", "Container inputs changed before execution"
                        )
                for filename, destination in (
                    ("core-junit.xml", root / junit),
                    ("core-coverage.xml", root / "core-coverage.xml"),
                    ("core-coverage.json", root / "core-coverage.json"),
                ):
                    if filename in evidence:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(bundle / filename, destination)
        for entry in report["checks"]:
            name = entry["name"]
            stable = checks.snapshot(root, name, omit=OMITTED_INPUTS) == initial[name]
            stable = (
                stable
                and checks.digest(source_modes(root, run, environ))
                == executor["source_modes_sha256"]
            )
            if not stable:
                entry.update(
                    status="stale",
                    input_stable=False,
                    passed_for_current_input=False,
                    reason="Host inputs changed during container execution",
                )
        code = next(
            (
                checks._process_exit_code(entry["exit_code"])
                if entry["exit_code"]
                else 3
                for entry in report["checks"]
                if not entry["passed_for_current_input"]
            ),
            0,
        )
    except KeyboardInterrupt:
        code = 130
        for entry in report["checks"]:
            if entry["status"] == "not_run":
                entry.update(
                    status="interrupted", reason="Container execution interrupted"
                )
    except (
        checks.PreconditionError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        StopIteration,
    ) as error:
        status = (
            error.status
            if isinstance(error, checks.PreconditionError)
            else "environment_error"
        )
        reason = (
            error.reason
            if isinstance(error, checks.PreconditionError)
            else "Unable to execute or validate container evidence"
        )
        original_failure = next(
            (
                entry["exit_code"]
                for entry in report["checks"]
                if entry.get("exit_code")
            ),
            None,
        )
        code = (
            checks._process_exit_code(original_failure)
            if original_failure
            else (127 if status == "missing_tool" else 3)
        )
        for entry in report["checks"]:
            entry.update(
                status="failed" if entry.get("exit_code") else status,
                passed_for_current_input=False,
                reason=reason,
            )
        print(f"Dagger checks: {reason}", flush=True)
    report.update(overall_status="passed" if code == 0 else "failed", exit_code=code)
    if output:
        try:
            checks.atomic_json(output, report)
        except OSError:
            report["report_error"] = "Local JSON could not be written"
            code = code or 3
            report.update(overall_status="failed", exit_code=code)
    return report, code


def inspect(
    saved, *, root=checks.ROOT, run=subprocess.run, which=shutil.which, environ=None
):
    root = root.resolve()
    environ = checks.execution_environment(root, environ)
    result = {
        "schema_version": 1,
        "profile": saved["profile"],
        "checks": [],
        "executor": saved["executor"],
    }
    remote = executor = None
    diagnostic = "Container execution identity is unavailable"
    try:
        parameters = saved["checks"][0]["identity"]["parameters"]
        _, _, executor, remote = prepare(
            root,
            [entry["name"] for entry in saved["checks"]],
            parameters,
            run,
            which,
            environ,
        )
    except (
        checks.PreconditionError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ) as error:
        if isinstance(error, checks.PreconditionError):
            diagnostic = error.reason
    for entry in saved["checks"]:
        current = {
            "name": entry["name"],
            "status": entry["status"],
            "exit_code": entry.get("exit_code"),
            "freshness": "unavailable",
            "passed_for_current_input": False,
            "environment": saved.get("environment"),
        }
        if remote is None:
            current["invalidation_reason"] = diagnostic
        for key in (
            "execution_status",
            "completeness",
            "counts",
            "optional_tests",
            "reason",
        ):
            if key in entry:
                current[key] = entry[key]
        if remote and entry.get("identity"):
            try:
                now, observations = identity(
                    root,
                    entry["name"],
                    entry["identity"]["parameters"],
                    executor,
                    remote,
                )
                read_evidence(entry, root)
                stable = (
                    checks.snapshot(root, entry["name"], omit=OMITTED_INPUTS)[1]
                    == observations
                )
                current["freshness"] = (
                    "current" if now == entry["identity"] and stable else "stale"
                )
                current["passed_for_current_input"] = (
                    reusable(entry, now, root)
                    and entry["status"] == "passed"
                    and stable
                )
            except (
                checks.PreconditionError,
                OSError,
                ValueError,
                KeyError,
                TypeError,
                StopIteration,
            ):
                current["invalidation_reason"] = (
                    "Container evidence or source binding is unavailable"
                )
        result["checks"].append(current)
    passed = all(entry["passed_for_current_input"] for entry in result["checks"])
    result["overall_status"] = "passed" if passed else "failed"
    return result, 0 if passed else 3
