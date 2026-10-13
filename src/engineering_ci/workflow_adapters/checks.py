"""Expose existing check operations as tools, sharing each ready Dagger batch."""

import copy
import hashlib
import shutil
from pathlib import Path
from uuid import uuid4

from .. import checks
from ..check_operations import OPERATIONS
from ..workflow_core import Catalog, ToolResult, ToolSpec
from ..workflow_core.documents import WorkflowError
from ..workflow_core.documents import read as read_document
from ..workflow_core.documents import write as write_document
from ..workflow_core.schemas import HASH, object_schema


def tool_id(name):
    return {"version": "engineering-ci.version-check", "lock": "uv.lock-check"}.get(
        name, f"python.{name}"
    )


RESOURCE = object_schema({"path": {"type": "string"}, "sha256": HASH})
INPUT = object_schema(
    {
        "executor": {"enum": ["auto", "dagger", "native"]},
        "coverage": {"type": "boolean"},
        "junit": {
            "type": "string",
            "pattern": "^(?!/)(?!.*(?:\\\\|:|(?:^|/)\\.\\.(?:/|$))).+\\.xml$",
        },
    },
    [],
)
OUTPUT = object_schema(
    {
        "name": {"enum": list(OPERATIONS)},
        "report": RESOURCE,
        "check_status": {"type": "string"},
        "evidence": {"type": "array", "items": RESOURCE},
    }
)


def recipe(selectors, *, executor="auto", coverage=False, junit="core-junit.xml"):
    """Expand the existing profile once into actual independently editable steps."""
    return {
        "schema_version": 1,
        "kind": "workflow-recipe",
        "steps": [
            {
                "id": name,
                "tool": tool_id(name),
                "inputs": {"executor": executor, "coverage": coverage, "junit": junit},
            }
            for name in checks.selected(selectors)
        ],
    }


class ChecksAdapter:
    def __init__(self, root, *, runner=None):
        self.root = Path(root).resolve()
        self.runner = runner or checks.run_checks
        self.names = {tool_id(name): name for name in OPERATIONS}

    def resource(self, path):
        path = Path(path)
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(self.root)
            or not path.is_file()
            or path.stat().st_size > 20 * 1024 * 1024
        ):
            raise WorkflowError("Check evidence must stay in the primary checkout")
        with path.open("rb") as stream:
            sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        return {"path": path.relative_to(self.root).as_posix(), "sha256": sha256}

    def verified_previous(self, invocation):
        """Verify immutable references; original checks still decide current identity."""
        if not invocation.previous:
            return None
        outputs = invocation.previous.get("outputs", {})
        try:
            for resource in [outputs["report"], *outputs["evidence"]]:
                if self.resource(self.root / resource["path"]) != resource:
                    return None
            name = self.names[invocation.tool_id]
            if outputs["name"] != name:
                return None
            report = checks.read_report(self.root / outputs["report"]["path"])
            return next(item for item in report["checks"] if item["name"] == name)
        except (OSError, ValueError, KeyError, TypeError, StopIteration):
            return None

    def recovered_previous(self, call, context, parameters):
        """Recover this execution's durable domain receipts after host process death."""
        for attempt in range(call.attempt - 1, 0, -1):
            directory = context.run_dir / "tool-results" / call.step_id / str(attempt)
            try:
                intent = read_document(directory / "intent.json")
                if intent != {
                    "execution_id": context.execution_id,
                    "step_id": call.step_id,
                    "tool_id": call.tool_id,
                    "attempt": attempt,
                    "parameters": parameters,
                    "report": intent.get("report"),
                }:
                    continue
                result_path = directory / "result.json"
                if result_path.is_file():
                    from dataclasses import replace

                    receipt = read_document(result_path)
                    recovered = self.verified_previous(replace(call, previous=receipt))
                    if recovered:
                        return recovered, receipt["outputs"]["evidence"]
                    continue
                report_path = self.root / intent["report"]
                self.resource(report_path)
                report = checks.read_report(report_path)
                entry = next(
                    item
                    for item in report["checks"]
                    if item["name"] == self.names[call.tool_id]
                )
                # Exported Dagger facts are independently verifiable. Native pytest without
                # its frozen receipt is incomplete evidence, so it cannot be reused here.
                if entry["name"] != "pytest" or entry.get("evidence"):
                    return entry, list(entry.get("evidence", {}).values())
            except (OSError, ValueError, KeyError, TypeError, StopIteration):
                continue
        return None, []

    def invoke(self, calls, context):
        """Group identical environment/parameters; keep each tool's real child result."""
        directory = context.run_dir / "tool-results"
        if not directory.resolve().is_relative_to(self.root):
            raise WorkflowError("Check run outputs must stay in the primary checkout")
        results = {}
        grouped = {}
        for call in calls:
            params = {
                "executor": "auto",
                "coverage": False,
                "junit": "core-junit.xml",
                **call.inputs,
            }
            checks.report_path(self.root / params["junit"], self.root)
            grouped.setdefault(tuple(params.items()), []).append(call)
        for key, group in grouped.items():
            params = dict(key)
            names = list(dict.fromkeys(self.names[call.tool_id] for call in group))
            output = directory / uuid4().hex / "checks.json"
            output.parent.mkdir(parents=True, exist_ok=False)
            prior, prior_evidence = {}, {}
            for call in group:
                entry = self.verified_previous(call)
                if entry:
                    evidence = call.previous["outputs"]["evidence"]
                else:
                    entry, evidence = self.recovered_previous(call, context, params)
                prior[self.names[call.tool_id]] = entry
                prior_evidence[call.step_id] = evidence
            for call in group:
                write_document(
                    context.run_dir
                    / "tool-results"
                    / call.step_id
                    / str(call.attempt)
                    / "intent.json",
                    {
                        "execution_id": context.execution_id,
                        "step_id": call.step_id,
                        "tool_id": call.tool_id,
                        "attempt": call.attempt,
                        "parameters": params,
                        "report": output.relative_to(self.root).as_posix(),
                    },
                )
            if any(prior.values()):
                checks.atomic_json(
                    output,
                    {
                        "schema_version": 1,
                        "profile": "+".join(names),
                        "selectors": names,
                        "checks": [prior[name] or {"name": name} for name in names],
                        # A Dagger receipt needs its original backend marker for reuse admission.
                        **(
                            {"executor": {"backend": "dagger"}}
                            if any(
                                (item or {})
                                .get("identity", {})
                                .get("executor", {})
                                .get("backend")
                                == "dagger"
                                for item in prior.values()
                            )
                            else {}
                        ),
                    },
                )
            report, code = self.runner(
                names, root=self.root, output=output, reuse=True, **params
            )
            entries = {entry["name"]: entry for entry in report["checks"]}
            try:
                resource = self.resource(output)
            except (OSError, WorkflowError):
                for call in group:
                    entry = entries[self.names[call.tool_id]]
                    raw_code = entry.get("exit_code")
                    results[call.step_id] = ToolResult(
                        "failed"
                        if entry["status"] == "failed" and raw_code
                        else "unavailable",
                        raw_code or code or 3,
                        executed=entry.get("executed") is True,
                        reason="Original report could not be saved; execution failure remains authoritative",
                    )
                continue
            for call in group:
                entry = entries[self.names[call.tool_id]]
                status = entry["status"]
                outcome = (
                    status
                    if status in {"passed", "failed", "incomplete", "interrupted"}
                    else "unavailable"
                )
                evidence = list(entry.get("evidence", {}).values())
                if (
                    entry.get("executed_this_invocation")
                    and entry["name"] == "pytest"
                    and not evidence
                ):
                    # Native JUnit is mutable at its requested output; freeze it for this execution.
                    for path in (
                        self.root / params["junit"],
                        *(
                            [
                                self.root / "core-coverage.xml",
                                self.root / "core-coverage.json",
                            ]
                            if params["coverage"]
                            else []
                        ),
                    ):
                        if path.is_file():
                            frozen = output.parent / path.name
                            shutil.copyfile(path, frozen)
                            evidence.append(self.resource(frozen))
                elif entry.get("reused") and not evidence:
                    evidence = copy.deepcopy(prior_evidence[call.step_id])
                results[call.step_id] = ToolResult(
                    status=outcome,
                    exit_code=entry.get("exit_code")
                    if entry.get("exit_code") is not None
                    else (code or None),
                    outputs={
                        "name": entry["name"],
                        "report": resource,
                        "check_status": status,
                        "evidence": evidence,
                    },
                    executed=entry.get("executed") is True,
                    complete=status in {"passed", "failed"}
                    and entry.get("executed") is True,
                    current=entry.get("input_stable") is True,
                    reused=entry.get("reused") is True,
                    report_generated=True,
                    reason=None
                    if status in {"passed", "failed"}
                    else "Inspect the original check report and execution environment",
                )
                write_document(
                    context.run_dir
                    / "tool-results"
                    / call.step_id
                    / str(call.attempt)
                    / "result.json",
                    results[call.step_id].document(),
                )
        return results


def catalog(root=checks.ROOT, *, runner=None):
    """Register from the original operation set; no duplicate commands or versions."""
    result, adapter = Catalog(), ChecksAdapter(root, runner=runner)
    for name in OPERATIONS:
        output_schema = copy.deepcopy(OUTPUT)
        output_schema["properties"]["name"] = {"const": name, "type": "string"}
        result.register(
            ToolSpec(
                tool_id(name), INPUT, output_schema, environments=("native", "dagger")
            ),
            adapter,
        )
    from .inspection import SPEC, InspectionAdapter

    result.register(SPEC, InspectionAdapter(root))
    return result
