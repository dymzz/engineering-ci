"""#66-C Expected -> Routing -> Native Result reconciliation tests."""

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from engineering_ci import (
    check_contract,
    expected_checks,
    reconciliation,
    selection,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/selection-app"


def run(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _attach_effective_profile_policy(path, profile):
    profile.pop("governed_rules", None)
    declared_domain = (profile.get("approval_binding") or {}).get("declared_domain")
    if isinstance(declared_domain, dict):
        declared_domain.pop("governed_rules", None)
    binding = profile["approval_binding"]
    binding["approval_digest"] = expected_checks._approval_digest(profile)
    profile["profile_sha256"] = expected_checks.profile_digest(profile)
    declared = profile.get("declared_checks") or []
    config_path = Path(path)
    existing = config_path.read_text(encoding="utf-8")
    existing = existing.split("\n[effective_profile]", 1)[0].rstrip()
    config_path.write_text(
        existing
        + f"""

[effective_profile]
profile_ref = {{ name = "{profile["profile_ref"]["name"]}", version = "{profile["profile_ref"]["version"]}" }}
profile_revision = "{profile["profile_revision"]}"
approval_source = "{binding["approval_source"]}"
approval_digest = "{binding["approval_digest"]}"
effective_at = "{profile["effective_at"]}"
declared_checks = {json.dumps(declared)}
""",
        encoding="utf-8",
    )
    return selection.load_config(config_path)


EVENT = {
    "base_ref": "refs/heads/main",
    "head_ref": "refs/heads/topic",
    "base_commit": "1" * 40,
    "head_commit": "2" * 40,
    "references": [],
}
WEAK_SOURCE = {
    "snapshot": "worktree",
    "declared_target_tree": None,
    "actual_execution_source": "unknown",
    "verified_input_scope": ["docs/guide.md"],
    "scope_kind": "changed_paths",
    "binding_status": "unknown",
}
STRONG_SOURCE = check_contract.source_ref(
    "worktree",
    declared_target_tree="3" * 40,
    actual_execution_source="worktree",
    content_digest="c" * 64,
    verified_scope=["docs/guide.md"],
    scope_kind="check_input_closure",
)
SELECTION_RULE = {
    "name": "engineering-ci.selection",
    "version": "1",
    "sha256": "a" * 64,
}
RULE_TACH = check_contract.rule_ref("tach", 1, digest="b" * 64)
RULE_VULTURE = check_contract.rule_ref("vulture", 1, digest="c" * 64)
RULE_DOCS = check_contract.rule_ref("docs", 1, digest="d" * 64)
RULE_WS = check_contract.rule_ref("whitespace", 1, digest="e" * 64)
RULE_FWD = check_contract.rule_ref("hygiene", 1, digest="f" * 64)
RULE_WT = check_contract.rule_ref("weaver", 1, digest="0" * 64)
RULE_SOURCE_REF = {"name": "hygiene-rule", "version": "1", "sha256": "4" * 64}
SOURCE_RECEIPT_REF = {"name": "p0-source-receipt", "version": "1", "sha256": "5" * 64}
BASELINE_SOURCE_REF = {"name": "baseline", "version": "1", "sha256": "6" * 64}
PROFILE_REF = {"name": "org/engineering", "version": "1"}
PROFILE_SOURCE = "org-governance"
PROFILE = {
    "profile_ref": PROFILE_REF,
    "profile_revision": "1",
    "approval_source": PROFILE_SOURCE,
    "profile_sha256": "9" * 64,
    "evaluated_at": "2026-11-15T00:00:00Z",
}
HYGIENE_RULE = {
    "name": "engineering-ci.hygiene",
    "version": "1",
    "sha256": "7" * 64,
}
SOURCES_RULE = {
    "name": "engineering-ci.sources",
    "version": "1",
    "sha256": "8" * 64,
}
RUN_REF = "run/123"
ATTEMPT_REF = "attempt/1"
RUNNER_REF = "execution:host"
ATTEMPT_QUALIFICATION_REF = {
    "name": "scheduler-attempt",
    "version": "1",
    "sha256": "b" * 64,
}
JOB_ISSUER_REF = {"name": "hook-runner", "version": "1", "sha256": "c" * 64}
FILE_A = {"kind": "changed_path", "path": "docs/a.md"}
FILE_B = {"kind": "changed_path", "path": "docs/b.md"}
CONFIG_REF = {"kind": "config", "path": "ci-hygiene.toml", "sha256": "d" * 64}


def report_for(results):
    return {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [{"name": "reconciled", "native_results": list(results)}],
    }


def report_ref_for(report):
    digest = reconciliation._digest(report).split(":", 1)[1]
    return {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": report["schema_version"],
        "sha256": digest,
    }


def job_receipt_for(
    job_ref,
    run_ref,
    attempt_ref,
    runner_ref,
    report_ref,
    *,
    verification_status="upstream_verified",
):
    return {
        "kind": reconciliation.JOB_RECEIPT_KIND,
        "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
        "job_ref": job_ref,
        "run_ref": run_ref,
        "attempt_ref": attempt_ref,
        "runner_ref": runner_ref,
        "report_ref": copy.deepcopy(report_ref),
        "issuer_ref": dict(JOB_ISSUER_REF),
        "verification_status": verification_status,
    }


def make_entry(
    name="tach",
    *,
    definition=None,
    rule_ref=None,
    baseline_ref=None,
    source=None,
    event=None,
    applicability="selected",
    input_refs=None,
    profile_ref=None,
    selection_rule=None,
    definition_status="resolved",
    **changes,
):
    definition = definition or check_contract.definition_for(name)
    entry = {
        "name": name,
        "check_id": definition.check_id,
        "definition_version": definition.definition_version,
        "definition_status": definition_status,
        "event_ref": copy.deepcopy(event or EVENT),
        "selection_rule_ref": copy.deepcopy(selection_rule or SELECTION_RULE),
        "rule_ref": copy.deepcopy(rule_ref),
        "rule_status": "resolved" if rule_ref is not None else "unknown",
        "profile_ref": copy.deepcopy(profile_ref),
        "applicability": {"status": applicability, "reason": f"{applicability}-test"},
        "policy_status": "unknown",
        "source_ref": copy.deepcopy(source or WEAK_SOURCE),
        "input_refs": list(input_refs or ()),
        "baseline_ref": copy.deepcopy(baseline_ref),
    }
    entry.update(changes)
    entry["expected_check_ref"] = expected_checks.entry_identity(entry)
    return entry


def make_projection(
    checks,
    *,
    status="partial",
    schema_version=1,
    consumer=None,
    event=None,
    source=None,
    selection_rule=None,
    profile_ref=None,
    profile_source=None,
    effective_profile=None,
    hygiene_rule_ref=None,
    sources_rule_ref=None,
    non_check_obligations=(),
    consumer_completeness="not_claimed",
    per_ref_checks=(),
):
    event = event or EVENT
    source = source or WEAK_SOURCE
    selection_rule = selection_rule or SELECTION_RULE
    if schema_version == 1:
        scope_id = expected_checks.scope_identity(
            consumer, profile_ref, profile_source, event, source, selection_rule
        )
        projection = {
            "kind": expected_checks.EXPECTED_KIND,
            "schema_version": 1,
            "projection_source": "check_scope",
        }
    else:
        profile = effective_profile or PROFILE
        scope_id = expected_checks.scope_identity(
            consumer,
            profile_ref,
            profile_source,
            event,
            source,
            selection_rule,
            hygiene_rule_ref,
            sources_rule_ref,
            profile_sha256=profile.get("profile_sha256"),
            evaluated_at=profile.get("evaluated_at"),
        )
        projection = {
            "kind": expected_checks.EXPECTED_KIND,
            "schema_version": 2,
            "projection_source": "effective_profile",
            "effective_profile": profile,
            "authority_status": "verified",
            "trusted_admission_status": "not_evaluated",
            "hygiene_rule_ref": hygiene_rule_ref,
            "sources_rule_ref": sources_rule_ref,
        }
    projection.update(
        {
            "status": status,
            "reason": "test",
            "scope_id": scope_id,
            "selection_rule_ref": copy.deepcopy(selection_rule),
            "expected_set_ref": expected_checks.set_identity(
                checks, non_check_obligations, schema_version=schema_version
            ),
            "definitions_status": "requested_names_complete",
            "applicability_status": "requested_scope",
            "consumer": consumer,
            "profile_ref": copy.deepcopy(profile_ref),
            "profile_source": profile_source,
            "consumer_completeness": consumer_completeness,
            "observed_input_closure_status": "not_observed",
            "event_ref": copy.deepcopy(event),
            "source_ref": copy.deepcopy(source),
            "checks": checks,
            "non_check_obligations": list(non_check_obligations),
            "missing": [],
            "conflicts": [],
        }
    )
    if per_ref_checks:
        projection["per_ref"] = list(per_ref_checks)
    return projection


def make_v2_per_ref_projection(checks, *, status="partial"):
    ref_scope_id = expected_checks.scope_identity(
        None,
        PROFILE_REF,
        PROFILE_SOURCE,
        EVENT,
        WEAK_SOURCE,
        SELECTION_RULE,
        HYGIENE_RULE,
        SOURCES_RULE,
        profile_sha256=PROFILE["profile_sha256"],
        evaluated_at=PROFILE["evaluated_at"],
    )
    ref = {
        "source_ref": "refs/heads/topic",
        "local_ref": "refs/heads/topic",
        "remote_ref": "origin",
        "ref_action": "update",
        "force_push": False,
        "baseline_status": "ok",
        "event_ref": copy.deepcopy(EVENT),
        "source": copy.deepcopy(WEAK_SOURCE),
        "scope_id": ref_scope_id,
        "expected_set_ref": expected_checks.set_identity(checks, (), schema_version=2),
        "checks": checks,
        "non_check_obligations": [],
        "status": status,
    }
    projection = make_projection(
        [],
        status="partial",
        schema_version=2,
        profile_ref=PROFILE_REF,
        profile_source=PROFILE_SOURCE,
        hygiene_rule_ref=HYGIENE_RULE,
        sources_rule_ref=SOURCES_RULE,
        consumer_completeness="per_ref_container_only",
        per_ref_checks=[ref],
    )
    return projection, ref_scope_id


def make_resolution(
    entry,
    *,
    rule=RULE_TACH,
    source=STRONG_SOURCE,
    baseline=None,
    baseline_source=BASELINE_SOURCE_REF,
    input_refs=None,
    coverage=None,
    coverage_by_requirement=None,
    phase=None,
    provenance=True,
):
    resolution = {
        "expected_check_ref": entry["expected_check_ref"],
        "rule_ref": copy.deepcopy(rule),
        "source_ref": copy.deepcopy(source),
        "baseline_ref": copy.deepcopy(baseline),
    }
    if provenance:
        resolution["rule_source_ref"] = dict(RULE_SOURCE_REF)
        resolution["source_receipt_ref"] = dict(SOURCE_RECEIPT_REF)
    if baseline is not None:
        resolution["baseline_source_ref"] = dict(baseline_source)
    if input_refs is not None:
        resolution["input_refs"] = [dict(item) for item in input_refs]
    if coverage is not None:
        resolution["coverage"] = copy.deepcopy(coverage)
    if coverage_by_requirement is not None:
        resolution["coverage_by_requirement"] = copy.deepcopy(coverage_by_requirement)
    if phase is not None:
        resolution["phase"] = phase
    return resolution


def make_routing(
    entry,
    *,
    jobs=("job-1",),
    status="routed",
    reason=None,
    instances=None,
    run_ref=RUN_REF,
    attempt_ref=ATTEMPT_REF,
    runner_ref=RUNNER_REF,
    report_ref=None,
    eligibility="eligible",
    qualification_ref=ATTEMPT_QUALIFICATION_REF,
):
    if instances is None:
        routed_jobs = list(jobs) if status == "routed" else []
        instances = []
        for job in routed_jobs:
            instance = {
                "job_ref": job,
                "run_ref": run_ref,
                "attempt_ref": attempt_ref,
                "runner_ref": runner_ref,
                "attempt_eligibility": eligibility,
            }
            if eligibility != "unknown":
                instance["qualification_ref"] = dict(qualification_ref)
            if report_ref is not None:
                instance["report_ref"] = copy.deepcopy(report_ref)
            instances.append(instance)
    return {
        "expected_check_ref": entry["expected_check_ref"],
        "job_refs": list(jobs),
        "job_instances": instances,
        "mapping_status": status,
        "scheduler_ref": {"name": "lefthook", "version": "2.2.2"},
        "effective_config_ref": {"name": "hook-job", "version": "1"},
        "reason": reason,
    }


def make_observation(
    job_ref,
    results,
    *,
    run_ref=RUN_REF,
    attempt_ref=ATTEMPT_REF,
    runner_ref=RUNNER_REF,
    report=None,
    report_ref=None,
    job_receipt=None,
    include_job_receipt=True,
    verification_status="upstream_verified",
):
    results = list(results)
    if report is None:
        report = report_for(results)
    if report_ref is None:
        report_ref = report_ref_for(report)
    observation = {
        "job_ref": job_ref,
        "run_ref": run_ref,
        "attempt_ref": attempt_ref,
        "runner_ref": runner_ref,
        "report": report,
        "report_ref": copy.deepcopy(report_ref),
        "native_results": results,
    }
    if (
        job_receipt is None
        and include_job_receipt
        and run_ref
        and attempt_ref
        and runner_ref
    ):
        job_receipt = job_receipt_for(
            job_ref,
            run_ref,
            attempt_ref,
            runner_ref,
            report_ref,
            verification_status=verification_status,
        )
    if job_receipt is not None:
        observation["job_receipt"] = copy.deepcopy(job_receipt)
    return observation


def native(
    name="tach",
    *,
    definition=None,
    tool_access=None,
    source=STRONG_SOURCE,
    rule=RULE_TACH,
    baseline=None,
    status="passed",
    evidence=None,
    instance="run-1",
    input_refs=(),
    coverage=None,
    phase=None,
    origin="host",
    runner_ref=RUNNER_REF,
    profile_ref=None,
):
    definition = definition or check_contract.definition_for(name)
    exit_code = 0 if status == "passed" else 1 if status == "failed" else None
    kwargs = {}
    if tool_access is not None:
        kwargs["tool"] = check_contract.tool_ref(tool_access)
    return check_contract.native_result(
        definition,
        source=copy.deepcopy(source),
        status=status,
        result_origin=origin,
        result_instance=instance,
        native_exit_code=exit_code,
        evidence=evidence if evidence is not None else {"stdout": ""},
        rule_ref=copy.deepcopy(rule),
        baseline_ref=copy.deepcopy(baseline),
        input_refs=[dict(item) for item in input_refs],
        coverage=copy.deepcopy(coverage),
        phase=phase,
        runner_ref=runner_ref,
        profile_ref=copy.deepcopy(profile_ref),
        **kwargs,
    )


def reconcile(projection, routing=(), resolutions=(), observations=(), **kwargs):
    return reconciliation.reconcile_expected_results(
        projection,
        routing=list(routing),
        resolutions=list(resolutions),
        observations=list(observations),
        **kwargs,
    )


def matched_setup(*, projection_status="partial", schema_version=1):
    entry = make_entry(
        "tach",
        profile_ref=PROFILE_REF if schema_version == 2 else None,
    )
    projection = make_projection(
        [entry],
        status=projection_status,
        schema_version=schema_version,
        profile_ref=PROFILE_REF if schema_version == 2 else None,
        profile_source=PROFILE_SOURCE if schema_version == 2 else None,
        hygiene_rule_ref=HYGIENE_RULE if schema_version == 2 else None,
        sources_rule_ref=SOURCES_RULE if schema_version == 2 else None,
        consumer_completeness=(
            "expected_domain_complete"
            if projection_status == "complete"
            else "not_claimed"
        ),
    )
    result = native(
        tool_access="tach/check",
        profile_ref=PROFILE_REF if schema_version == 2 else None,
    )
    return (
        entry,
        projection,
        make_routing(entry),
        make_resolution(entry),
        make_observation("job-1", [result]),
        result,
    )


def test_reconciles_matched_witness_without_claiming_authority():
    _entry, projection, routing, resolution, observation, result = matched_setup()
    receipt = reconcile(
        projection,
        routing=[routing],
        resolutions=[resolution],
        observations=[observation],
    )
    assert reconciliation.validate_receipt(receipt) is receipt
    store = receipt["entries"][0]
    assert store["binding_status"] == "matched"
    assert store["selected_result_ids"] == [result["result_id"]]
    assert store["selected_results"][0]["result_origin"] == "host"
    assert store["selected_results"][0]["tool"]["access_id"] == "tach/check"
    assert store["selected_results"][0]["run_ref"] == RUN_REF
    assert store["selected_results"][0]["attempt_ref"] == ATTEMPT_REF
    assert store["selected_results"][0]["report_ref"] == observation["report_ref"]
    assert store["selected_results"][0]["job_receipt_ref"].startswith("sha256:")
    assert store["job_instances"] == [
        {
            "job_ref": "job-1",
            "run_ref": RUN_REF,
            "attempt_ref": ATTEMPT_REF,
            "runner_ref": RUNNER_REF,
            "report_ref": None,
            "attempt_eligibility": "eligible",
            "qualification_ref": ATTEMPT_QUALIFICATION_REF,
            "observed": True,
        }
    ]
    assert store["native_satisfaction"]["status"] == "satisfied"
    assert receipt["status"] == "partial"
    assert receipt["trusted"] is False
    assert receipt["required"] is False
    assert receipt["evidence_admission"] == "not_evaluated"
    assert receipt["required_gate"] == "not_evaluated"
    assert receipt["expected_set_ref"] == projection["expected_set_ref"]
    assert receipt["scope_id"] == projection["scope_id"]
    assert json.dumps(receipt, sort_keys=True)


def test_legal_report_result_ownership_is_matched():
    entry = make_entry("tach")
    projection = make_projection([entry])
    result = native(tool_access="tach/check")
    observation = make_observation("job-1", [result])
    assert observation["report_ref"] == report_ref_for(observation["report"])
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry)],
        observations=[observation],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "matched"
    assert store["selected_results"][0]["report_ref"] == observation["report_ref"]
    assert store["selected_results"][0]["job_receipt_ref"] == reconciliation._digest(
        observation["job_receipt"]
    )


def test_report_digest_or_result_membership_mismatch_is_refused():
    entry = make_entry("tach")
    projection = make_projection([entry])
    result = native(tool_access="tach/check")
    observation = make_observation("job-1", [result])

    tampered_ref = copy.deepcopy(observation)
    tampered_ref["report_ref"] = report_ref_for(report_for([]))
    with pytest.raises(
        reconciliation.ReconciliationError, match="observation_report_digest_mismatch"
    ):
        reconcile(projection, observations=[tampered_ref])

    other = native(tool_access="tach/check", instance="other")
    tampered_results = copy.deepcopy(observation)
    tampered_results["native_results"] = [other]
    with pytest.raises(
        reconciliation.ReconciliationError,
        match="observation_report_results_mismatch",
    ):
        reconcile(projection, observations=[tampered_results])


def test_wrapped_result_without_upstream_attribution_is_unknown():
    entry = make_entry("tach")
    projection = make_projection([entry])
    result = native(tool_access="tach/check")
    # All self-reported Run/Attempt/runner/report fields are internally
    # consistent, but no verified Job/Execution Report receipt proves the
    # Result belongs to this attempt.
    observation = make_observation("job-1", [result], include_job_receipt=False)
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry)],
        observations=[observation],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "unknown"
    assert store["reason"] == "job_attribution_unproven"
    assert store["selected_result_ids"] == []

    unverified = make_observation("job-1", [result], verification_status="unverified")
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry)],
        observations=[unverified],
    )
    assert receipt["entries"][0]["binding_status"] == "unknown"
    assert receipt["entries"][0]["reason"] == "job_attribution_unproven"


def test_superseded_pass_cannot_mask_terminal_failure():
    entry = make_entry("tach")
    projection = make_projection([entry])
    passed = native(tool_access="tach/check", instance="attempt-1")
    failed = native(tool_access="tach/check", instance="attempt-2", status="failed")
    instances = [
        {
            "job_ref": "job-1",
            "run_ref": RUN_REF,
            "attempt_ref": "attempt/1",
            "runner_ref": RUNNER_REF,
            "attempt_eligibility": "superseded",
            "qualification_ref": ATTEMPT_QUALIFICATION_REF,
        },
        {
            "job_ref": "job-1",
            "run_ref": RUN_REF,
            "attempt_ref": "attempt/2",
            "runner_ref": RUNNER_REF,
            "attempt_eligibility": "eligible",
            "qualification_ref": ATTEMPT_QUALIFICATION_REF,
        },
    ]
    receipt = reconcile(
        projection,
        routing=[make_routing(entry, jobs=("job-1",), instances=instances)],
        resolutions=[make_resolution(entry)],
        observations=[
            make_observation("job-1", [passed], attempt_ref="attempt/1"),
            make_observation("job-1", [failed], attempt_ref="attempt/2"),
        ],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "insufficient"
    assert store["reason"] == "result_not_passed"
    assert store["native_status"] == "failed"
    assert store["selected_result_ids"] == []
    assert store["inactive_attempts"] == [
        {
            "job_ref": "job-1",
            "run_ref": RUN_REF,
            "attempt_ref": "attempt/1",
            "attempt_eligibility": "superseded",
            "observed": True,
            "result_ids": [passed["result_id"]],
        }
    ]


def test_effective_attempt_pass_is_matched():
    entry = make_entry("tach")
    projection = make_projection([entry])
    failed = native(tool_access="tach/check", instance="attempt-1", status="failed")
    passed = native(tool_access="tach/check", instance="attempt-2")
    instances = [
        {
            "job_ref": "job-1",
            "run_ref": RUN_REF,
            "attempt_ref": "attempt/1",
            "runner_ref": RUNNER_REF,
            "attempt_eligibility": "superseded",
            "qualification_ref": ATTEMPT_QUALIFICATION_REF,
        },
        {
            "job_ref": "job-1",
            "run_ref": RUN_REF,
            "attempt_ref": "attempt/2",
            "runner_ref": RUNNER_REF,
            "attempt_eligibility": "eligible",
            "qualification_ref": ATTEMPT_QUALIFICATION_REF,
        },
    ]
    receipt = reconcile(
        projection,
        routing=[make_routing(entry, jobs=("job-1",), instances=instances)],
        resolutions=[make_resolution(entry)],
        observations=[
            make_observation("job-1", [failed], attempt_ref="attempt/1"),
            make_observation("job-1", [passed], attempt_ref="attempt/2"),
        ],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "matched"
    assert store["selected_result_ids"] == [passed["result_id"]]
    assert store["inactive_attempts"][0]["attempt_ref"] == "attempt/1"


def test_unqualified_or_ambiguous_attempts_stay_unknown():
    entry = make_entry("tach")
    projection = make_projection([entry])
    result = native(tool_access="tach/check")

    unknown_eligibility = [
        {
            "job_ref": "job-1",
            "run_ref": RUN_REF,
            "attempt_ref": "attempt/1",
            "runner_ref": RUNNER_REF,
            "attempt_eligibility": "unknown",
        }
    ]
    receipt = reconcile(
        projection,
        routing=[make_routing(entry, jobs=("job-1",), instances=unknown_eligibility)],
        resolutions=[make_resolution(entry)],
        observations=[make_observation("job-1", [result])],
    )
    assert receipt["entries"][0]["binding_status"] == "unknown"
    assert receipt["entries"][0]["reason"] == "effective_attempt_unqualified"

    both_eligible = [
        {
            "job_ref": "job-1",
            "run_ref": RUN_REF,
            "attempt_ref": f"attempt/{index}",
            "runner_ref": RUNNER_REF,
            "attempt_eligibility": "eligible",
            "qualification_ref": ATTEMPT_QUALIFICATION_REF,
        }
        for index in (1, 2)
    ]
    receipt = reconcile(
        projection,
        routing=[make_routing(entry, jobs=("job-1",), instances=both_eligible)],
        resolutions=[make_resolution(entry)],
        observations=[
            make_observation("job-1", [result], attempt_ref="attempt/1"),
            make_observation("job-1", [result], attempt_ref="attempt/2"),
        ],
    )
    assert receipt["entries"][0]["binding_status"] == "unknown"
    assert receipt["entries"][0]["reason"] == "multiple_eligible_attempts"


def test_complete_expected_with_all_matched_is_receipt_matched():
    _entry, projection, routing, resolution, observation, _result = matched_setup(
        projection_status="complete", schema_version=2
    )
    receipt = reconcile(
        projection,
        routing=[routing],
        resolutions=[resolution],
        observations=[observation],
    )
    assert receipt["status"] == "matched"
    assert receipt["all_observations_reconciled"] is True
    assert receipt["reconciled_scope"] == "declared_expected_instances"
    assert receipt["entries"][0]["binding_status"] == "matched"


def test_receipt_status_distinguishes_declared_scope_from_all_observations():
    entry = make_entry("tach", profile_ref=PROFILE_REF)
    projection = make_projection(
        [entry],
        status="complete",
        schema_version=2,
        profile_ref=PROFILE_REF,
        profile_source=PROFILE_SOURCE,
        hygiene_rule_ref=HYGIENE_RULE,
        sources_rule_ref=SOURCES_RULE,
        consumer_completeness="expected_domain_complete",
    )
    expected_result = native(tool_access="tach/check", profile_ref=PROFILE_REF)
    other_source = check_contract.source_ref(
        "worktree",
        declared_target_tree="3" * 40,
        actual_execution_source="worktree",
        content_digest="f" * 64,
        verified_scope=["docs/guide.md"],
        scope_kind="check_input_closure",
    )
    wrong_object = native(
        tool_access="tach/check",
        source=other_source,
        instance="wrong-object",
        profile_ref=PROFILE_REF,
    )
    observation = make_observation("job-1", [expected_result, wrong_object])
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry)],
        observations=[observation],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "matched"
    assert store["selected_result_ids"] == [expected_result["result_id"]]
    assert store["rejected_results"][0]["reason"] == "source_mismatch"
    assert receipt["reconciled_scope"] == "declared_expected_instances"
    assert receipt["all_observations_reconciled"] is False
    assert receipt["status"] == "partial"
    assert receipt["summary"]["rejected_results"] == 1
    assert receipt["summary"]["rejected_observations"] == 0


def test_legal_empty_complete_expected_is_matched_without_required_claim():
    projection = make_projection(
        [],
        status="complete",
        schema_version=2,
        profile_ref=PROFILE_REF,
        profile_source=PROFILE_SOURCE,
        hygiene_rule_ref=HYGIENE_RULE,
        sources_rule_ref=SOURCES_RULE,
        consumer_completeness="expected_domain_complete",
    )
    receipt = reconcile(projection)
    assert receipt["status"] == "matched"
    assert receipt["summary"]["expected"] == 0
    assert receipt["trusted"] is False
    assert receipt["required"] is False


def test_empty_partial_expected_is_not_a_legal_empty_set():
    projection = make_projection([], status="partial")
    receipt = reconcile(projection)
    assert receipt["status"] == "unknown"
    assert receipt["summary"]["expected"] == 0
    assert receipt["expected_status"] == "partial"


def test_unrouted_expected_is_retained_not_deleted():
    entry = make_entry("tach")
    projection = make_projection([entry])
    receipt = reconcile(projection)
    assert receipt["entries"][0]["binding_status"] == "unrouted"
    assert receipt["entries"][0]["job_refs"] == []
    assert receipt["status"] == "unrouted"
    assert receipt["summary"]["unrouted"] == 1


@pytest.mark.parametrize("mapping_status", ["filtered", "not_configured", "skipped"])
def test_filtered_or_unconfigured_job_is_missing(mapping_status):
    entry = make_entry("tach")
    projection = make_projection([entry])
    receipt = reconcile(
        projection,
        routing=[make_routing(entry, jobs=(), status=mapping_status)],
    )
    assert receipt["entries"][0]["binding_status"] == "missing"
    assert receipt["entries"][0]["reason"] == f"job_{mapping_status}"


def test_routed_job_without_native_result_is_missing():
    entry = make_entry("tach")
    projection = make_projection([entry])
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        observations=[make_observation("job-1", [])],
    )
    assert receipt["entries"][0]["binding_status"] == "missing"
    assert receipt["entries"][0]["reason"] == "native_result_missing"


def test_unresolved_resolution_keeps_observed_fact_unknown():
    entry = make_entry("tach")
    projection = make_projection([entry])
    result = native(tool_access="tach/check")
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        observations=[make_observation("job-1", [result])],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "unknown"
    assert store["reason"] == "resolution_missing"
    assert store["observed_result_ids"] == [result["result_id"]]
    assert store["selected_result_ids"] == []
    assert store["native_status"] == "passed"


def test_resolution_without_independent_provenance_is_unknown():
    entry = make_entry("tach")
    projection = make_projection([entry])
    result = native(tool_access="tach/check")
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry, provenance=False)],
        observations=[make_observation("job-1", [result])],
    )
    assert receipt["entries"][0]["binding_status"] == "unknown"
    assert receipt["entries"][0]["reason"] == "resolution_evidence_missing"


def test_rule_source_and_baseline_evidence_missing_stay_unknown():
    entry = make_entry("tach")
    projection = make_projection([entry])
    result = native(tool_access="tach/check")
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry, rule=None)],
        observations=[make_observation("job-1", [result])],
    )
    assert receipt["entries"][0]["binding_status"] == "unknown"
    assert receipt["entries"][0]["reason"] == "expected_rule_unproven"

    weak = copy.deepcopy(WEAK_SOURCE)
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry, source=weak)],
        observations=[make_observation("job-1", [result])],
    )
    assert receipt["entries"][0]["binding_status"] == "unknown"
    assert receipt["entries"][0]["reason"] == "expected_source_unproven"

    ws_entry = make_entry("whitespace", rule_ref=None, baseline_ref=None)
    ws_projection = make_projection([ws_entry])
    ws_result = native(
        "whitespace",
        tool_access="git/diff-check",
        rule=RULE_WS,
        baseline={"base_commit": "1" * 40},
    )
    receipt = reconcile(
        ws_projection,
        routing=[make_routing(ws_entry)],
        resolutions=[make_resolution(ws_entry, rule=RULE_WS)],
        observations=[make_observation("job-1", [ws_result])],
    )
    assert receipt["entries"][0]["binding_status"] == "unknown"
    assert receipt["entries"][0]["reason"] == "expected_baseline_missing"


def test_rule_conflict_with_immutable_expected_is_insufficient():
    entry = make_entry("tach", rule_ref=RULE_TACH)
    projection = make_projection([entry])
    result = native(tool_access="tach/check")
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry, rule=RULE_VULTURE)],
        observations=[make_observation("job-1", [result])],
    )
    assert receipt["entries"][0]["binding_status"] == "insufficient"
    assert receipt["entries"][0]["reason"] == "expected_rule_mismatch"


def test_source_identity_conflict_is_insufficient():
    source = {**WEAK_SOURCE, "declared_target_tree": "9" * 40}
    entry = make_entry("tach", source=source)
    projection = make_projection([entry], source=source)
    result = native(tool_access="tach/check")
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry)],
        observations=[make_observation("job-1", [result])],
    )
    assert receipt["entries"][0]["binding_status"] == "insufficient"
    assert (
        receipt["entries"][0]["reason"]
        == "expected_source_declared_target_tree_mismatch"
    )


def test_same_tree_with_other_old_to_new_baseline_is_not_shared():
    entry = make_entry(
        "whitespace",
        rule_ref=RULE_WS,
        baseline_ref={"base_commit": "1" * 40},
    )
    projection = make_projection([entry])
    result = native(
        "whitespace",
        tool_access="git/diff-check",
        rule=RULE_WS,
        baseline={"base_commit": "2" * 40},
    )
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[
            make_resolution(
                entry,
                rule=RULE_WS,
                baseline={"base_commit": "2" * 40},
            )
        ],
        observations=[make_observation("job-1", [result])],
    )
    assert receipt["entries"][0]["binding_status"] == "insufficient"
    assert receipt["entries"][0]["reason"] == "expected_baseline_mismatch"
    assert receipt["entries"][0]["selected_result_ids"] == []


def test_native_failure_is_insufficient_and_keeps_failed_status():
    entry = make_entry("tach")
    projection = make_projection([entry])
    failed = native(tool_access="tach/check", status="failed")
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry)],
        observations=[make_observation("job-1", [failed])],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "insufficient"
    assert store["reason"] == "result_not_passed"
    assert store["native_status"] == "failed"
    assert store["selected_result_ids"] == []


def test_failed_or_unselected_result_cannot_complete_input_or_coverage():
    config = {"kind": "config", "path": "ci-hygiene.toml", "sha256": "d" * 64}
    coverage = {"modules": ["engineering_ci"]}
    entry = make_entry("tach")
    projection = make_projection([entry])
    passing_without_inputs = native(
        tool_access="tach/check", instance="passed-no-input"
    )
    failed_with_inputs = native(
        tool_access="tach/check",
        instance="failed-with-input",
        status="failed",
        input_refs=[config],
        coverage=coverage,
    )
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry, input_refs=[config], coverage=coverage)],
        observations=[
            make_observation("job-1", [passing_without_inputs, failed_with_inputs])
        ],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "insufficient"
    assert store["reason"] in {
        "expected_inputs_uncovered",
        "expected_coverage_unproven",
    }
    assert store["selected_result_ids"] == []
    assert store["native_status"] == "passed"


def test_expected_input_refs_cannot_be_dropped_by_resolution():
    entry = make_entry("tach", input_refs=[FILE_A, FILE_B])
    projection = make_projection([entry])
    partial = native(tool_access="tach/check", input_refs=[FILE_A])
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry)],
        observations=[make_observation("job-1", [partial])],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "insufficient"
    assert store["reason"] == "expected_inputs_uncovered"
    assert store["selected_result_ids"] == []

    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry, input_refs=[FILE_A])],
        observations=[make_observation("job-1", [partial])],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "insufficient"
    assert store["reason"] == "expected_inputs_reduced"

    complete = native(
        tool_access="tach/check",
        input_refs=[FILE_A, FILE_B, CONFIG_REF],
    )
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry, input_refs=[FILE_A, FILE_B, CONFIG_REF])],
        observations=[make_observation("job-1", [complete])],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "matched"
    assert store["selected_result_ids"] == [complete["result_id"]]

    # Omitting resolution inputs keeps the immutable Expected inputs as a floor.
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry)],
        observations=[make_observation("job-1", [complete])],
    )
    assert receipt["entries"][0]["binding_status"] == "matched"


def test_missing_or_different_profile_binding_is_not_matched():
    entry = make_entry("tach", profile_ref=PROFILE_REF)
    projection = make_projection(
        [entry],
        schema_version=2,
        profile_ref=PROFILE_REF,
        profile_source=PROFILE_SOURCE,
        hygiene_rule_ref=HYGIENE_RULE,
        sources_rule_ref=SOURCES_RULE,
    )
    routing = make_routing(entry)
    resolution = make_resolution(entry)

    missing = native(tool_access="tach/check")
    receipt = reconcile(
        projection,
        routing=[routing],
        resolutions=[resolution],
        observations=[make_observation("job-1", [missing])],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "unknown"
    assert store["reason"] == "expected_profile_binding_missing"
    assert store["rejected_results"][0]["reason"] == "profile_binding_missing"

    other_profile = {"name": "org/other", "version": "1"}
    different = native(tool_access="tach/check", profile_ref=other_profile)
    receipt = reconcile(
        projection,
        routing=[routing],
        resolutions=[resolution],
        observations=[make_observation("job-1", [different])],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "unknown"
    assert store["reason"] == "expected_profile_mismatch"
    assert store["rejected_results"][0]["reason"] == "profile_mismatch"

    bound = native(tool_access="tach/check", profile_ref=PROFILE_REF)
    receipt = reconcile(
        projection,
        routing=[routing],
        resolutions=[resolution],
        observations=[make_observation("job-1", [bound])],
    )
    assert receipt["entries"][0]["binding_status"] == "matched"


def test_unprofiled_expected_does_not_invent_profile_constraint():
    entry = make_entry("tach")
    projection = make_projection([entry])
    result = native(
        tool_access="tach/check",
        profile_ref={"name": "org/other", "version": "1"},
    )
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry)],
        observations=[make_observation("job-1", [result])],
    )
    assert receipt["entries"][0]["binding_status"] == "matched"


def test_expected_rule_and_baseline_can_stand_without_duplicate_evidence():
    entry = make_entry(
        "whitespace",
        rule_ref=RULE_WS,
        baseline_ref={"base_commit": "1" * 40},
    )
    projection = make_projection([entry])
    result = native(
        "whitespace",
        tool_access="git/diff-check",
        rule=RULE_WS,
        baseline={"base_commit": "1" * 40},
    )
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry, rule=None, baseline=None)],
        observations=[make_observation("job-1", [result])],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "matched"
    assert store["evidence_refs"]["rule_source_ref"] == RULE_SOURCE_REF
    assert "baseline_source_ref" not in store["evidence_refs"]


def test_definition_version_mismatch_is_insufficient():
    entry = make_entry("docs")
    projection = make_projection([entry])
    legacy_definition = check_contract.definition_for("docs", 1)
    legacy_result = native(
        "docs",
        definition=legacy_definition,
        rule=RULE_DOCS,
        evidence={"stdout": "legacy"},
    )
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry, rule=RULE_DOCS)],
        observations=[make_observation("job-1", [legacy_result])],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "insufficient"
    assert store["reason"] == "definition_version_mismatch"
    assert store["observed_result_ids"] == [legacy_result["result_id"]]
    assert store["selected_result_ids"] == []
    reconciliation.validate_receipt(receipt)


def test_two_required_tools_missing_one_is_insufficient():
    entry = make_entry("docs")
    projection = make_projection([entry])
    inventory = native(
        "docs",
        tool_access="git/ls-files",
        rule=RULE_DOCS,
        evidence={"stdout": "docs/guide.md"},
    )
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry, rule=RULE_DOCS)],
        observations=[make_observation("job-1", [inventory])],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "insufficient"
    assert store["reason"] == "result_missing"
    assert store["selected_result_ids"] == []


def test_one_job_covers_multiple_checks_and_one_check_spans_jobs():
    tach = make_entry("tach")
    vulture = make_entry("vulture")
    projection = make_projection([tach, vulture])
    tach_result = native(tool_access="tach/check")
    vulture_result = native(
        "vulture",
        tool_access="vulture/dead-code",
        rule=RULE_VULTURE,
    )
    receipt = reconcile(
        projection,
        routing=[make_routing(tach), make_routing(vulture)],
        resolutions=[
            make_resolution(tach),
            make_resolution(vulture, rule=RULE_VULTURE),
        ],
        observations=[make_observation("job-1", [tach_result, vulture_result])],
    )
    assert [item["binding_status"] for item in receipt["entries"]] == [
        "matched",
        "matched",
    ]
    assert receipt["summary"]["matched"] == 2

    second = native(tool_access="tach/check", instance="run-2")
    receipt = reconcile(
        projection,
        routing=[make_routing(tach, jobs=("job-1", "job-2"))],
        resolutions=[make_resolution(tach)],
        observations=[
            make_observation("job-1", [tach_result]),
            make_observation("job-2", [second]),
        ],
    )
    assert receipt["entries"][0]["binding_status"] == "matched"
    assert len(receipt["entries"][0]["selected_result_ids"]) == 1


def test_equivalent_expected_instances_share_one_native_witness():
    first = make_entry("tach", applicability="selected")
    first["applicability"]["reason"] = "first"
    first["expected_check_ref"] = expected_checks.entry_identity(first)
    second = make_entry("tach", applicability="selected")
    second["applicability"]["reason"] = "second"
    second["expected_check_ref"] = expected_checks.entry_identity(second)
    projection = make_projection([first, second])
    result = native(tool_access="tach/check")
    receipt = reconcile(
        projection,
        routing=[make_routing(first), make_routing(second)],
        resolutions=[make_resolution(first), make_resolution(second)],
        observations=[make_observation("job-1", [result])],
    )
    assert [item["binding_status"] for item in receipt["entries"]] == [
        "matched",
        "matched",
    ]
    assert receipt["entries"][0]["selected_result_ids"] == [result["result_id"]]
    assert receipt["entries"][1]["selected_result_ids"] == [result["result_id"]]
    assert receipt["summary"]["selected_results"] == 1


def test_equivalent_consumer_scopes_can_reuse_same_native_evidence():
    entry = make_entry("tach")
    result = native(tool_access="tach/check")
    observation = make_observation("job-1", [result])
    routing = make_routing(entry)
    resolution = make_resolution(entry)
    receipts = [
        reconcile(
            make_projection([entry], consumer=consumer),
            routing=[routing],
            resolutions=[resolution],
            observations=[observation],
        )
        for consumer in ("consumer-a", "consumer-b")
    ]
    assert receipts[0]["scope_id"] != receipts[1]["scope_id"]
    assert all(
        receipt["entries"][0]["binding_status"] == "matched" for receipt in receipts
    )
    assert all(
        receipt["entries"][0]["selected_result_ids"] == [result["result_id"]]
        for receipt in receipts
    )


def test_duplicate_observation_is_recorded_without_double_counting():
    entry = make_entry("tach")
    projection = make_projection([entry])
    result = native(tool_access="tach/check")
    receipt = reconcile(
        projection,
        routing=[make_routing(entry, jobs=("job-1", "job-2"))],
        resolutions=[make_resolution(entry)],
        observations=[
            make_observation("job-1", [result]),
            make_observation("job-2", [result]),
        ],
    )
    assert receipt["entries"][0]["binding_status"] == "matched"
    assert receipt["duplicate"] == [
        {
            "result_id": result["result_id"],
            "observations": [
                {"job_ref": "job-1", "run_ref": RUN_REF, "attempt_ref": ATTEMPT_REF},
                {"job_ref": "job-2", "run_ref": RUN_REF, "attempt_ref": ATTEMPT_REF},
            ],
        }
    ]
    assert receipt["summary"]["duplicate"] == 1


def test_wrong_source_observation_is_rejected_as_execution_object():
    entry = make_entry("tach")
    projection = make_projection([entry])
    other_source = check_contract.source_ref(
        "worktree",
        declared_target_tree="3" * 40,
        actual_execution_source="worktree",
        content_digest="f" * 64,
        verified_scope=["docs/guide.md"],
        scope_kind="check_input_closure",
    )
    wrong = native(tool_access="tach/check", source=other_source)
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry)],
        observations=[make_observation("job-1", [wrong])],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "insufficient"
    assert store["reason"] == "execution_object_mismatch"
    assert store["selected_result_ids"] == []
    assert store["rejected_results"] == [
        {
            "result_id": wrong["result_id"],
            "reason": "source_mismatch",
            "job_ref": "job-1",
            "run_ref": RUN_REF,
            "attempt_ref": ATTEMPT_REF,
        }
    ]

    correct = native(tool_access="tach/check", instance="correct")
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry)],
        observations=[make_observation("job-1", [wrong, correct])],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "matched"
    assert store["selected_result_ids"] == [correct["result_id"]]
    assert store["rejected_results"][0]["reason"] == "source_mismatch"


def test_unexpected_observations_are_reported():
    entry = make_entry("tach")
    projection = make_projection([entry])
    tach_result = native(tool_access="tach/check")
    docs_result = native(
        "docs",
        tool_access="git/ls-files",
        rule=RULE_DOCS,
        evidence={"stdout": "docs/guide.md"},
    )
    extra = native(tool_access="tach/check", instance="unrouted")
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry)],
        observations=[
            make_observation("job-1", [tach_result, docs_result]),
            make_observation("job-2", [extra]),
        ],
    )
    reasons = {
        (item["result_id"], item["job_ref"], item["reason"])
        for item in receipt["unexpected"]
    }
    assert (
        docs_result["result_id"],
        "job-1",
        "result_check_not_expected",
    ) in reasons
    assert (extra["result_id"], "job-2", "job_not_routed") in reasons
    assert receipt["entries"][0]["binding_status"] == "matched"
    assert receipt["status"] == "partial"


def test_missing_job_activation_identity_is_unknown():
    entry = make_entry("tach")
    projection = make_projection([entry])
    routing = make_routing(entry)
    routing["job_instances"] = []
    result = native(tool_access="tach/check")
    receipt = reconcile(
        projection,
        routing=[routing],
        resolutions=[make_resolution(entry)],
        observations=[make_observation("job-1", [result])],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "unknown"
    assert store["reason"] == "job_activation_identity_missing"
    assert store["selected_result_ids"] == []


def test_same_job_other_attempt_is_not_matched():
    entry = make_entry("tach")
    projection = make_projection([entry])
    result = native(tool_access="tach/check")
    receipt = reconcile(
        projection,
        routing=[make_routing(entry, attempt_ref="attempt/2")],
        resolutions=[make_resolution(entry)],
        observations=[make_observation("job-1", [result], attempt_ref="attempt/1")],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "missing"
    assert store["reason"] == "native_result_missing"
    assert store["selected_result_ids"] == []
    assert store["job_instances"] == [
        {
            "job_ref": "job-1",
            "run_ref": RUN_REF,
            "attempt_ref": "attempt/2",
            "runner_ref": RUNNER_REF,
            "report_ref": None,
            "attempt_eligibility": "eligible",
            "qualification_ref": ATTEMPT_QUALIFICATION_REF,
            "observed": False,
        }
    ]
    assert receipt["unexpected"] == [
        {
            "result_id": result["result_id"],
            "job_ref": "job-1",
            "run_ref": RUN_REF,
            "attempt_ref": "attempt/1",
            "check_id": "eci/hygiene/tach",
            "reason": "job_instance_not_activated",
        }
    ]


def test_old_run_result_reattached_to_new_job_is_not_matched():
    entry = make_entry("tach")
    projection = make_projection([entry])
    result = native(tool_access="tach/check")
    receipt = reconcile(
        projection,
        routing=[make_routing(entry, run_ref="run/new")],
        resolutions=[make_resolution(entry)],
        observations=[make_observation("job-1", [result], run_ref="run/old")],
    )
    assert receipt["entries"][0]["binding_status"] == "missing"
    assert receipt["unexpected"][0]["reason"] == "job_instance_not_activated"
    assert receipt["unexpected"][0]["run_ref"] == "run/old"


def test_wrong_runner_or_report_identity_is_unknown():
    entry = make_entry("tach")
    projection = make_projection([entry])
    resolution = make_resolution(entry)

    other_runner = "execution:other"
    result = native(tool_access="tach/check", runner_ref=other_runner)
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[resolution],
        observations=[make_observation("job-1", [result], runner_ref=other_runner)],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "unknown"
    assert store["reason"] == "job_execution_identity_mismatch"
    assert store["rejected_observations"][0]["reason"] == "job_runner_mismatch"

    other_report = report_ref_for(report_for([]))
    result = native(tool_access="tach/check")
    receipt = reconcile(
        projection,
        routing=[make_routing(entry, report_ref=other_report)],
        resolutions=[resolution],
        observations=[make_observation("job-1", [result])],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "unknown"
    assert store["reason"] == "job_execution_identity_mismatch"
    assert store["rejected_observations"][0]["reason"] == "job_report_mismatch"


def test_observation_without_run_attempt_identity_is_unknown():
    entry = make_entry("tach")
    projection = make_projection([entry])
    result = native(tool_access="tach/check")
    observation = make_observation(
        "job-1",
        [result],
        run_ref=None,
        attempt_ref=None,
        runner_ref=None,
        report_ref=None,
    )
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry)],
        observations=[observation],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "unknown"
    assert store["reason"] == "job_attribution_unproven"
    assert store["selected_result_ids"] == []
    assert receipt["unexpected"][0]["reason"] == "job_attribution_unproven"


def test_terminal_expected_requires_post_completion_phase_evidence():
    entry = make_entry("weaver_terminal")
    projection = make_projection([entry])
    result = native(
        "weaver_terminal",
        rule=RULE_WT,
        phase="post_completion",
    )
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry, rule=RULE_WT)],
        observations=[make_observation("job-1", [result])],
    )
    store = receipt["entries"][0]
    assert store["binding_status"] == "unknown"
    assert store["reason"] == "expected_post_completion_phase_required"


def test_not_applicable_expected_needs_no_result():
    entry = make_entry("docs", applicability="not_applicable")
    projection = make_projection([entry])
    receipt = reconcile(projection)
    assert receipt["entries"][0]["binding_status"] == "not_applicable"
    assert receipt["summary"]["not_applicable"] == 1


@pytest.mark.parametrize("applicability", ["targeted", "full", "escalated"])
def test_framework_test_applicability_is_reconcilable(applicability):
    entry = make_entry("pytest", applicability=applicability)
    projection = make_projection([entry])
    rule = check_contract.rule_ref("pytest", 1, digest="3" * 64)
    result = native("pytest", tool_access="python/pytest", rule=rule)
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry, rule=rule)],
        observations=[make_observation("job-1", [result])],
    )
    assert receipt["entries"][0]["binding_status"] == "matched"


def test_unresolved_definition_is_unknown():
    entry = make_entry("tach", definition_status="unknown")
    entry["check_id"] = None
    entry["definition_version"] = None
    entry["expected_check_ref"] = expected_checks.entry_identity(entry)
    projection = make_projection([entry])
    receipt = reconcile(projection)
    assert receipt["entries"][0]["binding_status"] == "unknown"
    assert receipt["entries"][0]["reason"] == "check_definition_unresolved"


def test_v2_per_ref_scope_is_reconciled_individually():
    entry = make_entry("tach", profile_ref=PROFILE_REF)
    projection, ref_scope_id = make_v2_per_ref_projection([entry])
    result = native(tool_access="tach/check", profile_ref=PROFILE_REF)
    receipt = reconcile(
        projection,
        ref_scope_id=ref_scope_id,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry)],
        observations=[make_observation("job-1", [result])],
    )
    assert receipt["entries"][0]["binding_status"] == "matched"
    assert receipt["expected_set_ref"] == projection["per_ref"][0]["expected_set_ref"]
    assert receipt["scope_id"] == ref_scope_id
    with pytest.raises(
        reconciliation.ReconciliationError, match="expected_ref_scope_required"
    ):
        reconcile(projection)
    with pytest.raises(
        reconciliation.ReconciliationError, match="expected_ref_scope_unknown"
    ):
        reconcile(projection, ref_scope_id="sha256:" + "0" * 64)


def test_multi_ref_v1_container_is_refused():
    projection = make_projection(
        [make_entry("tach")],
        source={**WEAK_SOURCE, "snapshot": "multi"},
    )
    with pytest.raises(
        reconciliation.ReconciliationError,
        match="expected_multi_ref_requires_per_ref",
    ):
        reconcile(projection)


def test_tampered_expected_identity_is_refused():
    entry = make_entry("tach")
    projection = make_projection([entry])
    changed = copy.deepcopy(projection)
    changed["checks"][0]["name"] = "vulture"
    with pytest.raises(
        reconciliation.ReconciliationError, match="expected_check_ref_mismatch"
    ):
        reconcile(changed)
    changed = copy.deepcopy(projection)
    changed["expected_set_ref"] = "sha256:" + "0" * 64
    with pytest.raises(
        reconciliation.ReconciliationError, match="expected_set_ref_mismatch"
    ):
        reconcile(changed)
    changed = copy.deepcopy(projection)
    changed["scope_id"] = "sha256:" + "0" * 64
    with pytest.raises(
        reconciliation.ReconciliationError, match="expected_scope_id_mismatch"
    ):
        reconcile(changed)


def test_routing_and_observation_input_contract_is_enforced():
    entry = make_entry("tach")
    projection = make_projection([entry])
    unknown_ref = "sha256:" + "0" * 64
    with pytest.raises(
        reconciliation.ReconciliationError, match="routing_expected_unknown"
    ):
        reconcile(
            projection,
            routing=[
                {
                    "expected_check_ref": unknown_ref,
                    "job_refs": ["job-1"],
                    "mapping_status": "routed",
                }
            ],
        )
    with pytest.raises(reconciliation.ReconciliationError, match="routing_duplicate"):
        reconcile(
            projection,
            routing=[make_routing(entry), make_routing(entry)],
        )
    with pytest.raises(
        reconciliation.ReconciliationError, match="routing_jobs_missing"
    ):
        reconcile(projection, routing=[make_routing(entry, jobs=())])
    with pytest.raises(
        reconciliation.ReconciliationError, match="observation_identity_duplicate"
    ):
        reconcile(
            projection,
            observations=[
                make_observation("job-1", []),
                make_observation("job-1", []),
            ],
        )


def test_invalid_native_result_is_refused():
    entry = make_entry("tach")
    projection = make_projection([entry])
    result = native(tool_access="tach/check")
    tampered = copy.deepcopy(result)
    tampered["status"] = "passed"
    tampered["native_exit_code"] = 3
    with pytest.raises(
        reconciliation.ReconciliationError, match="native_result_invalid"
    ):
        reconcile(
            projection,
            observations=[make_observation("job-1", [tampered])],
        )


def test_validate_receipt_rejects_authority_or_identity_tampering():
    _entry, projection, routing, resolution, observation, _result = matched_setup()
    receipt = reconcile(
        projection,
        routing=[routing],
        resolutions=[resolution],
        observations=[observation],
    )
    assert reconciliation.validate_receipt(receipt) is receipt
    trusted = copy.deepcopy(receipt)
    trusted["trusted"] = True
    with pytest.raises(
        reconciliation.ReconciliationError, match="receipt_authority_claimed"
    ):
        reconciliation.validate_receipt(trusted)
    changed = copy.deepcopy(receipt)
    changed["entries"][0]["selected_result_ids"].append("sha256:" + "0" * 64)
    with pytest.raises(
        reconciliation.ReconciliationError, match="receipt_selected_not_observed"
    ):
        reconciliation.validate_receipt(changed)
    changed = copy.deepcopy(receipt)
    changed["receipt_ref"] = "sha256:" + "0" * 64
    with pytest.raises(
        reconciliation.ReconciliationError, match="receipt_ref_mismatch"
    ):
        reconciliation.validate_receipt(changed)


def test_real_plan_expected_projection_is_accepted_without_routing(tmp_path):
    target = tmp_path / "repo"
    shutil.copytree(FIXTURE, target)
    run(target, "init", "-q", "-b", "main")
    run(target, "config", "user.email", "reconcile@example.invalid")
    run(target, "config", "user.name", "Reconcile Test")
    run(target, "add", "-A")
    run(target, "commit", "-q", "-m", "base")
    base = run(target, "rev-parse", "HEAD").strip()
    config = selection.load_config(target / "ci-selection.toml")
    changes = selection.changeset(target, base=base, mode="worktree")
    chosen = selection.select(changes, config, root=target)
    plan = selection.plan(
        chosen,
        config=config,
        config_path=target / "ci-selection.toml",
        root=target,
    )
    expected = plan["expected_checks"]
    receipt = reconcile(expected)
    assert receipt["expected_set_ref"] == expected["expected_set_ref"]
    assert receipt["scope_id"] == expected["scope_id"]
    assert receipt["entries"]
    assert all(
        item["binding_status"] in {"unrouted", "not_applicable"}
        for item in receipt["entries"]
    )
    reconciliation.validate_receipt(receipt)


def test_real_v2_effective_profile_expected_is_accepted(tmp_path):
    target = tmp_path / "repo"
    shutil.copytree(FIXTURE, target)
    run(target, "init", "-q", "-b", "main")
    run(target, "config", "user.email", "reconcile@example.invalid")
    run(target, "config", "user.name", "Reconcile Test")
    run(target, "add", "-A")
    run(target, "commit", "-q", "-m", "base")
    base = run(target, "rev-parse", "HEAD").strip()
    (target / "docs/guide.md").write_text("# docs change\n", encoding="utf-8")
    config_path = target / "ci-selection.toml"
    base_config = selection.load_config(config_path)
    changes = selection.changeset(target, base=base, mode="worktree")
    chosen = selection.select(changes, base_config, root=target)
    profile = expected_checks.build_effective_profile(
        profile_ref={"name": "consumer.pr", "version": "1.0.0"},
        profile_revision="rev-2026-11-15",
        approval_source="governance-signed",
        authority_verified=True,
        effective_at="2026-11-15T00:00:00Z",
        evaluated_at="2026-11-15T01:00:00Z",
        context="worktree",
        action="worktree",
        target="HEAD",
        required_obligations=[
            {
                "obligation_ref": "ob/architecture",
                "kind": "check",
                "check_names": ["tach", "docs"],
                "allow_inapplicable": True,
            }
        ],
        advisory_checks=[],
        allowed_inapplicability=[],
        declared_checks=["whitespace", "forwarding", "docs", "vulture", "tach"],
        domain_complete=True,
        governed_rules=expected_checks.governed_domain_rules(
            target, config_path=config_path
        ),
    )
    config = _attach_effective_profile_policy(config_path, profile)
    plan = selection.plan(
        chosen,
        config=config,
        config_path=config_path,
        root=target,
        effective_profile=profile,
    )
    expected = plan["expected_checks"]
    assert expected["schema_version"] == 2
    assert expected["projection_source"] == "effective_profile"
    if "per_ref" in expected:
        ref = expected["per_ref"][0]
        receipt = reconcile(expected, ref_scope_id=ref["scope_id"])
        assert receipt["expected_set_ref"] == ref["expected_set_ref"]
    else:
        receipt = reconcile(expected)
        assert receipt["expected_set_ref"] == expected["expected_set_ref"]
        assert receipt["scope_id"] == expected["scope_id"]
    assert receipt["entries"]
    assert all(
        item["binding_status"] in {"unrouted", "not_applicable"}
        for item in receipt["entries"]
    )
    reconciliation.validate_receipt(receipt)


def test_b1_report_check_level_admissibility_isolates_invalid_checks():
    tach_entry = make_entry("tach")
    docs_entry = make_entry("docs")
    ws_entry = make_entry("whitespace")
    projection = make_projection([tach_entry, docs_entry, ws_entry])

    tach_ok = native(tool_access="tach/check", instance="tach-ok")
    docs_inv = native("docs", tool_access="git/ls-files", instance="docs-inv")
    docs_links = native(
        "docs",
        tool_access="lychee/docs",
        instance="docs-links",
        input_refs=[
            {
                "kind": "check_result",
                "result_id": docs_inv["result_id"],
                "path": "git/ls-files",
            },
            FILE_A,
        ],
    )
    ws_ok = native(
        "whitespace",
        tool_access="git/diff-check",
        baseline={"base_commit": "1" * 40},
        instance="ws-ok",
    )
    unauth_result = native(tool_access="tach/check", instance="unauth")
    other_ok = native(tool_access="tach/check", instance="other-ok")

    # 1. Mixed valid + unauthorized + contract-invalid + primary-contradicted in one Report:
    mixed_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "tach",
                "check_id": tach_entry["check_id"],
                "status": "passed",
                "plan_authorized": True,
                "check_contract": {
                    "kind": check_contract.CHECK_CONTRACT_KIND,
                    "schema_version": 2,
                    "status": "valid",
                },
                "native_result": tach_ok,
                "native_results": [tach_ok],
            },
            {
                "name": "docs",
                "check_id": docs_entry["check_id"],
                "status": "passed",
                "plan_authorized": False,
                "check_contract": {
                    "kind": check_contract.CHECK_CONTRACT_KIND,
                    "schema_version": 2,
                    "status": "valid",
                },
                "native_result": docs_links,
                "native_results": [docs_inv, docs_links],
            },
            {
                "name": "whitespace",
                "check_id": ws_entry["check_id"],
                "status": "passed",
                "plan_authorized": True,
                "check_contract": {
                    "kind": check_contract.CHECK_CONTRACT_KIND,
                    "schema_version": 2,
                    "status": "identity_missing",
                },
                "native_result": ws_ok,
                "native_results": [ws_ok],
            },
        ],
    }
    rref = report_ref_for(mixed_report)
    obs = {
        "job_ref": "job-1",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": RUNNER_REF,
        "report_ref": rref,
        "report": mixed_report,
        "job_receipt": job_receipt_for("job-1", RUN_REF, ATTEMPT_REF, RUNNER_REF, rref),
        "native_results": [tach_ok, docs_inv, docs_links, ws_ok],
    }
    routing = [
        make_routing(tach_entry),
        make_routing(docs_entry),
        make_routing(ws_entry),
    ]
    resolutions = [
        make_resolution(tach_entry, rule=RULE_TACH),
        make_resolution(docs_entry, rule=RULE_DOCS),
        make_resolution(ws_entry, rule=RULE_WS, baseline={"base_commit": "1" * 40}),
    ]
    receipt = reconcile(
        projection, routing=routing, resolutions=resolutions, observations=[obs]
    )
    reconciliation.validate_receipt(receipt)
    by_name = {item["name"]: item for item in receipt["entries"]}
    assert by_name["tach"]["binding_status"] == "matched"
    assert by_name["docs"]["binding_status"] == "insufficient"
    assert by_name["docs"]["reason"] == "report_check_unauthorized"
    assert docs_links["result_id"] in by_name["docs"]["observed_result_ids"]
    assert by_name["docs"]["native_status"] == "passed"
    assert (
        by_name["docs"]["rejected_results"][0]["reason"] == "report_check_unauthorized"
    )
    assert by_name["whitespace"]["binding_status"] == "insufficient"
    assert by_name["whitespace"]["reason"] == "report_check_contract_invalid"
    assert ws_ok["result_id"] in by_name["whitespace"]["observed_result_ids"]

    # 2. Primary native_result contradicting native_results:
    contra_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "tach",
                "check_id": tach_entry["check_id"],
                "status": "passed",
                "plan_authorized": True,
                "check_contract": {
                    "kind": check_contract.CHECK_CONTRACT_KIND,
                    "schema_version": 2,
                    "status": "valid",
                },
                "native_result": other_ok,
                "native_results": [unauth_result],
            }
        ],
    }
    cref = report_ref_for(contra_report)
    c_obs = {
        "job_ref": "job-1",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": RUNNER_REF,
        "report_ref": cref,
        "report": contra_report,
        "job_receipt": job_receipt_for("job-1", RUN_REF, ATTEMPT_REF, RUNNER_REF, cref),
        "native_results": [unauth_result],
    }
    p_single = make_projection([tach_entry])
    contra_receipt = reconcile(
        p_single,
        routing=[make_routing(tach_entry)],
        resolutions=[make_resolution(tach_entry, rule=RULE_TACH)],
        observations=[c_obs],
    )
    reconciliation.validate_receipt(contra_receipt)
    assert contra_receipt["entries"][0]["binding_status"] == "insufficient"
    assert contra_receipt["entries"][0]["reason"] == "report_primary_result_mismatch"


def test_b2_validate_receipt_rejects_rehashed_logical_contradictions():
    entry = make_entry("tach")
    projection = make_projection([entry])
    partial = reconcile(projection)
    assert partial["status"] == "unrouted"

    def rehash(doc):
        payload = {k: v for k, v in doc.items() if k != "receipt_ref"}
        doc["receipt_ref"] = reconciliation._digest(payload)
        return doc

    forged_status = rehash({**copy.deepcopy(partial), "status": "matched"})
    with pytest.raises(
        reconciliation.ReconciliationError, match="receipt_status_mismatch"
    ):
        reconciliation.validate_receipt(forged_status)

    bad_summary = copy.deepcopy(partial)
    bad_summary["summary"]["matched"] = 1
    bad_summary = rehash(bad_summary)
    with pytest.raises(
        reconciliation.ReconciliationError, match="receipt_summary_mismatch"
    ):
        reconciliation.validate_receipt(bad_summary)

    _e, matched_proj, routing, resolution, obs, _res = matched_setup()
    matched = reconcile(
        matched_proj,
        routing=[routing],
        resolutions=[resolution],
        observations=[obs],
    )
    # Mark entry unexecuted/missing? Wait: simulate unrouted with forged all_observations_reconciled=False
    bad_flag = rehash({**copy.deepcopy(partial), "all_observations_reconciled": False})
    with pytest.raises(
        reconciliation.ReconciliationError, match="receipt_reconciliation_flag_mismatch"
    ):
        reconciliation.validate_receipt(bad_flag)

    bad_entry = copy.deepcopy(matched)
    bad_entry["entries"][0]["selected_result_ids"] = []
    bad_entry["entries"][0]["selected_results"] = []
    bad_entry = rehash(bad_entry)
    with pytest.raises(
        reconciliation.ReconciliationError, match="receipt_entry_status_inconsistent"
    ):
        reconciliation.validate_receipt(bad_entry)


def test_b3_invocation_and_event_attribution_refuse_old_run_replay():
    _entry, projection, routing, resolution, obs, _res = matched_setup()
    # Same Source, Rule, Profile, and valid Execution Report from an older Invocation ("inv/old"):
    old_obs = copy.deepcopy(obs)
    old_obs["job_receipt"]["invocation_ref"] = "inv/old"
    old_obs["job_receipt"]["event_ref"] = dict(EVENT)
    old_obs["job_receipt"]["scope_id"] = projection["scope_id"]
    old_obs["job_receipt"]["expected_set_ref"] = projection["expected_set_ref"]

    # Replaying inv/old receipt against current invocation_ref="inv/current" refuses matched:
    replayed = reconcile(
        projection,
        routing=[routing],
        resolutions=[resolution],
        observations=[old_obs],
        invocation_ref="inv/current",
    )
    reconciliation.validate_receipt(replayed)
    e = replayed["entries"][0]
    assert e["binding_status"] == "unknown"
    assert e["reason"] == "job_invocation_mismatch"
    assert e["rejected_observations"][0]["reason"] == "job_invocation_mismatch"
    assert e["selected_result_ids"] == []
    assert "cross_run_reuse_requires_upstream_admission" in replayed["limitations"]

    # Missing invocation_ref in receipt when Expected invocation_ref is bound stays unproven:
    unproven = reconcile(
        projection,
        routing=[routing],
        resolutions=[resolution],
        observations=[obs],
        invocation_ref="inv/current",
    )
    assert unproven["entries"][0]["binding_status"] == "unknown"
    assert unproven["entries"][0]["reason"] == "job_invocation_unproven"

    # Mismatched Event on job_receipt is rejected even without explicit invocation_ref:
    other_event_obs = copy.deepcopy(obs)
    other_event_obs["job_receipt"]["event_ref"] = {**EVENT, "head_commit": "f" * 40}
    other_event_rec = reconcile(
        projection,
        routing=[routing],
        resolutions=[resolution],
        observations=[other_event_obs],
    )
    assert other_event_rec["entries"][0]["binding_status"] == "unknown"
    assert other_event_rec["entries"][0]["reason"] == "job_event_mismatch"

    # Matching invocation_ref + event_ref + scope_id + expected_set_ref yields matched:
    curr_routing = copy.deepcopy(routing)
    curr_routing["job_instances"][0]["invocation_ref"] = "inv/current"
    curr_obs = copy.deepcopy(obs)
    curr_obs["job_receipt"]["invocation_ref"] = "inv/current"
    curr_obs["job_receipt"]["event_ref"] = dict(EVENT)
    curr_obs["job_receipt"]["scope_id"] = projection["scope_id"]
    curr_obs["job_receipt"]["expected_set_ref"] = projection["expected_set_ref"]
    matched_curr = reconcile(
        projection,
        routing=[curr_routing],
        resolutions=[resolution],
        observations=[curr_obs],
        invocation_ref="inv/current",
    )
    reconciliation.validate_receipt(matched_curr)
    assert matched_curr["entries"][0]["binding_status"] == "matched"
    assert matched_curr["expected_invocation_ref"] == "inv/current"


def test_b3_l2_handoff_preserves_compact_refs_partial_coverage_and_untrusted_boundary():
    tach_entry = make_entry("tach", rule=RULE_TACH)
    ws_entry = make_entry(
        "whitespace", rule=RULE_WS, baseline_ref={"base_commit": "1" * 40}
    )
    partial_proj = make_projection(
        [tach_entry, ws_entry], consumer_completeness="not_claimed"
    )
    tach_res = native("tach", tool_access="tach/check", instance="tach-ok")
    obs = make_observation("job-1", [tach_res])
    receipt = reconcile(
        partial_proj,
        routing=[
            make_routing(tach_entry),
            make_routing(ws_entry, jobs=(), status="unrouted"),
        ],
        resolutions=[make_resolution(tach_entry, rule=RULE_TACH)],
        observations=[obs],
    )
    reconciliation.validate_receipt(receipt)

    # 1. Structural Receipt always keeps trusted=False / evidence_admission=not_evaluated
    # (signaling #66-C has not authenticated external issuers, NOT a permanent veto):
    assert receipt["trusted"] is False
    assert receipt["required"] is False
    assert receipt["evidence_admission"] == "not_evaluated"
    assert receipt["required_gate"] == "not_evaluated"
    assert receipt["status"] == "partial"

    # 2. Partial scope keeps matched per-check entries intact alongside unrouted/missing ones:
    by_name = {item["name"]: item for item in receipt["entries"]}
    assert by_name["tach"]["binding_status"] == "matched"
    assert by_name["whitespace"]["binding_status"] == "unrouted"

    # 3. Selected witness evidence in Receipt is a compact index referencing the raw Report,
    # NOT a full NativeResult copy (#63 must fetch & verify raw NativeResult via report_ref):
    compact = by_name["tach"]["selected_results"][0]
    for required_pointer in (
        "result_id",
        "job_ref",
        "run_ref",
        "attempt_ref",
        "runner_ref",
        "report_ref",
        "job_receipt_ref",
        "result_origin",
    ):
        assert compact.get(required_pointer) is not None
    for full_only_field in ("source_ref", "rule_ref", "input_refs", "evidence"):
        assert full_only_field not in compact

    # 4. Different Event ref is refused only for current-run #66-C attribution, while
    # explicitly pointing callers to upstream #63 cross-run equivalence admission:
    diff_event_obs = copy.deepcopy(obs)
    diff_event_obs["job_receipt"]["event_ref"] = {**EVENT, "head_commit": "e" * 40}
    diff_rec = reconcile(
        make_projection([tach_entry]),
        routing=[make_routing(tach_entry)],
        resolutions=[make_resolution(tach_entry, rule=RULE_TACH)],
        observations=[diff_event_obs],
    )
    assert diff_rec["entries"][0]["binding_status"] == "unknown"
    assert diff_rec["entries"][0]["reason"] == "job_event_mismatch"
    assert "cross_run_reuse_requires_upstream_admission" in diff_rec["limitations"]


def test_b3_multi_ref_same_check_name_distinct_instances_and_canonical_receipt_ref():
    # Two distinct Refs (Ref A vs Ref B) both declare Check name "tach":
    event_a = {
        **EVENT,
        "head_ref": "refs/heads/ref-a",
        "head_commit": "a" * 40,
    }
    event_b = {
        **EVENT,
        "head_ref": "refs/heads/ref-b",
        "head_commit": "b" * 40,
    }
    entry_a = make_entry("tach", rule=RULE_TACH, event=event_a, profile_ref=PROFILE_REF)
    entry_b = make_entry("tach", rule=RULE_TACH, event=event_b, profile_ref=PROFILE_REF)
    assert entry_a["name"] == entry_b["name"] == "tach"
    assert entry_a["expected_check_ref"] != entry_b["expected_check_ref"]

    scope_id_a = expected_checks.scope_identity(
        None,
        PROFILE_REF,
        PROFILE_SOURCE,
        event_a,
        WEAK_SOURCE,
        SELECTION_RULE,
        HYGIENE_RULE,
        SOURCES_RULE,
        profile_sha256=PROFILE["profile_sha256"],
        evaluated_at=PROFILE["evaluated_at"],
    )
    scope_id_b = expected_checks.scope_identity(
        None,
        PROFILE_REF,
        PROFILE_SOURCE,
        event_b,
        WEAK_SOURCE,
        SELECTION_RULE,
        HYGIENE_RULE,
        SOURCES_RULE,
        profile_sha256=PROFILE["profile_sha256"],
        evaluated_at=PROFILE["evaluated_at"],
    )
    assert scope_id_a != scope_id_b

    multi_proj = {
        "kind": expected_checks.EXPECTED_KIND,
        "schema_version": 2,
        "projection_source": "effective_profile",
        "effective_profile": PROFILE,
        "authority_status": "verified",
        "trusted_admission_status": "not_evaluated",
        "hygiene_rule_ref": HYGIENE_RULE,
        "sources_rule_ref": SOURCES_RULE,
        "status": "partial",
        "reason": "test",
        "scope_id": expected_checks.scope_identity(
            None,
            PROFILE_REF,
            PROFILE_SOURCE,
            EVENT,
            WEAK_SOURCE,
            SELECTION_RULE,
            HYGIENE_RULE,
            SOURCES_RULE,
            profile_sha256=PROFILE["profile_sha256"],
            evaluated_at=PROFILE["evaluated_at"],
        ),
        "selection_rule_ref": copy.deepcopy(SELECTION_RULE),
        "expected_set_ref": expected_checks.set_identity((), (), schema_version=2),
        "definitions_status": "requested_names_complete",
        "applicability_status": "requested_scope",
        "consumer": None,
        "profile_ref": copy.deepcopy(PROFILE_REF),
        "profile_source": PROFILE_SOURCE,
        "consumer_completeness": "per_ref_container_only",
        "observed_input_closure_status": "not_observed",
        "event_ref": copy.deepcopy(EVENT),
        "source_ref": copy.deepcopy(WEAK_SOURCE),
        "checks": [],
        "non_check_obligations": [],
        "missing": [],
        "conflicts": [],
        "per_ref": [
            {
                "source_ref": "refs/heads/ref-a",
                "local_ref": "refs/heads/ref-a",
                "remote_ref": "origin",
                "ref_action": "update",
                "force_push": False,
                "baseline_status": "ok",
                "event_ref": copy.deepcopy(event_a),
                "source": copy.deepcopy(WEAK_SOURCE),
                "scope_id": scope_id_a,
                "expected_set_ref": expected_checks.set_identity(
                    [entry_a], (), schema_version=2
                ),
                "checks": [entry_a],
                "non_check_obligations": [],
                "status": "partial",
            },
            {
                "source_ref": "refs/heads/ref-b",
                "local_ref": "refs/heads/ref-b",
                "remote_ref": "origin",
                "ref_action": "update",
                "force_push": False,
                "baseline_status": "ok",
                "event_ref": copy.deepcopy(event_b),
                "source": copy.deepcopy(WEAK_SOURCE),
                "scope_id": scope_id_b,
                "expected_set_ref": expected_checks.set_identity(
                    [entry_b], (), schema_version=2
                ),
                "checks": [entry_b],
                "non_check_obligations": [],
                "status": "partial",
            },
        ],
    }

    tach_res_a = native(
        "tach", tool_access="tach/check", instance="tach-ref-a", profile_ref=PROFILE_REF
    )
    obs_a = make_observation("job-a", [tach_res_a])
    obs_a["job_receipt"]["invocation_ref"] = "inv/multi"
    obs_a["job_receipt"]["event_ref"] = copy.deepcopy(event_a)
    obs_a["job_receipt"]["scope_id"] = scope_id_a
    obs_a["job_receipt"]["expected_set_ref"] = multi_proj["per_ref"][0][
        "expected_set_ref"
    ]
    routing_a = make_routing(entry_a, jobs=("job-a",))
    routing_a["job_instances"][0]["invocation_ref"] = "inv/multi"

    rec_a = reconcile(
        multi_proj,
        ref_scope_id=scope_id_a,
        routing=[routing_a],
        resolutions=[make_resolution(entry_a, rule=RULE_TACH)],
        observations=[obs_a],
        invocation_ref="inv/multi",
    )
    reconciliation.validate_receipt(rec_a)
    assert rec_a["scope_id"] == scope_id_a
    assert rec_a["entries"][0]["expected_check_ref"] == entry_a["expected_check_ref"]
    assert rec_a["entries"][0]["binding_status"] == "matched"

    # Reconciling Ref B without its own witness, or attempting to reuse Ref A's
    # witness/observation under Ref B, must NEVER satisfy Ref B:
    routing_b = make_routing(entry_b, jobs=("job-b",))
    routing_b["job_instances"][0]["invocation_ref"] = "inv/multi"
    obs_a_as_b = copy.deepcopy(obs_a)
    obs_a_as_b["job_ref"] = "job-b"
    obs_a_as_b["job_receipt"]["job_ref"] = "job-b"
    rec_b = reconcile(
        multi_proj,
        ref_scope_id=scope_id_b,
        routing=[routing_b],
        resolutions=[make_resolution(entry_b, rule=RULE_TACH)],
        observations=[obs_a_as_b],
        invocation_ref="inv/multi",
    )
    reconciliation.validate_receipt(rec_b)
    assert rec_b["scope_id"] == scope_id_b
    assert rec_b["entries"][0]["expected_check_ref"] == entry_b["expected_check_ref"]
    assert rec_b["entries"][0]["binding_status"] == "unknown"
    assert rec_b["entries"][0]["reason"] in {"job_event_mismatch", "job_scope_mismatch"}

    # Canonical receipt_ref deterministically commits full payload and rejects forgery:
    payload_a = {k: v for k, v in rec_a.items() if k != "receipt_ref"}
    assert rec_a["receipt_ref"] == reconciliation._digest(payload_a)
    tampered_ref = {**copy.deepcopy(rec_a), "receipt_ref": "sha256:" + ("0" * 64)}
    with pytest.raises(
        reconciliation.ReconciliationError, match="receipt_ref_mismatch"
    ):
        reconciliation.validate_receipt(tampered_ref)


def test_b1_eci_producer_report_contract_and_dependency_propagation():
    ws_entry = make_entry("whitespace", rule=RULE_WS)
    docs_entry = make_entry("docs", rule=RULE_DOCS)
    tach_entry = make_entry("tach", rule=RULE_TACH)
    projection = make_projection([ws_entry, docs_entry, tach_entry])

    ws_res = native(
        "whitespace",
        rule=RULE_WS,
        tool_access="git/diff-check",
        baseline={"base_commit": "1" * 40},
        instance="ws-ok",
    )
    docs_res = native(
        "docs",
        rule=RULE_DOCS,
        tool_access="lychee/docs",
        instance="docs-links",
        input_refs=[FILE_A],
    )
    tach_res = native(tool_access="tach/check", instance="tach-ok")

    # ECI Producer Report: valid whitespace, missing check_contract on docs,
    # missing plan_authorized on tach, PLUS blocked_by:
    mixed_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "whitespace",
                "check_id": ws_entry["check_id"],
                "status": "passed",
                "plan_authorized": True,
                "check_contract": {
                    "kind": check_contract.CHECK_CONTRACT_KIND,
                    "schema_version": 2,
                    "status": "valid",
                },
                "native_result": ws_res,
                "native_results": [ws_res],
            },
            {
                "name": "docs",
                "check_id": docs_entry["check_id"],
                "status": "passed",
                "plan_authorized": True,
                "native_result": docs_res,
                "native_results": [docs_res],
            },
            {
                "name": "tach",
                "check_id": tach_entry["check_id"],
                "status": "passed",
                "check_contract": {
                    "kind": check_contract.CHECK_CONTRACT_KIND,
                    "schema_version": 2,
                    "status": "valid",
                },
                "blocked_by": [
                    {
                        "producer_ref": {"kind": "check", "name": "docs"},
                        "reason": "pre_execution_prerequisite_failed",
                    }
                ],
                "native_result": tach_res,
                "native_results": [tach_res],
            },
        ],
    }
    rref = report_ref_for(mixed_report)
    obs = {
        "job_ref": "job-1",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": RUNNER_REF,
        "report_ref": rref,
        "report": mixed_report,
        "job_receipt": job_receipt_for("job-1", RUN_REF, ATTEMPT_REF, RUNNER_REF, rref),
        "native_results": [ws_res, docs_res, tach_res],
    }
    receipt = reconcile(
        projection,
        routing=[
            make_routing(ws_entry),
            make_routing(docs_entry),
            make_routing(tach_entry),
        ],
        resolutions=[
            make_resolution(ws_entry, rule=RULE_WS, baseline={"base_commit": "1" * 40}),
            make_resolution(docs_entry, rule=RULE_DOCS),
            make_resolution(tach_entry, rule=RULE_TACH),
        ],
        observations=[obs],
    )
    reconciliation.validate_receipt(receipt)
    by_name = {item["name"]: item for item in receipt["entries"]}
    assert by_name["whitespace"]["binding_status"] == "matched"
    assert by_name["docs"]["binding_status"] == "insufficient"
    assert by_name["docs"]["reason"] == "report_check_contract_invalid"
    assert by_name["tach"]["binding_status"] == "insufficient"
    assert by_name["tach"]["reason"] in {
        "report_check_unauthorized",
        "report_prerequisite_failed",
    }

    # Report-wide plan_declaration_consistent=False rejects every Check in the report:
    inconsistent_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "plan_declaration_consistent": False,
        "checks": [mixed_report["checks"][0]],
    }
    iref = report_ref_for(inconsistent_report)
    iobs = {
        "job_ref": "job-1",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": RUNNER_REF,
        "report_ref": iref,
        "report": inconsistent_report,
        "job_receipt": job_receipt_for("job-1", RUN_REF, ATTEMPT_REF, RUNNER_REF, iref),
        "native_results": [ws_res],
    }
    p_single = make_projection([ws_entry])
    inc_rec = reconcile(
        p_single,
        routing=[make_routing(ws_entry)],
        resolutions=[
            make_resolution(ws_entry, rule=RULE_WS, baseline={"base_commit": "1" * 40})
        ],
        observations=[iobs],
    )
    reconciliation.validate_receipt(inc_rec)
    assert inc_rec["entries"][0]["binding_status"] == "insufficient"
    assert inc_rec["entries"][0]["reason"] == "report_plan_declaration_inconsistent"


def test_b2_validate_receipt_entry_semantic_invariants():
    def rehash(doc):
        payload = {k: v for k, v in doc.items() if k != "receipt_ref"}
        doc["receipt_ref"] = reconciliation._digest(payload)
        return doc

    _e, matched_proj, routing, resolution, obs, _res = matched_setup()
    matched = reconcile(
        matched_proj,
        routing=[routing],
        resolutions=[resolution],
        observations=[obs],
    )

    bad_satisfaction = copy.deepcopy(matched)
    bad_satisfaction["entries"][0]["native_satisfaction"]["status"] = "unsatisfied"
    with pytest.raises(
        reconciliation.ReconciliationError, match="receipt_satisfaction_invalid"
    ):
        reconciliation.validate_receipt(rehash(bad_satisfaction))

    forged_ref = copy.deepcopy(matched)
    forged_ref["entries"][0]["resolved_expected_ref"] = "sha256:" + ("0" * 64)
    with pytest.raises(
        reconciliation.ReconciliationError, match="receipt_resolved_expected_mismatch"
    ):
        reconciliation.validate_receipt(rehash(forged_ref))

    overlap = copy.deepcopy(matched)
    overlap["entries"][0]["rejected_results"].append(
        {"result_id": overlap["entries"][0]["selected_result_ids"][0], "reason": "bad"}
    )
    with pytest.raises(
        reconciliation.ReconciliationError, match="receipt_selected_rejected_conflict"
    ):
        reconciliation.validate_receipt(rehash(overlap))

    # Selected evidence diverging from the same-ID observed evidence is refused:
    bad_sel_status = copy.deepcopy(matched)
    bad_sel_status["entries"][0]["selected_results"][0]["status"] = "advisory"
    with pytest.raises(
        reconciliation.ReconciliationError, match="receipt_result_evidence_mismatch"
    ):
        reconciliation.validate_receipt(rehash(bad_sel_status))

    bad_sel_attempt = copy.deepcopy(matched)
    bad_sel_attempt["entries"][0]["selected_results"][0]["attempt_ref"] = (
        "attempt/other"
    )
    with pytest.raises(
        reconciliation.ReconciliationError, match="receipt_result_evidence_mismatch"
    ):
        reconciliation.validate_receipt(rehash(bad_sel_attempt))

    # Consistently rewritten non-passed witness is refused on status:
    both_status = copy.deepcopy(matched)
    for key in ("selected_results", "observed_results"):
        both_status["entries"][0][key][0]["status"] = "advisory"
    with pytest.raises(
        reconciliation.ReconciliationError, match="receipt_selected_status_invalid"
    ):
        reconciliation.validate_receipt(rehash(both_status))

    # With no activated job instances a matched witness has no eligible attempt:
    no_instances = copy.deepcopy(matched)
    no_instances["entries"][0]["job_instances"] = []
    with pytest.raises(
        reconciliation.ReconciliationError, match="receipt_selected_unqualified_attempt"
    ):
        reconciliation.validate_receipt(rehash(no_instances))


def test_b3_job_receipt_requires_all_four_attribution_fields_and_frozen_l2_interface():
    assert reconciliation.CURRENT_EXECUTION_ATTRIBUTION_FIELDS == (
        "invocation_ref",
        "event_ref",
        "scope_id",
        "expected_set_ref",
    )
    assert reconciliation.EVIDENCE_ADMISSION_HANDOFF_FIELDS == (
        "invocation_ref",
        "event_ref",
        "scope_id",
        "expected_set_ref",
        "receipt_ref",
    )
    assert len(reconciliation.EXECUTION_PHASE_ORDER) == 3

    _entry, projection, routing, resolution, obs, _res = matched_setup()
    curr_routing = copy.deepcopy(routing)
    curr_routing["job_instances"][0]["invocation_ref"] = "inv/curr"
    base_obs = copy.deepcopy(obs)
    base_obs["job_receipt"]["invocation_ref"] = "inv/curr"
    base_obs["job_receipt"]["event_ref"] = dict(EVENT)
    base_obs["job_receipt"]["scope_id"] = projection["scope_id"]
    base_obs["job_receipt"]["expected_set_ref"] = projection["expected_set_ref"]

    for drop_field, expected_reason in (
        ("event_ref", "job_event_unproven"),
        ("scope_id", "job_scope_unproven"),
        ("expected_set_ref", "job_expected_set_unproven"),
    ):
        bad_obs = copy.deepcopy(base_obs)
        bad_obs["job_receipt"].pop(drop_field)
        rec = reconcile(
            projection,
            routing=[curr_routing],
            resolutions=[resolution],
            observations=[bad_obs],
            invocation_ref="inv/curr",
        )
        reconciliation.validate_receipt(rec)
        assert rec["entries"][0]["binding_status"] == "unknown"
        assert rec["entries"][0]["reason"] == expected_reason


def test_issue109_requirement_details_internal_consistency_negatives():
    def rehash(doc):
        payload = {k: v for k, v in doc.items() if k != "receipt_ref"}
        doc["receipt_ref"] = reconciliation._digest(payload)
        return doc

    def expect_invalid(doc, pattern):
        with pytest.raises(reconciliation.ReconciliationError, match=pattern):
            reconciliation.validate_receipt(rehash(doc))

    # 1) Single-requirement Check (tach) with extra candidate + rejected result:
    entry = make_entry("tach")
    projection = make_projection([entry])
    res_ok = native("tach", tool_access="tach/check", instance="tach-1")
    res_alt = native("tach", tool_access="tach/check", instance="tach-2")
    wrong_source = check_contract.source_ref(
        "worktree",
        declared_target_tree="f" * 40,
        actual_execution_source="worktree",
        content_digest="e" * 64,
        verified_scope=["docs/guide.md"],
        scope_kind="check_input_closure",
    )
    res_wrong_source = native(
        "tach",
        tool_access="tach/check",
        instance="tach-bad-src",
        source=wrong_source,
    )
    receipt = reconcile(
        projection,
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry, rule=RULE_TACH)],
        observations=[make_observation("job-1", [res_ok, res_alt, res_wrong_source])],
    )
    reconciliation.validate_receipt(receipt)
    selected_id = receipt["entries"][0]["selected_result_ids"][0]
    unselected_id = (
        res_alt["result_id"]
        if selected_id == res_ok["result_id"]
        else res_ok["result_id"]
    )
    rejected_id = res_wrong_source["result_id"]

    # Counterexample 1 (Core): requirement status flipped to unsatisfied while top-level stays matched/satisfied:
    bad_req_status = copy.deepcopy(receipt)
    bad_req_status["entries"][0]["native_satisfaction"]["requirements"][0]["status"] = (
        "unsatisfied"
    )
    expect_invalid(bad_req_status, "receipt_requirement_unsatisfied")

    # Counterexample 2a: satisfied_by missing / empty:
    bad_missing_by = copy.deepcopy(receipt)
    bad_missing_by["entries"][0]["native_satisfaction"]["requirements"][0][
        "satisfied_by"
    ] = None
    expect_invalid(bad_missing_by, "receipt_requirement_witness_missing")

    # Counterexample 2b: satisfied_by points to valid observed but unselected result:
    bad_unselected = copy.deepcopy(receipt)
    bad_unselected["entries"][0]["native_satisfaction"]["requirements"][0][
        "satisfied_by"
    ] = unselected_id
    expect_invalid(bad_unselected, "receipt_requirement_witness_unselected")

    # Counterexample 2c: satisfied_by points to rejected result:
    bad_rejected = copy.deepcopy(receipt)
    bad_rejected["entries"][0]["native_satisfaction"]["requirements"][0][
        "satisfied_by"
    ] = rejected_id
    expect_invalid(bad_rejected, "receipt_selected_rejected_conflict")

    # Counterexample 2d: failure reason retained on satisfied requirement:
    bad_reason = copy.deepcopy(receipt)
    bad_reason["entries"][0]["native_satisfaction"]["requirements"][0]["reason"] = (
        "prerequisite_unmet"
    )
    expect_invalid(bad_reason, "receipt_requirement_reason_invalid")

    # Counterexample 3a: duplicate requirement_id or missing/extra requirement:
    dup_req = copy.deepcopy(receipt)
    dup_req["entries"][0]["native_satisfaction"]["requirements"].append(
        copy.deepcopy(dup_req["entries"][0]["native_satisfaction"]["requirements"][0])
    )
    expect_invalid(dup_req, "receipt_requirement_duplicate")

    missing_req = copy.deepcopy(receipt)
    missing_req["entries"][0]["native_satisfaction"]["requirements"] = []
    expect_invalid(missing_req, "receipt_satisfaction_requirements_mismatch")

    extra_req = copy.deepcopy(receipt)
    extra_req["entries"][0]["native_satisfaction"]["requirements"].append(
        {
            "requirement_id": "extra-req",
            "after": [],
            "alternatives": ["tach/check"],
            "observed": [selected_id],
            "satisfied_by": selected_id,
            "status": "satisfied",
            "reason": "native_pass",
        }
    )
    expect_invalid(extra_req, "receipt_satisfaction_requirements_mismatch")

    # 2) Prerequisite chain (docs: inventory -> links):
    docs_entry = make_entry("docs")
    docs_proj = make_projection([docs_entry])
    inv = native("docs", tool_access="git/ls-files", rule=RULE_DOCS, instance="inv-1")
    links = native(
        "docs",
        tool_access="lychee/docs",
        rule=RULE_DOCS,
        instance="links-1",
        input_refs=[
            {
                "kind": "check_result",
                "result_id": inv["result_id"],
                "path": "git/ls-files",
            },
            FILE_A,
        ],
    )
    docs_rec = reconcile(
        docs_proj,
        routing=[make_routing(docs_entry)],
        resolutions=[make_resolution(docs_entry, rule=RULE_DOCS)],
        observations=[make_observation("job-1", [inv, links])],
    )
    reconciliation.validate_receipt(docs_rec)
    assert docs_rec["entries"][0]["binding_status"] == "matched"

    # Predecessor requirement marked unsatisfied while links stays satisfied:
    bad_pred_status = copy.deepcopy(docs_rec)
    bad_pred_status["entries"][0]["native_satisfaction"]["requirements"][0][
        "status"
    ] = "unsatisfied"
    expect_invalid(bad_pred_status, "receipt_requirement_unsatisfied")

    # Swap satisfied_by between inventory and links:
    # (a) without updating observed[] -> caught as witness unobserved;
    # (b) with observed[] also forged -> caught as tool mismatch against controlled definition:
    swapped_unobs = copy.deepcopy(docs_rec)
    reqs_u = swapped_unobs["entries"][0]["native_satisfaction"]["requirements"]
    reqs_u[0]["satisfied_by"], reqs_u[1]["satisfied_by"] = (
        reqs_u[1]["satisfied_by"],
        reqs_u[0]["satisfied_by"],
    )
    expect_invalid(swapped_unobs, "receipt_requirement_witness_unobserved")

    swapped_docs = copy.deepcopy(docs_rec)
    reqs = swapped_docs["entries"][0]["native_satisfaction"]["requirements"]
    reqs[0]["satisfied_by"], reqs[1]["satisfied_by"] = (
        reqs[1]["satisfied_by"],
        reqs[0]["satisfied_by"],
    )
    reqs[0]["observed"] = [reqs[0]["satisfied_by"]]
    reqs[1]["observed"] = [reqs[1]["satisfied_by"]]
    swapped_docs["entries"][0]["selected_result_ids"] = [
        reqs[0]["satisfied_by"],
        reqs[1]["satisfied_by"],
    ]
    swapped_docs["entries"][0]["native_satisfaction"]["selected_result_ids"] = [
        reqs[0]["satisfied_by"],
        reqs[1]["satisfied_by"],
    ]
    swapped_docs["entries"][0]["selected_results"] = list(
        reversed(swapped_docs["entries"][0]["selected_results"])
    )
    expect_invalid(swapped_docs, "receipt_requirement_tool_mismatch")

    # Tamper prerequisite metadata (`after` cleared on links):
    bad_after = copy.deepcopy(docs_rec)
    bad_after["entries"][0]["native_satisfaction"]["requirements"][1]["after"] = []
    expect_invalid(bad_after, "receipt_requirement_structure_mismatch")

    # Misaligned selected_result_ids order relative to requirement order refuses:
    bad_order = copy.deepcopy(docs_rec)
    bad_order["entries"][0]["selected_result_ids"] = list(
        reversed(bad_order["entries"][0]["selected_result_ids"])
    )
    bad_order["entries"][0]["selected_results"] = list(
        reversed(bad_order["entries"][0]["selected_results"])
    )
    expect_invalid(bad_order, "receipt_witness_mismatch")

    # 3) Conditional requirement driven by controlled Rule (weaver_registry):
    from test_native_tool_contract import (
        _weaver_baseline_chain,
        _weaver_packaging_chain,
    )

    reg_def = check_contract.definition_for("weaver_registry")
    baseline_rule = check_contract.rule_ref(
        "ci-weaver", 1, path="ci-weaver.toml", digest="a" * 64, baseline_required=True
    )
    non_baseline_rule = check_contract.rule_ref(
        "ci-weaver", 1, path="ci-weaver.toml", digest="b" * 64, baseline_required=False
    )
    reg_entry = make_entry("weaver_registry", rule_ref=baseline_rule)
    reg_pack = _weaver_packaging_chain(
        reg_def, source=STRONG_SOURCE, rule=baseline_rule, prefix="pack"
    )
    reg_base = _weaver_baseline_chain(
        reg_def,
        source=STRONG_SOURCE,
        rule=baseline_rule,
        registry_local=reg_pack[-1],
        include_diff=True,
    )
    reg_rec = reconcile(
        make_projection([reg_entry]),
        routing=[make_routing(reg_entry)],
        resolutions=[make_resolution(reg_entry, rule=baseline_rule)],
        observations=[make_observation("job-1", [*reg_pack, *reg_base])],
    )
    reconciliation.validate_receipt(reg_rec)
    assert reg_rec["entries"][0]["binding_status"] == "matched"

    # Dropping an active conditional baseline requirement under baseline_required=True refuses:
    dropped_cond = copy.deepcopy(reg_rec)
    dropped_cond["entries"][0]["native_satisfaction"]["requirements"] = [
        r
        for r in dropped_cond["entries"][0]["native_satisfaction"]["requirements"]
        if r["requirement_id"] != "registry-diff"
    ]
    expect_invalid(dropped_cond, "receipt_satisfaction_requirements_mismatch")

    # Conversely, under baseline_required=False only 3 packaging requirements are active;
    # injecting an inactive conditional requirement refuses:
    reg_no_base_entry = make_entry("weaver_registry", rule_ref=non_baseline_rule)
    reg_pack_only = _weaver_packaging_chain(
        reg_def, source=STRONG_SOURCE, rule=non_baseline_rule, prefix="nobase"
    )
    reg_no_base_rec = reconcile(
        make_projection([reg_no_base_entry]),
        routing=[make_routing(reg_no_base_entry)],
        resolutions=[make_resolution(reg_no_base_entry, rule=non_baseline_rule)],
        observations=[make_observation("job-1", reg_pack_only)],
    )
    reconciliation.validate_receipt(reg_no_base_rec)
    injected_inactive = copy.deepcopy(reg_no_base_rec)
    injected_inactive["entries"][0]["native_satisfaction"]["requirements"].append(
        {
            "requirement_id": "baseline-check",
            "after": [],
            "alternatives": ["weaver/baseline-check"],
            "observed": [reg_pack_only[0]["result_id"]],
            "satisfied_by": reg_pack_only[0]["result_id"],
            "status": "satisfied",
            "reason": "native_pass",
        }
    )
    expect_invalid(injected_inactive, "receipt_satisfaction_requirements_mismatch")

    # 4) Tool-less internal Check (forwarding): non-empty requirements[] is refused:
    fwd_def = check_contract.definition_for("forwarding")
    fwd_entry = make_entry("forwarding", baseline_ref={"base_commit": "1" * 40})
    fwd_res = check_contract.native_result(
        fwd_def,
        source=STRONG_SOURCE,
        status="passed",
        result_origin="host",
        result_instance="fwd-ok",
        native_exit_code=None,
        tool=None,
        evidence={"analysis": "forwarding", "findings": []},
        rule_ref=RULE_TACH,
        profile_ref=fwd_def.profile_ref,
        runner_ref=RUNNER_REF,
        baseline_ref={"base_commit": "1" * 40},
    )
    fwd_rec = reconcile(
        make_projection([fwd_entry]),
        routing=[make_routing(fwd_entry)],
        resolutions=[
            make_resolution(
                fwd_entry, rule=RULE_TACH, baseline={"base_commit": "1" * 40}
            )
        ],
        observations=[make_observation("job-1", [fwd_res])],
    )
    reconciliation.validate_receipt(fwd_rec)
    assert fwd_rec["entries"][0]["native_satisfaction"]["requirements"] == []
    fwd_fake_reqs = copy.deepcopy(fwd_rec)
    fwd_fake_reqs["entries"][0]["native_satisfaction"]["requirements"] = [
        {"requirement_id": "fake", "status": "satisfied"}
    ]
    expect_invalid(fwd_fake_reqs, "receipt_satisfaction_requirements_mismatch")


def test_issue109_preserves_valid_failure_unknown_and_partial_diagnostics():
    # Real non-matched failure where inventory succeeded ("satisfied") and links failed
    # ("unsatisfied") must pass validate_receipt() and retain diagnostic details:
    docs_entry = make_entry("docs")
    missing_entry = make_entry("tach")
    na_entry = make_entry("whitespace", applicability="not_applicable")
    inv_ok = native(
        "docs", tool_access="git/ls-files", rule=RULE_DOCS, instance="inv-ok"
    )
    links_fail = native(
        "docs",
        tool_access="lychee/docs",
        rule=RULE_DOCS,
        status="failed",
        instance="links-fail",
        evidence={"broken": 2},
        input_refs=[
            {
                "kind": "check_result",
                "result_id": inv_ok["result_id"],
                "path": "git/ls-files",
            },
            FILE_A,
        ],
    )
    receipt = reconcile(
        make_projection([docs_entry, missing_entry, na_entry]),
        routing=[
            make_routing(docs_entry),
            make_routing(missing_entry, jobs=(), status="unrouted"),
        ],
        resolutions=[make_resolution(docs_entry, rule=RULE_DOCS)],
        observations=[make_observation("job-1", [inv_ok, links_fail])],
    )
    reconciliation.validate_receipt(receipt)
    by_name = {item["name"]: item for item in receipt["entries"]}
    assert by_name["docs"]["binding_status"] == "insufficient"
    assert [
        (r["requirement_id"], r["status"])
        for r in by_name["docs"]["native_satisfaction"]["requirements"]
    ] == [("inventory", "satisfied"), ("links", "unsatisfied")]
    assert by_name["tach"]["binding_status"] == "unrouted"
    assert by_name["whitespace"]["binding_status"] == "not_applicable"


def test_issue109_allows_multi_alternative_requirement_satisfaction(monkeypatch):
    from dataclasses import replace

    tach_def = check_contract.definition_for("tach")
    alt_def = replace(
        tach_def,
        tool_requirements=check_contract.validate_tool_requirements(
            (
                check_contract.ToolRequirement(
                    "lint",
                    (
                        check_contract.ToolIdentity("lint/primary"),
                        check_contract.ToolIdentity("lint/fallback"),
                    ),
                ),
            )
        ),
    )
    monkeypatch.setattr(
        check_contract,
        "_CURRENT_BY_ID",
        {**check_contract._CURRENT_BY_ID, alt_def.check_id: alt_def},
    )
    monkeypatch.setattr(
        check_contract,
        "_BUILTIN_BY_VERSION",
        {
            **check_contract._BUILTIN_BY_VERSION,
            (alt_def.check_id, alt_def.definition_version): alt_def,
        },
    )

    entry = make_entry("tach", definition=alt_def, rule_ref=RULE_TACH)
    res_alt2 = native(
        "tach",
        definition=alt_def,
        tool_access="lint/fallback",
        instance="fallback-1",
    )
    receipt = reconcile(
        make_projection([entry]),
        routing=[make_routing(entry)],
        resolutions=[make_resolution(entry, rule=RULE_TACH)],
        observations=[make_observation("job-1", [res_alt2])],
    )
    reconciliation.validate_receipt(receipt)
    req_entry = receipt["entries"][0]["native_satisfaction"]["requirements"][0]
    assert req_entry["alternatives"] == ["lint/primary", "lint/fallback"]
    assert req_entry["satisfied_by"] == res_alt2["result_id"]

    # Tampering alternatives[] to drop an allowed option refuses:
    bad = copy.deepcopy(receipt)
    bad["entries"][0]["native_satisfaction"]["requirements"][0]["alternatives"] = [
        "lint/primary"
    ]
    payload = {k: v for k, v in bad.items() if k != "receipt_ref"}
    bad["receipt_ref"] = reconciliation._digest(payload)
    with pytest.raises(
        reconciliation.ReconciliationError,
        match="receipt_requirement_structure_mismatch",
    ):
        reconciliation.validate_receipt(bad)

    # Also verify _validate_satisfied_requirements refuses unresolved rule conditions:
    calls = 0

    def _unresolved_on_req_check(*a, **kw):
        nonlocal calls
        calls += 1
        return () if calls == 1 else ("registry_baseline_rule",)

    monkeypatch.setattr(
        check_contract,
        "_unresolved_conditions",
        _unresolved_on_req_check,
    )
    with pytest.raises(
        reconciliation.ReconciliationError,
        match="receipt_satisfaction_rule_unresolved",
    ):
        reconciliation.validate_receipt(receipt)
