"""One registration source for descriptors and replaceable tool implementations."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .documents import WorkflowError, digest
from .schemas import TOOL_DESCRIPTOR, check_schema, validate


@dataclass(frozen=True)
class ToolSpec:
    tool_id: str
    input_schema: dict
    output_schema: dict
    contract_version: str = "1"
    effect: str = "check"
    recovery: str = "restartable"
    reuse: str = "verified_identity"
    environments: tuple[str, ...] = ("host",)

    def document(self):
        return {**asdict(self), "environments": list(self.environments)}

    @property
    def sha256(self):
        return digest(self.document())


@dataclass
class ToolResult:
    status: str
    exit_code: int | None
    outputs: dict = field(default_factory=dict)
    executed: bool = False
    complete: bool = False
    current: bool = False
    reused: bool = False
    report_generated: bool = False
    reason: str | None = None

    def document(self):
        return asdict(self)


@dataclass(frozen=True)
class Invocation:
    step_id: str
    tool_id: str
    inputs: Any
    attempt: int
    previous: dict | None = None


@dataclass(frozen=True)
class ExecutionContext:
    execution_id: str
    generation_id: str
    definition_id: str
    run_dir: Path
    predecessors: Mapping


class ToolAdapter(Protocol):
    def invoke(
        self, calls: tuple[Invocation, ...], context: ExecutionContext
    ) -> dict[str, ToolResult]:
        """Execute an admitted ready batch; independently verify any previous evidence."""
        ...


class Catalog:
    def __init__(self):
        self._tools = {}

    def register(self, spec: ToolSpec, adapter: ToolAdapter):
        spec = deepcopy(spec)
        validate(spec.document(), TOOL_DESCRIPTOR, label="tool descriptor")
        if spec.tool_id in self._tools:
            raise WorkflowError("Duplicate tool registration")
        if spec.effect not in {
            "read",
            "check",
            "write",
        }:
            raise WorkflowError("Unsupported tool contract")
        if (
            spec.recovery not in {"restartable", "manual"}
            or spec.reuse != "verified_identity"
        ):
            raise WorkflowError("Unsupported recovery or reuse contract")
        if spec.effect == "write" and spec.recovery != "manual":
            raise WorkflowError("Write tools require domain-owned manual recovery")
        if not spec.environments or any(
            not isinstance(value, str) or not value for value in spec.environments
        ):
            raise WorkflowError("Tools must declare supported execution environments")
        check_schema(spec.input_schema)
        check_schema(spec.output_schema)
        self._tools[spec.tool_id] = (spec, adapter)

    def resolve(self, tool_id):
        if tool_id not in self._tools:
            raise WorkflowError(f"Tool is not registered: {tool_id}")
        return self._tools[tool_id]

    def documents(self):
        return [
            {**spec.document(), "contract_sha256": spec.sha256}
            for spec, _ in self._tools.values()
        ]
