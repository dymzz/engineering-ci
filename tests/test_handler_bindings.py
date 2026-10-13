"""Candidate admission and real check-runner failure/reuse contracts."""

import copy
import json
from dataclasses import replace

import pytest
from test_checks import checkout as check_checkout
from test_checks import fake_process
from test_trace_candidates import evidence

from engineering_ci import checks
from engineering_ci.cli import mine
from engineering_ci.mining.bindings import bind, read, validate
from engineering_ci.mining.candidates import candidates
from engineering_ci.tracing.results import canonical, digest
from engineering_ci.tracing.store import ArchiveError

checkout = check_checkout


def binding():
    value = evidence()
    return bind(value, candidates(value)["candidates"][0]["candidate_id"])


def identify(value):
    value["binding_id"] = digest(
        canonical({k: v for k, v in value.items() if k != "binding_id"})
    )
    return value


def test_binding_is_deterministic_and_does_not_approve_historical_checks():
    value = binding()
    assert value == binding() == validate(value)
    assert value["target"]["checks"] == list(checks.PR_CHECKS)
    assert value["target"]["handler"] == "checks.pr"
    assert value["source"]["completeness"] == "unknown"
    assert value["mapping_basis"] == "explicit_target_selection"
    assert value["historical_check_identity"] == "unavailable"
    assert not value["optimization_verified"] and not value["llm_used"]


def test_candidate_requires_actual_supported_observation_and_association():
    value = evidence()
    proposed = candidates(value)["candidates"]
    with pytest.raises(ArchiveError, match="adjacent command"):
        bind(value, proposed[1]["candidate_id"])
    with pytest.raises(ArchiveError, match="not present"):
        bind(value, "f" * 64)
    value = replace(value, association_verified=False)
    with pytest.raises(ArchiveError, match="verified"):
        bind(value, proposed[0]["candidate_id"])


def test_changed_model_is_rejected_before_binding():
    value = evidence()
    candidate_id = candidates(value)["candidates"][0]["candidate_id"]
    value.analysis["dfg"] = []
    with pytest.raises(ArchiveError, match="differ"):
        bind(value, candidate_id)


@pytest.mark.parametrize(
    "change",
    [
        lambda x: x.update(command="echo SECRET"),
        lambda x: x.update(schema_version=True),
        lambda x: x.update(mapping_basis="inferred"),
        lambda x: x.update(optimization_verified=True),
        lambda x: x.update(llm_used=True),
        lambda x: x["source"].update(token="SECRET"),
        lambda x: x["source"].update(candidate_id="SECRET"),
        lambda x: x["target"].update(checks=["ruff-lint"]),
        lambda x: x["target"].update(handler="release.publish"),
        lambda x: x["target"].update(reuse_policy="trust_historical_pass"),
    ],
)
def test_unsupported_fields_and_gate_changes_are_rejected(change):
    value = binding()
    change(value)
    with pytest.raises(ArchiveError):
        validate(identify(value))


def test_content_integrity_and_changed_pipeline_contract():
    value = binding()
    value["source"]["analysis_id"] = "a" * 64
    with pytest.raises(ArchiveError, match="identity"):
        validate(value)
    config = copy.deepcopy(checks.PIPELINE)
    config["arguments"]["pytest"] = ["-q", "-x"]
    with pytest.raises(ArchiveError, match="Pipeline contract changed"):
        validate(binding(), pipeline=config)


@pytest.mark.parametrize(
    "raw",
    [b"{", b"[]", b"null", b" " * (64 * 1024 + 1)],
    ids=["broken", "array", "null", "oversized"],
)
def test_invalid_files_are_safe_and_do_not_leak_content(tmp_path, raw):
    path = tmp_path / "binding.json"
    path.write_bytes(raw)
    with pytest.raises(ArchiveError, match="binding|Handler"):
        read(path)


def test_bind_cli_preserves_provenance_and_writes_no_execution_facts(
    tmp_path, monkeypatch, capsys
):
    from engineering_ci.cli import mine_bind

    value = evidence()
    monkeypatch.setattr(mine_bind, "file_evidence", lambda *args: value)
    out = tmp_path / "binding.json"
    args = [
        "bind",
        "analysis.json",
        "--projection",
        "projection.json",
        "--candidate",
        candidates(value)["candidates"][0]["candidate_id"],
        "--profile",
        "pr",
        "--out",
        str(out),
        "--json",
    ]
    assert mine.main(args) == 0
    operation = json.loads(capsys.readouterr().out)
    assert operation["status"] == "bound" and not operation["optimization_verified"]
    assert read(out)["source"]["analysis_id"] == value.analysis["analysis_id"]
    assert operation["next_actions"][0][:3] == ["engineering-ci", "checks", "--binding"]
    monkeypatch.setattr(
        mine_bind,
        "file_evidence",
        lambda *args: pytest.fail("Output collision must be rejected first"),
    )
    assert mine.main(args) == 2
    retry = json.loads(capsys.readouterr().out)
    assert retry["status"] == "output_exists" and not retry["binding_saved"]
    assert str(out) not in retry["next_actions"][0]
    assert read(out) == binding()


def test_bind_cli_requires_explicit_target_and_projection(capsys):
    with pytest.raises(SystemExit):
        mine.main(
            [
                "bind",
                "analysis.json",
                "--candidate",
                "a" * 64,
                "--profile",
                "pr",
                "--out",
                "unused.json",
            ]
        )
    assert "--projection" in capsys.readouterr().err


def run_bound(checkout, monkeypatch, *, process=None, executor="native"):
    """Use the production CLI and runner, with only child processes replaced."""
    path = checkout / "binding.json"
    path.write_bytes(canonical(binding()))
    monkeypatch.setattr(checks, "ROOT", checkout)
    if executor == "native":
        invoke = checks.execute
        monkeypatch.setattr(
            checks,
            "run_checks",
            lambda selectors, **kw: invoke(
                selectors,
                root=checkout,
                run=process or fake_process(),
                environ={},
                **{k: v for k, v in kw.items() if k != "executor"},
            ),
        )
    args = ["--binding", str(path), "--reuse", "--json", str(checkout / "result.json")]
    return args


def test_bound_pr_executes_then_reuses_all_five_without_duplicate_processes(
    checkout, monkeypatch
):
    calls = []
    process = fake_process(action=lambda args, kw: calls.append(args))
    args = run_bound(checkout, monkeypatch, process=process)
    assert checks.main(args) == 0 and len(calls) == 5
    report = checks.read_report(checkout / "result.json")
    assert report["handler_binding"] == binding()
    assert checks.main(args) == 0 and len(calls) == 5
    report = checks.read_report(checkout / "result.json")
    assert all(
        x["reused"] and not x["executed_this_invocation"] for x in report["checks"]
    )
    (checkout / "tests/test_new.py").write_text("def test_new(): pass\n")
    assert checks.main(args) == 0 and len(calls) == 6
    assert calls[-1][0:2] == ["uv", "run"] and "pytest" in calls[-1]


def test_failed_bound_check_remains_failed_when_reused(checkout, monkeypatch):
    calls = []
    args = run_bound(
        checkout,
        monkeypatch,
        process=fake_process(7, lambda args, kw: calls.append(args)),
    )
    assert checks.main(args) == 7
    assert checks.main(args) == 7 and len(calls) == 5
    report = checks.read_report(checkout / "result.json")
    assert report["overall_status"] == "failed" and report["exit_code"] == 7
    assert all(x["status"] == "failed" for x in report["checks"])


def test_bound_execution_does_not_hide_input_change_or_missing_tool(
    checkout, monkeypatch
):
    def change(args, kw):
        (checkout / "tests/test_new.py").write_text("# changed\n")

    args = run_bound(checkout, monkeypatch, process=fake_process(action=change))
    assert checks.main(args) == 3
    report = checks.read_report(checkout / "result.json")
    assert report["checks"][2]["status"] == "stale"
    assert not report["checks"][2]["passed_for_current_input"]

    def missing(*args):
        raise checks.PreconditionError("missing_tool", "uv unavailable")

    monkeypatch.setattr(checks, "preflight", missing)
    assert checks.main(args) == 127
    assert all(
        x["status"] == "missing_tool"
        for x in checks.read_report(checkout / "result.json")["checks"]
    )


def test_binding_rejects_selector_override_and_output_collisions(checkout, monkeypatch):
    args = run_bound(checkout, monkeypatch)
    assert checks.main(["ruff", *args]) == 3
    assert checks.main([*args, "--junit", str(checkout / "binding.json")]) == 3
    assert (
        checks.main(
            [
                "--binding",
                str(checkout / "binding.json"),
                "--reuse",
                "--json",
                str(checkout / "binding.json"),
            ]
        )
        == 3
    )
    assert read(checkout / "binding.json") == binding()
    assert not (checkout / "result.json").exists()


def test_binding_save_failure_keeps_original_failure_exit(checkout, monkeypatch):
    args = run_bound(checkout, monkeypatch, process=fake_process(7))
    write = checks.atomic_json

    def fail_association(path, value):
        if "handler_binding" in value:
            raise OSError("cannot save")
        write(path, value)

    monkeypatch.setattr(checks, "atomic_json", fail_association)
    assert checks.main(args) == 7
    report = checks.read_report(checkout / "result.json")
    assert report["exit_code"] == 7 and "handler_binding" not in report


def test_bound_cli_uses_same_dagger_executor_and_receipts(checkout, monkeypatch):
    from test_dagger_checks import TOOLS, Dagger

    from engineering_ci import dagger_checks

    (checkout / "dagger.json").write_text('{"engineVersion":"v1.0.0-beta.16"}')
    (checkout / "dagger.lock").write_text("# lock\n")
    backend = Dagger(checkout, monkeypatch)
    monkeypatch.setattr(checks, "preflight", lambda *args: copy.deepcopy(TOOLS))
    args = run_bound(checkout, monkeypatch, executor="dagger")
    invoke = dagger_checks.execute
    monkeypatch.setattr(
        checks,
        "run_checks",
        lambda selectors, **kw: invoke(
            selectors,
            root=checkout,
            run=backend.run,
            which=backend.which,
            environ={},
            **{k: v for k, v in kw.items() if k != "executor"},
        ),
    )
    assert checks.main(args) == 0 and len(backend.exports) == 1
    assert checks.main(args) == 0 and len(backend.exports) == 1
    report = checks.read_report(checkout / "result.json")
    assert report["handler_binding"] == binding()
    assert all(x["reused"] for x in report["checks"])
