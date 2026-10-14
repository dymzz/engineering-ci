"""Pinned Harness delegation, old identities and base CI/CD without optional Vesryn."""

import copy
import os
import subprocess
import sys
from pathlib import Path

import pytest

from engineering_ci.cli import main
from engineering_ci.workflow_adapters.contracts import APPROVAL, FEEDBACK
from engineering_ci.workflow_core.capabilities import CapabilityStore
from engineering_ci.workflow_core.documents import WorkflowError, digest
from engineering_ci.workflow_core.schemas import validate


def test_compatibility_imports_are_the_same_portable_types():
    from vesryn.tracing.sources.codex_rollout import RolloutSource as NewSource
    from vesryn.workflow_core import Runtime as NewRuntime
    from vesryn.workflow_core.documents import WorkflowError as NewError

    from engineering_ci.tracing.sources.codex_rollout import RolloutSource
    from engineering_ci.workflow_core import Runtime
    from engineering_ci.workflow_core.documents import WorkflowError as OldError

    assert Runtime is NewRuntime and OldError is NewError and RolloutSource is NewSource


def test_host_keeps_original_narrow_approval_schema(tmp_path):
    from vesryn.workflow_core.capabilities import CapabilityStore as PortableStore

    assert issubclass(CapabilityStore, PortableStore)
    assert CapabilityStore(tmp_path).approval_schema is APPROVAL
    assert APPROVAL["properties"]["verified_scope"] == {
        "const": "check-submission-reuse"
    }
    assert FEEDBACK["properties"]["task_outcome_scope"] == {
        "const": "original-registered-checks"
    }


def test_legacy_feedback_remains_byte_identical_and_unknown_scope_is_rejected():
    from test_workflow_feedback_query import recorded, row

    from engineering_ci.tracing.feedback_query import checked_document

    value = recorded()
    identity = value["feedback_id"]
    assert checked_document(row(value))["feedback_id"] == identity
    assert (
        digest({key: item for key, item in value.items() if key != "feedback_id"})
        == identity
    )
    modified = copy.deepcopy(value)
    modified["task_outcome_scope"] = "other-domain"
    with pytest.raises(WorkflowError):
        validate(modified, FEEDBACK)


def test_host_archive_supplies_validation_before_portable_persistence(monkeypatch):
    from vesryn.tracing.store import ArchiveStore as PortableStore

    from engineering_ci.tracing.store import ArchiveStore

    calls = []

    def persist(self, value, *, validator):
        validator(value)
        calls.append(value)
        return {"status": "registered"}

    monkeypatch.setattr(PortableStore, "register_feedback", persist)
    archive = object.__new__(ArchiveStore)
    from test_workflow_feedback_query import recorded

    value = recorded()
    assert archive.register_feedback(value)["status"] == "registered"
    wrong = copy.deepcopy(value)
    wrong["metrics"]["unexpected"] = 0
    with pytest.raises(WorkflowError):
        archive.register_feedback(wrong)
    assert calls == [value]


def test_base_commands_do_not_import_optional_harness(tmp_path):
    script = """
import importlib.abc
import sys
class BlockHarness(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "vesryn" or name.startswith("vesryn."):
            raise ModuleNotFoundError("Optional Harness absent", name="vesryn")
sys.meta_path.insert(0, BlockHarness())
from engineering_ci.cli import main
for argv in (["checks", "--help"], ["version", "--help"], ["release", "--help"], ["download", "--help"]):
    try:
        assert main(argv) == 0
    except SystemExit as error:
        assert error.code == 0
assert not any(name.startswith("vesryn") for name in sys.modules)
assert main(["workflow", "tools"]) == 2
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "uv sync --locked --extra harness" in result.stderr


def test_old_trace_configuration_stays_checkout_anchored(tmp_path, monkeypatch):
    from engineering_ci import _paths
    from engineering_ci.tracing.config import load_config

    monkeypatch.chdir(tmp_path)
    assert load_config().schema == "engineering_ci_trace"
    assert (_paths.ROOT / "config/tracing.toml").is_file()


def test_compatibility_help_does_not_reject_injected_configuration(capsys):
    with pytest.raises(SystemExit) as result:
        main(["trace", "--help"])
    assert result.value.code == 0
    assert "status" in capsys.readouterr().out
