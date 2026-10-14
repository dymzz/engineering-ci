"""Container protocol for the existing check runner; never invokes Dagger or GitHub."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from . import checks
from .check_operations import container_plan

OMITTED_INPUTS = ("config/ghwf.local.toml",)


def aggregate(root, names, request, runtime, branches, out):
    """Validate original branch facts and collect evidence without executing checks."""
    current = prepare(root, names, runtime)
    report = {
        "schema_version": 1,
        "profile": "+".join(request["selectors"]),
        "selectors": request["selectors"],
        "checks": [],
    }
    codes = []
    for name in names:
        branch = branches / name
        environment = json.loads((branch / "environment.json").read_text())
        if "error" in environment:
            raise checks.PreconditionError(
                environment["error"]["status"], environment["error"]["reason"]
            )
        native = checks.read_report(branch / "container-report.json")
        if [entry["name"] for entry in native["checks"]] != [name]:
            raise ValueError("Branch does not match the requested check")
        entry = native["checks"][0]
        if type(native.get("exit_code")) is not int or native["exit_code"] != int(
            (branch / "exit-code.txt").read_text()
        ):
            raise ValueError("Branch exit evidence does not match")
        if entry["identity"]["name"] != name or any(
            entry["identity"][key] != expected
            for key, expected in (
                ("tools", environment["tools"][name]),
                ("environment", environment["environment"]),
            )
        ):
            raise ValueError("Branch identity differs from its environment")
        if entry["status"] == "passed" and not (
            entry.get("executed") is True
            and entry.get("input_stable") is True
            and entry.get("passed_for_current_input") is True
            and entry.get("exit_code") == 0
            and native["exit_code"] == 0
        ):
            raise ValueError("Branch pass lacks original successful execution")
        if entry["identity"]["parameters"] != {
            "coverage": request["coverage"],
            "junit": request["junit"],
        }:
            raise ValueError("Branch execution parameters do not match")
        codes.append(native["exit_code"])
        if any(
            environment[key] != current[key] for key in ("runtime", "environment")
        ) or any(
            environment[key][name] != current[key][name]
            for key in ("tools", "source_inputs")
        ):
            entry.update(
                status="stale",
                input_stable=False,
                passed_for_current_input=False,
                reason="Branch inputs or environment differ from the completed graph",
            )
            codes.append(3)
        report["checks"].append(entry)
        if entry["status"] != "passed" and native["exit_code"] == 0:
            codes.append(3)
        for filename in ("stdout.log", "stderr.log"):
            with (out / filename).open("ab") as destination:
                destination.write((branch / filename).read_bytes())
        if name == "pytest":
            for filename in (
                "core-junit.xml",
                "core-coverage.xml",
                "core-coverage.json",
            ):
                if (branch / filename).is_file():
                    checks.shutil.copyfile(branch / filename, out / filename)
    code = next((code for code in codes if code), 0)
    report.update(
        overall_status="passed"
        if code == 0
        and all(
            entry.get("passed_for_current_input") is True for entry in report["checks"]
        )
        else "failed",
        exit_code=code,
    )
    if report["overall_status"] != "passed" and code == 0:
        report["exit_code"] = code = 3
    checks.atomic_json(out / "container-report.json", report)
    checks.atomic_json(out / "environment.json", current)
    (out / "exit-code.txt").write_text(str(code), encoding="utf-8")
    return 1 if 128 <= code < 192 else code


def prepare(root, names, runtime):
    """Describe actual tools and source inputs without running a check."""
    environment = checks.execution_environment(root, os.environ)
    result = {
        "schema_version": 1,
        "runtime": runtime,
        "environment": checks.environment_identity(environment),
        "tools": {},
        "source_inputs": {},
    }
    for name in names:
        result["tools"][name] = checks.preflight(
            name, root, checks.subprocess.run, checks.shutil.which, environment
        )
        result["source_inputs"][name] = checks.snapshot(
            root, name, portable=True, omit=OMITTED_INPUTS
        )[0]
    return result


def main(argv=None, *, output=None, branches=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=("plan", "select", "prepare", "execute", "aggregate")
    )
    parser.add_argument("--request")
    parser.add_argument("--runtime")
    args = parser.parse_args(argv)
    if args.operation == "plan":
        if args.request is not None or args.runtime is not None:
            parser.error("plan reads the validated Pipeline only")
        print(json.dumps(container_plan(checks.PIPELINE), sort_keys=True))
        return 0
    if args.operation == "select":
        if args.request is None or args.runtime is not None:
            parser.error("select requires only a request")
        request = json.loads(args.request)
        if (
            set(request) != {"selectors", "coverage", "junit"}
            or type(request["coverage"]) is not bool
        ):
            raise ValueError("Invalid container request")
        print(json.dumps(checks.selected(request["selectors"])))
        return 0
    if args.request is None or args.runtime is None:
        parser.error("prepare and execute require --request and --runtime")
    root = checks.ROOT
    out = Path(output) if output else Path("/evidence")
    out.mkdir(parents=True, exist_ok=True)
    try:
        request = json.loads(args.request)
        runtime = json.loads(args.runtime)
        names = checks.selected(request["selectors"])
        if set(request) != {"selectors", "coverage", "junit"}:
            raise ValueError("Invalid container request")
        if type(request["coverage"]) is not bool:
            raise ValueError("Invalid coverage request")
        junit = root / request["junit"]
        if not junit.resolve().is_relative_to(root):
            raise ValueError("JUnit must stay inside the source")
        if args.operation == "aggregate":
            return aggregate(
                root, names, request, runtime, Path(branches or "/branches"), out
            )
        before = prepare(root, names, runtime)
        checks.atomic_json(out / "environment.json", before)
        if args.operation == "prepare":
            return 0
        report, code = checks.execute(
            request["selectors"],
            root=root,
            coverage=request["coverage"],
            junit=request["junit"],
            output=out / "container-report.json",
        )
        # The original tool exits remain in the report; export also works on failure.
        after = prepare(root, names, runtime)
        if before != after:
            for entry in report["checks"]:
                entry.update(
                    status="stale",
                    passed_for_current_input=False,
                    input_stable=False,
                    reason="Container inputs or tools changed",
                )
            report.update(overall_status="failed", exit_code=code or 3)
            code = code or 3
        checks.atomic_json(out / "container-report.json", report)
        checks.atomic_json(out / "environment.json", after)
        for source, destination in (
            (junit, "core-junit.xml"),
            (root / "core-coverage.xml", "core-coverage.xml"),
            (root / "core-coverage.json", "core-coverage.json"),
        ):
            if source.is_file() and (
                destination == "core-junit.xml" or request["coverage"]
            ):
                checks.shutil.copyfile(source, out / destination)
        (out / "exit-code.txt").write_text(str(code), encoding="utf-8")
        # Dagger ANY excludes 128..191. Export a nonzero transport result while
        # retaining the original signal/tool result in JSON and exit-code.txt.
        return 1 if 128 <= code < 192 else code
    except (
        checks.PreconditionError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ) as error:
        status = (
            error.status
            if isinstance(error, checks.PreconditionError)
            else "environment_error"
        )
        reason = (
            error.reason
            if isinstance(error, checks.PreconditionError)
            else "Invalid or unavailable container evidence"
        )
        checks.atomic_json(
            out / "environment.json",
            {"schema_version": 1, "error": {"status": status, "reason": reason}},
        )
        return 127 if status == "missing_tool" else 3


if __name__ == "__main__":
    raise SystemExit(main())
