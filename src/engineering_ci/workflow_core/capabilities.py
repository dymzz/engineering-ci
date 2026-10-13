"""Portable, explicit capability approvals and atomic future-call selection."""

from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from .definition import load_generation
from .documents import WorkflowError, digest, read, write
from .feedback_snapshot import SNAPSHOT
from .schemas import HASH, ID, NAME, SCHEMAS, object_schema, validate

VERSION = {"type": "string", "pattern": r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"}
REFERENCE = object_schema({"name": NAME, "version": VERSION, "approval_id": HASH})
SCOPE = object_schema(
    {
        "execution_id": ID,
        **{
            key: HASH
            for key in (
                "state_sha256",
                "observations_sha256",
                "provenance_sha256",
                "evidence_sha256",
            )
        },
    }
)
EVIDENCE = object_schema(
    {"path": {"type": "string", "maxLength": 1024}, "scope": SCOPE}
)
APPROVAL = object_schema(
    {
        "schema_version": {"const": 1, "type": "integer"},
        "kind": {"const": "workflow-capability-approval"},
        "approval_id": HASH,
        "name": NAME,
        "version": VERSION,
        "decision": {"const": "explicit-cli-approval"},
        "definition_id": HASH,
        "workflow_sha256": HASH,
        "provenance_sha256": HASH,
        "comparison_id": HASH,
        "comparison_sha256": HASH,
        "comparison_version": {"type": "string"},
        "verified_scope": {"const": "check-submission-reuse"},
        "baseline": EVIDENCE,
        "candidate": EVIDENCE,
    }
)
INDEX = object_schema(
    {
        "schema_version": {"const": 1, "type": "integer"},
        "kind": {"const": "workflow-capability-catalog"},
        "revision": {"type": "integer", "minimum": 0},
        "versions": {
            "type": "array",
            "items": REFERENCE,
            "uniqueItems": True,
            "maxItems": 256,
        },
        "active": {
            "type": "object",
            "propertyNames": NAME,
            "additionalProperties": HASH,
        },
    }
)
CALL = object_schema(
    {
        "schema_version": {"const": 1, "type": "integer"},
        "kind": {"const": "workflow-capability-call"},
        **REFERENCE["properties"],
        "catalog_revision": {"type": "integer", "minimum": 0},
        "store": {"type": "string", "maxLength": 1024},
        "execution_id": ID,
        "generation_id": ID,
        "definition_id": HASH,
    }
)
FEEDBACK = object_schema(
    {
        "schema_version": {"const": 1, "type": "integer"},
        "kind": {"const": "workflow-capability-feedback"},
        "feedback_id": HASH,
        "capability": REFERENCE,
        "definition_id": HASH,
        "generation_id": ID,
        "catalog_revision": {"type": "integer", "minimum": 0},
        "evidence": SCOPE,
        "recorded_result": object_schema(
            {
                "phase": {
                    "enum": ["READY", "RUNNING", "PAUSED", "UNKNOWN", "FINISHED"]
                },
                "status": {
                    "enum": [
                        "not_run",
                        "running",
                        "passed",
                        "failed",
                        "incomplete",
                        "unavailable",
                        "interrupted",
                        "unknown",
                        "blocked",
                    ]
                },
                "exit_code": {"type": ["integer", "null"]},
            }
        ),
        "observation_completeness": {"enum": ["complete", "incomplete", "unknown"]},
        "metrics": object_schema(
            {
                key: {"type": ["integer", "null"], "minimum": 0}
                for key in (
                    "required_checks",
                    "observed_check_attempts",
                    "check_submissions",
                    "reused_checks",
                    "adapter_batches",
                )
            }
        ),
        "current_input_validity": {"const": "not_checked"},
        "task_outcome_scope": {"const": "original-registered-checks"},
        "optimization_evaluated": {"const": False},
        "llm_used": {"const": False},
    }
)
FEEDBACK["properties"]["schema_version"] = {"enum": [1, 2], "type": "integer"}
FEEDBACK["properties"]["attempt_snapshot"] = SNAPSHOT
FEEDBACK["allOf"] = [
    {
        "if": {"properties": {"schema_version": {"const": 2}}},
        "then": {"required": ["attempt_snapshot"]},
        "else": {"not": {"required": ["attempt_snapshot"]}},
    }
]
SCHEMAS.update(
    capability_approval=APPROVAL,
    capability_catalog=INDEX,
    capability_call=CALL,
    capability_feedback=FEEDBACK,
)


class CapabilityStore:
    """Store separate immutable versions; selection never rewrites prior executions."""

    def __init__(self, directory):
        self.directory = Path(directory).resolve()

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(self.directory / "catalog.lock", timeout=0):
                yield
        except Timeout:
            raise WorkflowError(
                "Capability catalog is busy; repeat the same command"
            ) from None

    def index(self):
        path = self.directory / "catalog.json"
        value = (
            read(path)
            if path.exists()
            else {
                "schema_version": 1,
                "kind": "workflow-capability-catalog",
                "revision": 0,
                "versions": [],
                "active": {},
            }
        )
        validate(value, INDEX, label="capability catalog")
        keys = [(item["name"], item["version"]) for item in value["versions"]]
        if len(keys) != len(set(keys)) or any(
            not any(
                item["name"] == name and item["approval_id"] == identity
                for item in value["versions"]
            )
            for name, identity in value["active"].items()
        ):
            raise WorkflowError("Capability catalog has conflicting version identities")
        return value

    def location(self, name, version):
        validate(name, NAME, label="capability name")
        validate(version, VERSION, label="capability version")
        path = self.directory / name / version
        if not path.resolve().is_relative_to(self.directory):
            raise WorkflowError("Capability version must stay inside its store")
        return path

    def publish(self, approval, workflow, provenance, comparison):
        """Commit approval last; a partial write can retry without exposing a version."""
        validate(approval, APPROVAL, label="capability approval")
        if approval["approval_id"] != digest(
            {k: v for k, v in approval.items() if k != "approval_id"}
        ):
            raise WorkflowError("Capability approval identity differs")
        reference = {key: approval[key] for key in ("name", "version", "approval_id")}
        with self.locked():
            index = self.index()
            old = next(
                (
                    item
                    for item in index["versions"]
                    if (item["name"], item["version"])
                    == (reference["name"], reference["version"])
                ),
                None,
            )
            if old is not None and old != reference:
                raise WorkflowError(
                    "Capability version already names another approval; choose a new version"
                )
            if any(
                approval[key] != digest(value)
                for key, value in (
                    ("workflow_sha256", workflow),
                    ("provenance_sha256", provenance),
                    ("comparison_sha256", comparison),
                )
            ) or any(
                approval[key] != comparison.get(key)
                for key in (
                    "comparison_id",
                    "comparison_version",
                    "verified_scope",
                )
            ):
                raise WorkflowError(
                    "Approval does not match its supplied definition and comparison"
                )
            directory = self.location(reference["name"], reference["version"])
            for filename, value in (
                ("workflow.json", workflow),
                ("provenance.json", provenance),
                ("comparison.json", comparison),
                ("approval.json", approval),
            ):
                path = directory / filename
                if path.exists():
                    if read(path) != value:
                        raise WorkflowError(
                            "Capability version files conflict; preserve them and use a new version"
                        )
                else:
                    write(path, value)
            if old is None:
                index["versions"].append(reference)
                index["revision"] += 1
                validate(index, INDEX, label="capability catalog")
                write(self.directory / "catalog.json", index, replace=True)
        return reference

    def load(self, name, version, catalog):
        """Resolve registered approval and original tool contracts; do not infer activation."""
        directory = self.location(name, version)
        index = self.index()
        reference = next(
            (
                item
                for item in index["versions"]
                if item["name"] == name and item["version"] == version
            ),
            None,
        )
        if reference is None:
            raise WorkflowError("Capability version is not registered")
        approval = read(directory / "approval.json")
        validate(approval, APPROVAL, label="capability approval")
        if reference != {key: approval[key] for key in reference} or approval[
            "approval_id"
        ] != digest(
            {key: value for key, value in approval.items() if key != "approval_id"}
        ):
            raise WorkflowError("Registered approval identity changed")
        workflow, provenance, _ = load_generation(directory / "workflow.json", catalog)
        comparison = read(directory / "comparison.json")
        if (
            any(
                approval[key] != digest(value)
                for key, value in (
                    ("workflow_sha256", workflow),
                    ("provenance_sha256", provenance),
                    ("comparison_sha256", comparison),
                )
            )
            or approval["definition_id"] != workflow["definition_id"]
        ):
            raise WorkflowError("Approved definition or evidence changed")
        return approval, directory

    def select(self, name, version, catalog):
        """Require one enabled version, including when the caller explicitly names it."""
        validate(name, NAME, label="capability name")
        index = self.index()
        identity = index["active"].get(name)
        reference = next(
            (
                item
                for item in index["versions"]
                if item["name"] == name and item["approval_id"] == identity
            ),
            None,
        )
        if reference is None or version is not None and reference["version"] != version:
            raise WorkflowError(
                "Capability version is not enabled; explicitly activate the reviewed version"
            )
        approval, directory = self.load(name, reference["version"], catalog)
        return approval, directory, index["revision"]

    def activate(self, name, version, catalog, admission):
        """Serialize activation with call admission; repeated selection is idempotent."""
        with self.locked():
            approval, directory = self.load(name, version, catalog)
            admission(approval, directory)
            index = self.index()
            if index["active"].get(name) != approval["approval_id"]:
                index["active"][name] = approval["approval_id"]
                index["revision"] += 1
                write(self.directory / "catalog.json", index, replace=True)
            return approval, index["revision"]

    def deactivate(self, name):
        """Disable subsequent selection while retaining approval and all prior receipts."""
        validate(name, NAME, label="capability name")
        with self.locked():
            index = self.index()
            if not any(item["name"] == name for item in index["versions"]):
                raise WorkflowError("Capability is not registered")
            if name in index["active"]:
                del index["active"][name]
                index["revision"] += 1
                write(self.directory / "catalog.json", index, replace=True)
            return index["revision"]
