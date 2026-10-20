"""Public CLI Provider for the primary engineering checkout; no shared definitions."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from functools import partial
from pathlib import Path
from types import SimpleNamespace

from vesryn_tool_runtime import (
    Catalog,
    DiscoveryScope,
    Invocation,
    ToolResult,
    ToolRuntime,
    ToolSpec,
)
from vesryn_tool_runtime.documents import WorkflowError, read
from vesryn_tool_runtime.schemas import RESULT, validate


class ProviderAdapter:
    """Reach the domain Provider from one checkout while checking another.

    root is the Provider source that supplies the contract; target is the consumer
    tree that is checked; output_root owns this run's evidence. A caller that supplies
    only root keeps the historical single-directory behaviour exactly.
    """

    def __init__(self, root, specs, *, target=None, output_root=None):
        self.root = Path(root).resolve()
        self.target = Path(target).resolve() if target is not None else self.root
        self.output_root = (
            Path(output_root).resolve() if output_root is not None else self.target
        )
        self.specs = specs

    def request(self, action, value=None, run_dir=None):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(self.root / "src")
        command = [
            sys.executable,
            "-m",
            "engineering_ci.workflow_adapters.provider",
            action,
            "--root",
            str(self.root),
            "--target",
            str(self.target),
            "--output-root",
            str(self.output_root),
        ]
        try:
            with tempfile.TemporaryDirectory(
                prefix=".domain-provider-", dir=run_dir or self.output_root
            ) as directory:
                output = Path(directory) / "response.json"
                if value is not None:
                    request = Path(directory) / "request.json"
                    request.write_text(json.dumps(value), encoding="utf-8")
                    command += ["--request", str(request)]
                with output.open("wb") as stream:
                    process = subprocess.run(
                        command,
                        cwd=self.root,
                        env=environment,
                        stdout=stream,
                        stderr=subprocess.DEVNULL,
                        timeout=600 if action == "invoke" else 30,
                        check=False,
                    )
                if process.returncode:
                    raise WorkflowError("Domain Provider did not return evidence")
                return read(output)
        except (OSError, subprocess.TimeoutExpired):
            raise WorkflowError("Domain Provider transport is unavailable") from None

    def invoke(self, calls, context):
        request = {
            "calls": [
                {
                    "step_id": call.step_id,
                    "tool_id": call.tool_id,
                    "inputs": call.inputs,
                    "attempt": call.attempt,
                    "previous": call.previous,
                    "contract_sha256": self.specs[call.tool_id].sha256,
                }
                for call in calls
            ],
            "context": {
                "execution_id": context.execution_id,
                "run_dir": str(context.run_dir),
            },
        }
        try:
            response = self.request("invoke", request, context.run_dir)
            result = {}
            if set(response["results"]) != {call.step_id for call in calls}:
                raise WorkflowError("Domain Provider omitted a requested result")
            for call in calls:
                value = response["results"][call.step_id]
                validate(value, RESULT, label="domain tool result")
                # Runtime preserves an original failure even if output evidence is invalid.
                result[call.step_id] = ToolResult(**value)
            return result
        except (WorkflowError, KeyError, TypeError):
            return {
                call.step_id: ToolResult(
                    "unavailable",
                    3,
                    reason="Domain Provider binding or evidence is unavailable",
                )
                for call in calls
            }


def catalog(root, *, target=None, output_root=None):
    root = Path(root).resolve()
    if not (root / "src/engineering_ci/workflow_adapters/provider.py").is_file():
        raise WorkflowError(
            "Domain Provider requires its explicit primary source checkout"
        )
    result = Catalog()
    result.provider_name = "engineering-ci-dagger"
    adapter = ProviderAdapter(root, {}, target=target, output_root=output_root)
    value = adapter.request("tools")
    if (
        value.get("schema_version") != 1
        or value.get("kind") != "domain-provider-catalog"
    ):
        raise WorkflowError("Unsupported domain Provider catalog")
    for document in value["tools"]:
        fields = dict(document)
        declared = fields.pop("contract_sha256")
        for name in ("environments", "use_when", "not_for", "tags"):
            if name in fields:
                fields[name] = tuple(fields[name])
        spec = ToolSpec(**fields)
        if spec.sha256 != declared:
            raise WorkflowError("Domain Provider contract identity mismatch")
        result.register(spec, adapter)
        adapter.specs[spec.tool_id] = spec
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("tools", "invoke"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--target",
        type=Path,
        help="Consumer source directory to check; defaults to the Provider checkout",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        help="Consumer-approved directory owning this run's evidence",
    )
    parser.add_argument("--request", type=Path)
    args = parser.parse_args(argv)
    try:
        from .. import checks
        from .checks import dagger_catalog

        def original_process(command, **kwargs):
            if not kwargs.get("capture_output") and "stdout" not in kwargs:
                kwargs["stdout"] = sys.stderr
            return subprocess.run(command, check=kwargs.pop("check", False), **kwargs)

        root = args.root.resolve()
        target = args.target.resolve() if args.target else root
        owner = args.output_root.resolve() if args.output_root else target
        tools = dagger_catalog(
            root,
            runner=partial(checks.run_checks, run=original_process),
            target=target,
            output_root=owner,
        )
        if args.action == "tools":
            value = {
                "schema_version": 1,
                "kind": "domain-provider-catalog",
                "provider_source": str(root),
                "check_target": str(target),
                "output_root": str(owner),
                "tools": tools.documents(),
            }
        else:
            value = read(args.request)
            directory = Path(value["context"]["run_dir"]).resolve()
            if not directory.is_relative_to(owner):
                raise WorkflowError(
                    "Domain evidence must stay inside the approved output root"
                )
            calls, adapter = [], None
            for entry in value["calls"]:
                spec, selected = tools.resolve(entry["tool_id"])
                if spec.sha256 != entry["contract_sha256"]:
                    raise WorkflowError("Domain contract changed after selection")
                validate(entry["inputs"], spec.input_schema, label="domain inputs")
                if adapter is not None and adapter is not selected:
                    raise WorkflowError("Provider batch has inconsistent adapters")
                adapter = selected
                calls.append(
                    Invocation(
                        entry["step_id"],
                        entry["tool_id"],
                        entry["inputs"],
                        entry["attempt"],
                        entry["previous"],
                    )
                )
            if not calls:
                raise WorkflowError("Domain Provider batch is empty")
            context = SimpleNamespace(
                execution_id=value["context"]["execution_id"], run_dir=directory
            )
            with redirect_stdout(sys.stderr):
                receipt = ToolRuntime(
                    tools,
                    scope=DiscoveryScope(
                        environment="dagger", permissions=frozenset({"check"})
                    ),
                ).dispatch(tuple(calls), context)
                results = receipt.results
            value = {"results": results}
        print(json.dumps(value))
        return 0
    except (WorkflowError, KeyError, TypeError, OSError):
        print(
            json.dumps(
                {
                    "status": "unavailable",
                    "exit_code": 3,
                    "reason": "Domain Provider inputs or binding are unavailable",
                }
            )
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
