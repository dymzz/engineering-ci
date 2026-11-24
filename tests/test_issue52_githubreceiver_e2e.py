"""End-to-end #52 consumer verification on official githubreceiver telemetry."""

from __future__ import annotations

import copy
import http.client
import json
import os
import shutil
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent import futures
from pathlib import Path

import pytest
from google.protobuf import json_format
from test_github_receiver import (
    COLLECTOR_YAML,
    ROOT,
    SECRET,
    WEAVER_FIXTURE_DIR,
    WEAVER_MATCHER_TOML,
    _normalized_span_capture,
    _run_dagger_weaver,
    _sample_job,
    _sample_run,
)

from engineering_ci import (
    admission,
    check_contract,
    cicd_telemetry,
    expected_checks,
    reconciliation,
    required_assessment,
    required_assessment_post,
    selection,
    weaver_tool,
)
from engineering_ci import github_receiver as gr
from engineering_ci import terminal_expected as te

try:
    import grpc
    from opentelemetry.proto.collector.trace.v1 import (
        trace_service_pb2,
        trace_service_pb2_grpc,
    )
except ImportError:  # pragma: no cover
    grpc = None
    trace_service_pb2 = None
    trace_service_pb2_grpc = None

SELECTION_FIXTURE = ROOT / "tests/fixtures/selection-app"
PROFILE_REF = {"name": "consumer.terminal", "version": "1.0.0"}
PROFILE_REVISION = "rev-2026-11-24"
PROFILE_SOURCE = "governance-signed"
EVALUATED_AT = "2026-11-24T12:00:00+00:00"
PRIOR_EVALUATED_AT = "2026-11-24T11:00:00+00:00"
CLOSURE_REF = {"name": "org/policy-closure", "version": "1", "sha256": "6" * 64}
AUTHORITY_REF = {"name": "org/governance-source", "version": "1", "sha256": "7" * 64}
RULE_SOURCE_REF = {"name": "weaver-rule", "version": "1", "sha256": "4" * 64}
SOURCE_RECEIPT_REF = {"name": "p0-source-receipt", "version": "1", "sha256": "5" * 64}
JOB_ISSUER = {"name": "eci-github-runner", "version": "1", "sha256": "c" * 64}
PHASE_WITNESS_ISSUER = {
    "name": "org/phase-witness-issuer",
    "version": "1",
    "sha256": "b" * 64,
}
PHASE_ORDER = {
    "ref": {"name": "org/release-phase-order", "version": "1", "sha256": "3" * 64},
    "phases": ["pre_publish", "post_completion"],
}
QUALIFICATION_REF = {"name": "scheduler-attempt", "version": "1", "sha256": "d" * 64}


def _attach_profile_policy(repo: Path, profile: dict) -> dict:
    binding = profile["approval_binding"]
    binding["approval_digest"] = expected_checks._approval_digest(profile)
    profile["profile_sha256"] = expected_checks.profile_digest(profile)
    declared = profile.get("declared_checks") or []
    cfg_path = repo / "ci-selection.toml"
    existing = (
        cfg_path.read_text(encoding="utf-8")
        .split("\n[effective_profile]", 1)[0]
        .rstrip()
    )
    phase_line = (
        f'phase = "{profile["phase"]}"\n'
        if isinstance(profile.get("phase"), str) and profile["phase"]
        else ""
    )
    cfg_path.write_text(
        existing
        + f"""

[effective_profile]
profile_ref = {{ name = "{profile["profile_ref"]["name"]}", version = "{profile["profile_ref"]["version"]}" }}
profile_revision = "{profile["profile_revision"]}"
approval_source = "{binding["approval_source"]}"
approval_digest = "{binding["approval_digest"]}"
effective_at = "{profile["effective_at"]}"
declared_checks = {json.dumps(declared)}
{phase_line}""",
        encoding="utf-8",
    )
    return selection.load_config(cfg_path)


def _prepare_git_expected_plans(repo_root: Path) -> tuple[dict, dict]:
    """Create real git-backed prior + post Expected plans for live validate_projection."""
    shutil.copytree(SELECTION_FIXTURE, repo_root)

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout

    git("init", "-q", "-b", "main")
    git("config", "user.email", "terminal@example.invalid")
    git("config", "user.name", "Terminal Test")
    git("add", "-A")
    git("commit", "-q", "-m", "base")

    def clean_governed(profile: dict) -> dict:
        profile.pop("governed_rules", None)
        dd = (profile.get("approval_binding") or {}).get("declared_domain")
        if isinstance(dd, dict):
            dd.pop("governed_rules", None)
        return profile

    post_profile = clean_governed(
        expected_checks.build_effective_profile(
            profile_ref=copy.deepcopy(PROFILE_REF),
            profile_revision=PROFILE_REVISION,
            approval_source=PROFILE_SOURCE,
            authority_verified=True,
            effective_at="2026-11-24T00:00:00Z",
            evaluated_at=EVALUATED_AT,
            context="worktree",
            action="worktree",
            target="HEAD",
            phase=check_contract.POST_COMPLETION_PHASE,
            declared_checks=[
                "docs",
                "forwarding",
                "tach",
                "vulture",
                "weaver_terminal",
                "whitespace",
            ],
            required_obligations=[
                {
                    "obligation_ref": "o/terminal",
                    "kind": "check",
                    "check_names": ["weaver_terminal"],
                    "allow_inapplicable": True,
                }
            ],
            advisory_checks=[],
            allowed_inapplicability=[],
            domain_complete=True,
            governed_rules=expected_checks.governed_domain_rules(
                repo_root, config_path=repo_root / "ci-selection.toml"
            ),
        )
    )
    post_cfg = _attach_profile_policy(repo_root, post_profile)
    git("add", "ci-selection.toml")
    git("commit", "-q", "-m", "attach post profile")
    base = git("rev-parse", "HEAD").strip()
    changes = selection.changeset(repo_root, base=base, mode="worktree")
    chosen = selection.select(changes, post_cfg, root=repo_root)
    post_plan = selection.plan(
        chosen,
        config=post_cfg,
        config_path=repo_root / "ci-selection.toml",
        root=repo_root,
        effective_profile=post_profile,
    )

    prior_profile = clean_governed(
        expected_checks.build_effective_profile(
            profile_ref=copy.deepcopy(PROFILE_REF),
            profile_revision=PROFILE_REVISION,
            approval_source=PROFILE_SOURCE,
            authority_verified=True,
            effective_at="2026-11-24T00:00:00Z",
            evaluated_at=PRIOR_EVALUATED_AT,
            context="worktree",
            action="worktree",
            target="HEAD",
            phase=None,
            declared_checks=["docs", "forwarding", "tach", "vulture", "whitespace"],
            required_obligations=[],
            advisory_checks=[],
            allowed_inapplicability=[],
            domain_complete=True,
            governed_rules=expected_checks.governed_domain_rules(
                repo_root, config_path=repo_root / "ci-selection.toml"
            ),
        )
    )
    prior_cfg = _attach_profile_policy(repo_root, prior_profile)
    prior_chosen = selection.select(changes, prior_cfg, root=repo_root)
    prior_plan = selection.plan(
        prior_chosen,
        config=prior_cfg,
        config_path=repo_root / "ci-selection.toml",
        root=repo_root,
        effective_profile=prior_profile,
    )
    # Restore ci-selection.toml to post_profile for live validate_projection on post_plan.
    _attach_profile_policy(repo_root, post_profile)
    return post_plan, prior_plan


def _make_weaver_terminal_report(
    *,
    term_entry: dict,
    source_ref: dict,
    status: str,
    exit_code: int,
    implementation: dict,
    run_ref: str,
    attempt_ref: str,
    rule_ref: dict,
    terminal_correlation_status: str | None = None,
    terminal_correlation_reason: str | None = None,
    gate_status: str | None = None,
    gate_exit_code: int | None = None,
) -> tuple[dict, dict, dict]:
    definition = check_contract.definition_for("weaver_terminal")
    native_results = []
    prior_by_req: dict[str, dict] = {}
    stages = list(definition.tool_requirements)
    for idx, req in enumerate(stages):
        access = req.alternatives[0].access_id
        tool = check_contract.tool_ref(
            access,
            executable="weaver",
            tool_id="weaver",
            tool_id_source="eci_pinned_asset",
            version=weaver_tool.VERSION,
            artifact_sha256=weaver_tool.LINUX_SHA256,
        )
        is_last = idx == len(stages) - 1
        step_status = status if is_last else "passed"
        step_exit = exit_code if is_last else 0
        inputs = [
            {
                "kind": "native_inputs",
                "value": {"mode": "test", "step": req.requirement_id},
            }
        ]
        for pred in req.after:
            pred_res = prior_by_req[pred]
            inputs.append(
                {
                    "kind": "check_result",
                    "result_id": pred_res["result_id"],
                    "check_id": pred_res["check"]["check_id"],
                }
            )
        evidence = {
            "requirement_id": req.requirement_id,
            "gate_status": (
                gate_status if is_last and gate_status is not None else step_status
            ),
            "gate_exit_code": (
                gate_exit_code if is_last and gate_exit_code is not None else step_exit
            ),
        }
        if is_last:
            evidence["terminal_correlation_status"] = (
                terminal_correlation_status
                if terminal_correlation_status is not None
                else ("passed" if step_status == "passed" else "failed")
            )
            evidence["terminal_correlation_reason"] = (
                terminal_correlation_reason
                if terminal_correlation_reason is not None
                else (
                    "ok"
                    if evidence["terminal_correlation_status"] == "passed"
                    else "terminal_correlation_failed"
                )
            )
        step_res = check_contract.native_result(
            definition,
            source=copy.deepcopy(source_ref),
            status=step_status,
            native_exit_code=step_exit,
            result_origin="dagger",
            runner_ref="execution:dagger",
            result_instance=f"exec:{run_ref}:{attempt_ref}:{access}",
            rule_ref=copy.deepcopy(rule_ref),
            profile_ref=copy.deepcopy(term_entry.get("profile_ref")),
            baseline_ref=copy.deepcopy(term_entry.get("baseline_ref")),
            tool=tool,
            phase=check_contract.POST_COMPLETION_PHASE,
            input_refs=inputs,
            evidence=evidence,
        )
        prior_by_req[req.requirement_id] = step_res
        native_results.append(step_res)
    exec_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [
            {
                "name": "weaver_terminal",
                "verification_implementation": copy.deepcopy(implementation),
                "native_results": native_results,
            }
        ],
    }
    report_ref = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "sha256": reconciliation._digest(exec_report).split(":", 1)[1],
    }
    job_receipt = {
        "kind": reconciliation.JOB_RECEIPT_KIND,
        "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
        "job_ref": "weaver-terminal-job",
        "run_ref": run_ref,
        "attempt_ref": attempt_ref,
        "runner_ref": "execution:dagger",
        "report_ref": copy.deepcopy(report_ref),
        "issuer_ref": copy.deepcopy(JOB_ISSUER),
        "verification_status": "upstream_verified",
    }
    return exec_report, report_ref, job_receipt


def _run_issue52_gate_chain(
    *,
    stage_dir: Path,
    run_id: int,
    attempt: int,
    run_conclusion: str,
    jobs: list[dict],
    capture: dict,
    terminal_status: str,
    terminal_exit_code: int,
    real_check_result: dict | None = None,
    trust_authorized: bool = True,
    run_started_at: str | None = "2026-11-10T10:00:01Z",
    executed_job_ids: list[int | str] | None = None,
    terminal_correlation_status: str | None = None,
    terminal_correlation_reason: str | None = None,
    gate_status: str | None = None,
    gate_exit_code: int | None = None,
) -> dict:
    """Execute #52 -> #74 -> #66-C -> #63 -> #61 prior/post Required gate chain."""
    closure_dir = stage_dir / "closure"
    closure_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        WEAVER_FIXTURE_DIR / "registry", closure_dir / "registry", dirs_exist_ok=True
    )
    shutil.copytree(
        WEAVER_FIXTURE_DIR / "advice-policies",
        closure_dir / "advice-policies",
        dirs_exist_ok=True,
    )
    shutil.copy2(
        WEAVER_FIXTURE_DIR / "registry-sha256.json",
        closure_dir / "registry-sha256.json",
    )
    shutil.copy2(WEAVER_MATCHER_TOML, closure_dir / ".weaver.toml")
    (closure_dir / "capture.json").write_text(
        json.dumps(capture, indent=2), encoding="utf-8"
    )

    expectations = gr.build_weaver_expectations(
        run_id=run_id,
        attempt=attempt,
        run_status="completed",
        run_conclusion=run_conclusion,
        jobs=jobs,
        run_started_at=run_started_at,
        executed_job_ids=executed_job_ids,
    )
    executed_jobs_for_matchers = (
        gr.partition_witness_jobs(
            jobs,
            rid=run_id,
            att=attempt,
            run_started_at=run_started_at,
            executed_job_ids=executed_job_ids,
        )["executed_jobs"]
        if attempt > 1 or executed_job_ids is not None
        else jobs
    )
    (closure_dir / "expected.json").write_text(
        json.dumps(expectations, indent=2), encoding="utf-8"
    )
    (closure_dir / "ci-weaver.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                'registry = "registry"',
                'registry_manifest = "registry-sha256.json"',
                'matcher_config = ".weaver.toml"',
                (
                    "expected_matchers = "
                    f"{json.dumps(gr.expected_matchers_for_jobs(executed_jobs_for_matchers))}"
                ),
                'otlp = "capture.json"',
                'expectations = "expected.json"',
                'advice_policies = "advice-policies"',
                "",
                "[origin]",
                'repository = "https://github.com/open-telemetry/semantic-conventions"',
                'commit = "fb30ca17193b551473b1f43c315482918ab6987c"',
                'version = "1.45.0"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    # 1. Witness <-> githubreceiver trace reconciliation (#52)
    raw_witness = {
        "attempt": {
            "id": run_id,
            "run_attempt": attempt,
            "status": "completed",
            "conclusion": run_conclusion,
            "run_started_at": run_started_at,
            "head_sha": "7ea85c24a62e65a9384a0abe9eb541ca54064cbc",
            "html_url": f"https://github.com/dymzz/engineering-ci/actions/runs/{run_id}",
            "previous_attempt_url": (
                f"https://api.github.com/repos/dymzz/engineering-ci/actions/runs/{run_id}/attempts/{attempt - 1}"
                if attempt > 1
                else None
            ),
            "repository": {
                "full_name": "dymzz/engineering-ci",
                "html_url": "https://github.com/dymzz/engineering-ci",
            },
        },
        "job_pages": [{"total_count": len(jobs), "jobs": jobs}],
    }
    witness_reconcile = cicd_telemetry.reconcile_attempt_witness_capture(
        raw_witness, capture, executed_job_ids=executed_job_ids
    )
    assert witness_reconcile["ok"] is True, witness_reconcile

    extracted = gr.extract_capture_spans(capture)
    trace_reconcile = cicd_telemetry.reconcile_expected_trace(
        run_id=str(run_id),
        attempt=attempt,
        job_ids=[str(j["id"]) for j in executed_jobs_for_matchers],
        observed_spans=extracted,
    )
    assert trace_reconcile["id_scheme"] == gr.ID_SCHEME
    assert trace_reconcile["ok"] is True, trace_reconcile

    # 2. Selection post_completion_plan + validation (#52-D4 / #74)
    mapped_run, _ = gr.map_github_conclusion(run_conclusion)
    composite_run_id = f"{run_id}/{attempt}"
    plan = selection.post_completion_plan(
        root=closure_dir,
        config_path=closure_dir / "pyproject.toml",
        weaver_config="ci-weaver.toml",
        run_id=composite_run_id,
        attempt=attempt,
        result=mapped_run,
    )
    assert selection.validate_post_completion_binding(plan, closure_dir)["ok"] is True

    # 3. Git-backed #66-B2 Expected + live Terminal Expected Binding (#52-D4)
    repo_root = stage_dir / "expected-repo"
    expected_plan, prior_plan = _prepare_git_expected_plans(repo_root)
    post_expected = expected_plan["expected_checks"]
    post_profile = post_expected["effective_profile"]
    term_entry = next(
        item for item in post_expected["checks"] if item["name"] == "weaver_terminal"
    )
    post_scope_id = post_expected["scope_id"]
    sealed_witness = te.seal_phase_witness(
        scope_id=post_scope_id,
        expected_set_ref=post_expected["expected_set_ref"],
        phase=check_contract.POST_COMPLETION_PHASE,
        profile_ref=PROFILE_REF,
        profile_revision=PROFILE_REVISION,
        issuer_ref=PHASE_WITNESS_ISSUER,
        validated_at=EVALUATED_AT,
        domain_complete=True,
    )
    completed_run = {
        "run_id": composite_run_id,
        "attempt": attempt,
        "result": mapped_run,
        "status": "completed",
        "source": "github_attempt_api",
        "subject": {
            "declared_target_tree": term_entry["source_ref"].get(
                "declared_target_tree"
            ),
            "head_commit": term_entry["event_ref"].get("head_commit"),
            "base_commit": term_entry["event_ref"].get("base_commit"),
            "head_ref": term_entry["event_ref"].get("head_ref"),
            "base_ref": term_entry["event_ref"].get("base_ref"),
            "expected_check_ref": term_entry["expected_check_ref"],
            "scope_id": post_scope_id,
        },
    }
    term_binding = te.bind_terminal_expected(
        post_expected,
        plan=plan,
        completed_run=completed_run,
        root=closure_dir,
        expected_authority=lambda _ctx: {
            "accepted": True,
            "status": "authorized",
            "authority_ref": AUTHORITY_REF,
        },
        completed_run_authority=lambda _ctx: {
            "accepted": True,
            "status": "authorized",
            "subject_verified": True,
            "authority_ref": AUTHORITY_REF,
        },
        phase_witness=sealed_witness,
        phase_witness_authority=lambda _ctx: {
            "accepted": True,
            "status": "authorized",
            "authority_ref": AUTHORITY_REF,
        },
        projection_git=expected_plan["git"],
        expected_plan=expected_plan,
        expected_root=repo_root,
    )
    assert term_binding["authorized"] is True, term_binding

    # 4. #66-C Receipt + #63 Admission across every entry in post_expected
    resolved_term_rule = copy.deepcopy(term_entry.get("rule_ref") or RULE_SOURCE_REF)
    resolved_term_source = check_contract.source_ref(
        term_entry["source_ref"]["snapshot"],
        declared_target_tree=term_entry["source_ref"].get("declared_target_tree"),
        head_commit=term_entry["source_ref"].get("head_commit"),
        actual_execution_source="worktree",
        content_digest=selection.directory_content_id(closure_dir).removeprefix(
            "sha256:"
        ),
        verified_scope=sorted(
            {
                *list(term_entry["source_ref"].get("verified_input_scope") or ()),
                "ci-weaver.toml",
            }
        ),
        scope_kind="check_input_closure",
    )
    if real_check_result is not None:
        impl = copy.deepcopy(real_check_result["verification_implementation"])
        real_def = check_contract.definition_for("weaver_terminal")
        id_map: dict[str, str] = {}
        real_natives = []
        for real_raw in real_check_result["native_results"]:
            raw_tool = real_raw.get("tool") or {}
            live_tool = check_contract.tool_ref(
                raw_tool.get("access_id")
                or real_def.tool_requirements[0].alternatives[0].access_id,
                executable=raw_tool.get("executable") or "weaver",
                tool_id=raw_tool.get("tool_id") or "weaver",
                tool_id_source=raw_tool.get("tool_id_source") or "eci_pinned_asset",
                version=raw_tool.get("version") or weaver_tool.VERSION,
                artifact_sha256=raw_tool.get("artifact_sha256")
                or weaver_tool.LINUX_SHA256,
            )
            remapped_inputs = []
            for ref in real_raw.get("input_refs") or ():
                if (
                    isinstance(ref, dict)
                    and ref.get("kind") == "check_result"
                    and ref.get("result_id") in id_map
                ):
                    updated = copy.deepcopy(ref)
                    updated["result_id"] = id_map[ref["result_id"]]
                    remapped_inputs.append(updated)
                else:
                    remapped_inputs.append(copy.deepcopy(ref))
            rebound = check_contract.native_result(
                real_def,
                source=copy.deepcopy(resolved_term_source),
                status=real_raw["status"],
                native_exit_code=real_raw["native_exit_code"],
                result_origin=real_raw["result_origin"],
                runner_ref=real_raw["runner_ref"],
                result_instance=real_raw["result_instance"],
                rule_ref=copy.deepcopy(resolved_term_rule),
                profile_ref=copy.deepcopy(term_entry.get("profile_ref")),
                baseline_ref=copy.deepcopy(term_entry.get("baseline_ref")),
                tool=live_tool,
                phase=check_contract.POST_COMPLETION_PHASE,
                input_refs=remapped_inputs,
                artifact_refs=real_raw.get("artifact_refs") or (),
                evidence=real_raw.get("evidence"),
            )
            id_map[real_raw["result_id"]] = rebound["result_id"]
            real_natives.append(rebound)
        exec_report = {
            "kind": reconciliation.EXECUTION_REPORT_KIND,
            "schema_version": 1,
            "checks": [
                {
                    "name": "weaver_terminal",
                    "verification_implementation": impl,
                    "native_results": real_natives,
                }
            ],
        }
        report_ref = {
            "kind": reconciliation.EXECUTION_REPORT_KIND,
            "schema_version": 1,
            "sha256": reconciliation._digest(exec_report).split(":", 1)[1],
        }
        job_receipt = {
            "kind": reconciliation.JOB_RECEIPT_KIND,
            "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
            "job_ref": "weaver-terminal-job",
            "run_ref": composite_run_id,
            "attempt_ref": f"attempt/{attempt}",
            "runner_ref": real_natives[-1]["runner_ref"],
            "report_ref": copy.deepcopy(report_ref),
            "issuer_ref": copy.deepcopy(JOB_ISSUER),
            "verification_status": "upstream_verified",
        }
    else:
        impl = {
            "implementation_id": "engineering-ci/dagger/weaver",
            "implementation_version": "1",
            "implementation_sha256": "e" * 64,
        }
        exec_report, report_ref, job_receipt = _make_weaver_terminal_report(
            term_entry=term_entry,
            source_ref=resolved_term_source,
            status=terminal_status,
            exit_code=terminal_exit_code,
            implementation=impl,
            run_ref=composite_run_id,
            attempt_ref=f"attempt/{attempt}",
            rule_ref=resolved_term_rule,
            terminal_correlation_status=terminal_correlation_status,
            terminal_correlation_reason=terminal_correlation_reason,
            gate_status=gate_status,
            gate_exit_code=gate_exit_code,
        )

    native_results = list(exec_report["checks"][0]["native_results"])
    native_res = native_results[0]
    routing = []
    resolutions = []
    for entry in post_expected["checks"]:
        if entry["name"] == "weaver_terminal":
            routing.append(
                {
                    "expected_check_ref": entry["expected_check_ref"],
                    "job_refs": ["weaver-terminal-job"],
                    "job_instances": [
                        {
                            "job_ref": "weaver-terminal-job",
                            "run_ref": composite_run_id,
                            "attempt_ref": f"attempt/{attempt}",
                            "runner_ref": native_res["runner_ref"],
                            "attempt_eligibility": "eligible",
                            "qualification_ref": copy.deepcopy(QUALIFICATION_REF),
                        }
                    ],
                    "mapping_status": "routed",
                    "scheduler_ref": {"name": "github-actions", "version": "1"},
                    "effective_config_ref": {
                        "name": "githubreceiver-config",
                        "version": "1",
                    },
                    "reason": None,
                }
            )
            resolutions.append(
                {
                    "expected_check_ref": entry["expected_check_ref"],
                    "rule_ref": copy.deepcopy(resolved_term_rule),
                    "source_ref": copy.deepcopy(resolved_term_source),
                    "phase": check_contract.POST_COMPLETION_PHASE,
                    "baseline_ref": copy.deepcopy(entry.get("baseline_ref")),
                    **(
                        {"baseline_source_ref": copy.deepcopy(SOURCE_RECEIPT_REF)}
                        if entry.get("baseline_ref") is not None
                        else {}
                    ),
                    "rule_source_ref": copy.deepcopy(RULE_SOURCE_REF),
                    "source_receipt_ref": copy.deepcopy(SOURCE_RECEIPT_REF),
                }
            )
        else:
            routing.append(
                {
                    "expected_check_ref": entry["expected_check_ref"],
                    "job_refs": [],
                    "job_instances": [],
                    "mapping_status": "skipped",
                    "scheduler_ref": {"name": "github-actions", "version": "1"},
                    "effective_config_ref": {
                        "name": "githubreceiver-config",
                        "version": "1",
                    },
                    "reason": "advisory_or_not_applicable",
                }
            )
            resolutions.append(
                {
                    "expected_check_ref": entry["expected_check_ref"],
                    "rule_ref": copy.deepcopy(entry["rule_ref"]),
                    "source_ref": copy.deepcopy(entry["source_ref"]),
                    "baseline_ref": copy.deepcopy(entry.get("baseline_ref")),
                    **(
                        {"baseline_source_ref": copy.deepcopy(SOURCE_RECEIPT_REF)}
                        if entry.get("baseline_ref") is not None
                        else {}
                    ),
                    "rule_source_ref": copy.deepcopy(RULE_SOURCE_REF),
                    "source_receipt_ref": copy.deepcopy(SOURCE_RECEIPT_REF),
                }
            )

    observations = [
        {
            "job_ref": "weaver-terminal-job",
            "run_ref": composite_run_id,
            "attempt_ref": f"attempt/{attempt}",
            "runner_ref": native_res["runner_ref"],
            "report": exec_report,
            "report_ref": copy.deepcopy(report_ref),
            "native_results": native_results,
            "job_receipt": copy.deepcopy(job_receipt),
        }
    ]
    receipt = reconciliation.reconcile_expected_results(
        post_expected,
        routing=routing,
        resolutions=resolutions,
        observations=observations,
        terminal_bindings=[term_binding],
    )
    receipt_without_binding = reconciliation.reconcile_expected_results(
        post_expected,
        routing=routing,
        resolutions=resolutions,
        observations=observations,
    )
    term_def = check_contract.definition_for("weaver_terminal")
    policy = {
        "trusted_issuers": [copy.deepcopy(JOB_ISSUER)],
        "approved_implementations": [
            {
                "check_id": term_def.check_id,
                "definition_version": term_def.definition_version,
                "implementation_id": impl["implementation_id"],
                "implementation_version": impl["implementation_version"],
                "implementation_sha256": impl["implementation_sha256"],
            }
        ],
    }
    admission_doc = admission.admit_evidence(
        post_expected,
        receipt,
        reports=[exec_report],
        job_receipts=[job_receipt],
        policy=policy,
        authority=lambda _ctx: trust_authorized,
        resolutions=resolutions,
        resolution_authority=lambda _ctx: True,
    )

    # 5. #61 R1 Post Assessment & R5 Prior-vs-Post Reconciliation
    target_ref = (
        post_expected["event_ref"].get("head_ref")
        or post_expected["event_ref"].get("base_ref")
        or "HEAD"
    )
    post_obligation = {
        "obligation_ref": "o/terminal",
        "kind": "check",
        "target_ref": target_ref,
        "action": "worktree",
        "phase": check_contract.POST_COMPLETION_PHASE,
        "candidate_digest": None,
        "applicability": "required",
        "reason": "required_by_effective_profile",
        "applicability_basis_ref": None,
        "due_phase": None,
        "due_at": None,
        "expected_check_refs": [term_entry["expected_check_ref"]],
        "check_names": ["weaver_terminal"],
    }
    post_universe = {
        "kind": required_assessment.UNIVERSE_KIND,
        "schema_version": 1,
        "profile": {
            "profile_ref": copy.deepcopy(PROFILE_REF),
            "profile_revision": PROFILE_REVISION,
            "approval_source": PROFILE_SOURCE,
            "profile_sha256": post_profile["profile_sha256"],
            "approval_digest": post_profile["approval_binding"]["approval_digest"],
        },
        "authority": {
            "status": "verified",
            "source_ref": copy.deepcopy(AUTHORITY_REF),
            "approved_at": "2026-11-24T00:00:00Z",
            "effective_at": "2026-11-24T00:00:00Z",
            "valid_from": None,
            "valid_until": None,
            "revoked_at": None,
            "evaluated_at": EVALUATED_AT,
        },
        "governance_domain": {
            "closure_status": "closed",
            "closure_ref": copy.deepcopy(CLOSURE_REF),
            "scope": "organization",
            "delegation_domains": [],
            "inheritance": [],
            "exclusions": [],
        },
        "evaluation": {
            "context": "worktree",
            "action": "worktree",
            "target": "HEAD",
            "phase": check_contract.POST_COMPLETION_PHASE,
            "phase_order": copy.deepcopy(PHASE_ORDER),
            "refs": [{"target_ref": target_ref, "ref_action": "update"}],
            "candidate": {"commit": post_expected["event_ref"]["head_commit"]},
            "evaluated_at": EVALUATED_AT,
        },
        "check_domain": {
            "scope_id": post_expected["scope_id"],
            "expected_set_ref": post_expected["expected_set_ref"],
            "status": post_expected["status"],
            "consumer_completeness": post_expected["consumer_completeness"],
        },
        "obligations": [post_obligation],
        "universe_status": "complete",
        "reason": None,
        "contract_sample": False,
    }
    post_universe["universe_ref"] = required_assessment._digest(post_universe)

    def _phase_auth(ctx):
        return {
            "status": "verified",
            "scope_id": ctx["scope_id"],
            "expected_set_ref": ctx["expected_set_ref"],
            "phase": ctx["phase"],
            "profile_sha256": ctx["profile_sha256"],
            "universe_ref": ctx["universe_ref"],
            "proof_ref": ctx["proof_ref"],
            "issuer_ref": copy.deepcopy(PHASE_WITNESS_ISSUER),
            "verification_ref": copy.deepcopy(AUTHORITY_REF),
        }

    def _universe_auth(ctx):
        return {
            "status": "verified",
            "universe_ref": ctx["universe_ref"],
            "profile_sha256": ctx["profile_sha256"],
            "scope_id": ctx["scope_id"],
            "expected_set_ref": ctx["expected_set_ref"],
            "issuer_ref": copy.deepcopy(AUTHORITY_REF),
            "verification_ref": copy.deepcopy(AUTHORITY_REF),
        }

    def _admission_source_auth(req):
        return {
            "status": "verified",
            "source_admission_ref": req["source_admission_ref"],
            "current_authorization_ref": req["current_authorization_ref"],
            "issuer_ref": copy.deepcopy(JOB_ISSUER),
            "verification_ref": copy.deepcopy(SOURCE_RECEIPT_REF),
        }

    post_assessment = required_assessment.assess_required_obligations(
        universe=post_universe,
        expected=post_expected,
        evaluated_at=EVALUATED_AT,
        admission=admission_doc,
        receipt=receipt,
        phase_authority=_phase_auth,
        universe_authority=_universe_auth,
        admission_source_authority=_admission_source_auth,
        admission_current_authorization={
            "phase": check_contract.POST_COMPLETION_PHASE,
            "action": "worktree",
            "target_ref": target_ref,
            "candidate_digest": None,
            "profile_sha256": post_profile["profile_sha256"],
        },
        terminal_bindings=[term_binding],
    )
    post_assessment_without_binding = required_assessment.assess_required_obligations(
        universe=post_universe,
        expected=post_expected,
        evaluated_at=EVALUATED_AT,
        admission=admission_doc,
        receipt=receipt,
        phase_authority=_phase_auth,
        universe_authority=_universe_auth,
        admission_source_authority=_admission_source_auth,
        admission_current_authorization={
            "phase": check_contract.POST_COMPLETION_PHASE,
            "action": "worktree",
            "target_ref": target_ref,
            "candidate_digest": None,
            "profile_sha256": post_profile["profile_sha256"],
        },
    )

    prior_expected = prior_plan["expected_checks"]
    prior_profile = prior_expected["effective_profile"]
    prior_universe = copy.deepcopy(post_universe)
    prior_universe["evaluation"]["phase"] = "pre_publish"
    prior_universe["evaluation"]["evaluated_at"] = PRIOR_EVALUATED_AT
    prior_universe["profile"]["profile_sha256"] = prior_profile["profile_sha256"]
    prior_universe["profile"]["approval_digest"] = prior_profile["approval_binding"][
        "approval_digest"
    ]
    prior_universe["authority"]["evaluated_at"] = PRIOR_EVALUATED_AT
    prior_universe["check_domain"] = {
        "scope_id": prior_expected["scope_id"],
        "expected_set_ref": prior_expected["expected_set_ref"],
        "status": prior_expected["status"],
        "consumer_completeness": prior_expected["consumer_completeness"],
    }
    prior_universe["obligations"] = []
    prior_universe.pop("universe_ref", None)
    prior_universe["universe_ref"] = required_assessment._digest(prior_universe)
    prior_assessment = required_assessment.assess_required_obligations(
        universe=prior_universe,
        expected=prior_expected,
        evaluated_at=PRIOR_EVALUATED_AT,
        phase_authority=_phase_auth,
        universe_authority=_universe_auth,
    )

    post_stage = required_assessment_post.reconcile_post_stage(
        prior_assessment,
        post_assessment,
        trigger_event={
            "kind": "run_completed",
            "observed_at": EVALUATED_AT,
            "run_id": composite_run_id,
            "run_attempt": attempt,
            "target": "HEAD",
            "run_completed": True,
        },
        due_window={
            "earliest_at": "2026-11-24T10:00:00+00:00",
            "due_at": "2026-11-24T18:00:00+00:00",
        },
        evaluated_at=EVALUATED_AT,
        phase_order=copy.deepcopy(PHASE_ORDER),
        phase_order_authority=lambda _ctx: {"accepted": True, "status": "authorized"},
        trigger_authority=lambda _ctx: {"accepted": True, "status": "authorized"},
        due_window_authority=lambda _ctx: {"accepted": True, "status": "authorized"},
    )
    return {
        "witness_reconcile": witness_reconcile,
        "trace_reconcile": trace_reconcile,
        "plan": plan,
        "closure_dir": closure_dir,
        "term_entry_ref": term_entry["expected_check_ref"],
        "term_binding": term_binding,
        "receipt": receipt,
        "receipt_without_binding": receipt_without_binding,
        "admission": admission_doc,
        "post_assessment": post_assessment,
        "post_assessment_without_binding": post_assessment_without_binding,
        "post_stage": post_stage,
    }


def test_issue52_gate_chain_passes_on_valid_githubreceiver_capture(tmp_path):
    jobs = [
        _sample_job(
            job_id=8001, run_id=7001, attempt=1, name="lint", conclusion="success"
        )["workflow_job"],
        _sample_job(
            job_id=8002, run_id=7001, attempt=1, name="tests", conclusion="success"
        )["workflow_job"],
    ]
    cap = _normalized_span_capture(
        run_id=7001, attempt=1, run_conclusion="success", jobs=jobs
    )
    outcome = _run_issue52_gate_chain(
        stage_dir=tmp_path / "pass",
        run_id=7001,
        attempt=1,
        run_conclusion="success",
        jobs=jobs,
        capture=cap,
        terminal_status="passed",
        terminal_exit_code=0,
    )
    term_entry_ref = outcome["term_entry_ref"]
    unbound_term = next(
        e
        for e in outcome["receipt_without_binding"]["entries"]
        if e["expected_check_ref"] == term_entry_ref
    )
    assert unbound_term["binding_status"] == "unknown"
    assert unbound_term["reason"] == "applicability_unknown"
    bound_term = next(
        e
        for e in outcome["receipt"]["entries"]
        if e["expected_check_ref"] == term_entry_ref
    )
    assert bound_term["binding_status"] == "matched"
    assert bound_term["terminal_binding_ref"] == outcome["term_binding"]["binding_ref"]
    assert outcome["receipt"]["all_observations_reconciled"] is True
    assert outcome["admission"]["status"] == "admitted"
    assert outcome["post_assessment_without_binding"]["status"] == "blocked"
    assert outcome["post_assessment"]["status"] == "passed"
    assert outcome["post_stage"]["status"] == "verified"


def test_issue52_gate_chain_blocks_on_native_failure_even_with_valid_telemetry(
    tmp_path,
):
    jobs = [
        _sample_job(
            job_id=8101, run_id=7002, attempt=1, name="tests", conclusion="failure"
        )["workflow_job"]
    ]
    cap = _normalized_span_capture(
        run_id=7002, attempt=1, run_conclusion="failure", jobs=jobs
    )
    outcome = _run_issue52_gate_chain(
        stage_dir=tmp_path / "fail",
        run_id=7002,
        attempt=1,
        run_conclusion="failure",
        jobs=jobs,
        capture=cap,
        terminal_status="failed",
        terminal_exit_code=1,
    )
    assert outcome["post_assessment"]["status"] == "blocked"
    assert outcome["post_stage"]["status"] == "rejected"


def test_issue52_gate_chain_blocks_when_trusted_authority_rejects(tmp_path):
    jobs = [
        _sample_job(
            job_id=8201, run_id=7003, attempt=1, name="lint", conclusion="success"
        )["workflow_job"]
    ]
    cap = _normalized_span_capture(
        run_id=7003, attempt=1, run_conclusion="success", jobs=jobs
    )
    outcome = _run_issue52_gate_chain(
        stage_dir=tmp_path / "untrusted",
        run_id=7003,
        attempt=1,
        run_conclusion="success",
        jobs=jobs,
        capture=cap,
        terminal_status="passed",
        terminal_exit_code=0,
        trust_authorized=False,
    )
    assert outcome["admission"]["status"] == "rejected"
    assert outcome["post_assessment"]["status"] == "blocked"
    assert outcome["post_stage"]["status"] == "rejected"


def test_issue52_reconcile_attempt_witness_handles_zero_job_pagination_and_partial_rerun():
    job_p1 = _sample_job(
        job_id=8301, run_id=7004, attempt=2, name="tests", conclusion="success"
    )["workflow_job"]
    job_p2 = _sample_job(
        job_id=8302, run_id=7004, attempt=2, name="lint", conclusion="skipped"
    )["workflow_job"]
    cap = _normalized_span_capture(
        run_id=7004, attempt=2, run_conclusion="success", jobs=[job_p1, job_p2]
    )
    witness = {
        "attempt": {
            "id": 7004,
            "run_attempt": 2,
            "status": "completed",
            "conclusion": "success",
            "run_started_at": "2026-11-10T10:00:01Z",
            "head_sha": "7ea85c24a62e65a9384a0abe9eb541ca54064cbc",
            "previous_attempt_url": "https://api.github.com/repos/dymzz/engineering-ci/actions/runs/7004/attempts/1",
            "repository": {"html_url": "https://github.com/dymzz/engineering-ci"},
        },
        "job_pages": [
            {"total_count": 2, "jobs": [job_p1]},
            {"total_count": 2, "jobs": [job_p2]},
        ],
    }
    ok = cicd_telemetry.reconcile_attempt_witness_capture(witness, cap)
    assert ok["ok"] is True
    assert ok["expected_previous_trace_id"] == gr.trace_id(7004, 1)

    short = copy.deepcopy(witness)
    short["job_pages"] = [{"total_count": 2, "jobs": [job_p1]}]
    with pytest.raises(cicd_telemetry.TelemetryError, match="jobs_page_incomplete"):
        cicd_telemetry.reconcile_attempt_witness_capture(short, cap)

    zero_cap = _normalized_span_capture(
        run_id=7005, attempt=1, run_conclusion="cancelled", jobs=[]
    )
    zero_witness = {
        "attempt": {
            "id": 7005,
            "run_attempt": 1,
            "status": "completed",
            "conclusion": "cancelled",
            "head_sha": "7ea85c24a62e65a9384a0abe9eb541ca54064cbc",
            "repository": {"html_url": "https://github.com/dymzz/engineering-ci"},
        },
        "job_pages": [{"total_count": 0, "jobs": []}],
    }
    zero_ok = cicd_telemetry.reconcile_attempt_witness_capture(zero_witness, zero_cap)
    assert zero_ok["ok"] is True
    assert zero_ok["task_coverage"] == "zero_task_confirmed"


def _docker_and_dagger_available() -> bool:
    if grpc is None or os.environ.get("ECI_SKIP_LIVE_COLLECTOR_TESTS") == "1":
        return False
    if not shutil.which("docker") or not shutil.which("dagger"):
        return False
    probe = subprocess.run(
        ["docker", "image", "inspect", gr.COLLECTOR_CONTRIB_REF],
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


class _OtlpCaptureServer(
    trace_service_pb2_grpc.TraceServiceServicer if trace_service_pb2_grpc else object
):
    def __init__(self) -> None:
        self.batches: list[dict] = []
        self.lock = threading.Lock()

    def Export(self, request, context):
        payload = json_format.MessageToDict(request, preserving_proto_field_name=False)
        with self.lock:
            self.batches.append(payload)
        return trace_service_pb2.ExportTraceServiceResponse()


def _rmtree(path: Path) -> None:
    def _onexc(func, p, _err):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if path.exists():
        shutil.rmtree(path, onexc=_onexc)


@pytest.mark.skipif(
    not _docker_and_dagger_available(),
    reason="Requires Docker with pinned otelcol-contrib image and Dagger CLI",
)
def test_live_githubreceiver_full_issue52_terminal_trusted_required_e2e():
    """Live Webhook -> otelcol-contrib -> Dagger Weaver Terminal -> #66-C -> #63 -> #61."""
    work_root = ROOT / ".artifacts" / "pytest-issue52-e2e"
    _rmtree(work_root)
    work_root.mkdir(parents=True)

    try:
        cfg_dir = work_root / "cfg"
        cfg_dir.mkdir()
        live_yaml = COLLECTOR_YAML.read_text(encoding="utf-8").replace(
            "exporters:\n  otlp_grpc:\n    endpoint: ${env:ECI_OTLP_GRPC_ENDPOINT}",
            "exporters:\n  otlp_grpc:\n    endpoint: ${env:ECI_OTLP_GRPC_ENDPOINT}\n    tls:\n      insecure: true",
        )
        (cfg_dir / "live.yaml").write_text(live_yaml, encoding="utf-8")

        servicer = _OtlpCaptureServer()
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
        trace_service_pb2_grpc.add_TraceServiceServicer_to_server(servicer, server)
        grpc_port = server.add_insecure_port("0.0.0.0:0")
        server.start()

        container_name = f"eci-test-52-{os.getpid()}"
        subprocess.run(
            ["docker", "rm", "-f", container_name], capture_output=True, check=False
        )
        proc = subprocess.Popen(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                container_name,
                "-p",
                "19429:19418",
                "-e",
                f"GITHUB_WEBHOOK_SECRET={SECRET}",
                "-e",
                "GITHUB_SCRAPER_ORG=dymzz",
                "-e",
                f"ECI_OTLP_GRPC_ENDPOINT=host.docker.internal:{grpc_port}",
                "-v",
                f"{cfg_dir}:/cfg:ro",
                gr.COLLECTOR_CONTRIB_REF,
                "--config=/cfg/live.yaml",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 30
            healthy = False
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(
                        "http://127.0.0.1:19429/health", timeout=2
                    ) as resp:
                        if resp.status == 200:
                            healthy = True
                            break
                except (
                    http.client.HTTPException,
                    urllib.error.URLError,
                    TimeoutError,
                    OSError,
                ):
                    time.sleep(0.25)
            assert healthy

            job1 = _sample_job(
                job_id=9101, run_id=6101, attempt=2, name="lint", conclusion="success"
            )
            job2 = _sample_job(
                job_id=9102, run_id=6101, attempt=2, name="tests", conclusion="success"
            )
            run2 = _sample_run(
                run_id=6101, attempt=2, conclusion="success", include_custom_props=True
            )
            for idx, (ev, body) in enumerate(
                [("workflow_job", job1), ("workflow_job", job2), ("workflow_run", run2)]
            ):
                raw = json.dumps(body).encode("utf-8")
                req = urllib.request.Request(
                    "http://127.0.0.1:19429/events",
                    data=raw,
                    method="POST",
                    headers=gr.webhook_headers(
                        ev, raw, secret=SECRET, delivery_id=f"d52-{idx}"
                    ),
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    assert r.status == 200

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and len(servicer.batches) < 3:
                time.sleep(0.2)
            assert len(servicer.batches) == 3
        finally:
            subprocess.run(
                ["docker", "stop", "-t", "5", container_name],
                capture_output=True,
                check=False,
            )
            proc.communicate(timeout=15)
            server.stop(0)

        capture = gr.merge_otlp_json_batches(servicer.batches)
        jobs = [job1["workflow_job"], job2["workflow_job"]]
        run_started_at = run2["workflow_run"]["run_started_at"]
        expectations = gr.build_weaver_expectations(
            run_id=6101,
            attempt=2,
            run_status="completed",
            run_conclusion="success",
            jobs=jobs,
            run_started_at=run_started_at,
        )
        weaver_result = _run_dagger_weaver(
            work_root / "weaver-6101",
            kind="terminal",
            capture=capture,
            expected=expectations,
            expected_matchers=gr.expected_matchers_for_jobs(jobs),
        )
        assert weaver_result["status"] == "passed"
        assert weaver_result["native_satisfaction"]["status"] == "satisfied"
        assert weaver_result["terminal_correlation_status"] == "passed"

        outcome = _run_issue52_gate_chain(
            stage_dir=work_root / "gate-6101",
            run_id=6101,
            attempt=2,
            run_conclusion="success",
            jobs=jobs,
            capture=capture,
            terminal_status="passed",
            terminal_exit_code=0,
            real_check_result=weaver_result,
            run_started_at=run_started_at,
        )
        term_entry_ref = outcome["term_entry_ref"]
        bound_term = next(
            e
            for e in outcome["receipt"]["entries"]
            if e["expected_check_ref"] == term_entry_ref
        )
        assert bound_term["binding_status"] == "matched"
        assert outcome["receipt"]["all_observations_reconciled"] is True
        assert outcome["admission"]["status"] == "admitted"
        assert outcome["post_assessment"]["status"] == "passed"
        assert outcome["post_stage"]["status"] == "verified"

        (outcome["closure_dir"] / "capture.json").write_text(
            '{"resourceSpans":[]}\n', encoding="utf-8"
        )
        drift = selection.validate_post_completion_binding(
            outcome["plan"], outcome["closure_dir"]
        )
        assert drift["ok"] is False
    finally:
        _rmtree(work_root)


def test_issue52_partial_rerun_separates_result_and_executed_inventories():
    """Run 44713410255 Attempt 2 pattern: reused jobs stay in result_inventory only."""
    exec_job = {
        **_sample_job(
            job_id=136212522105,
            run_id=44713410255,
            attempt=2,
            name="version-metadata",
            conclusion="success",
        )["workflow_job"],
        "created_at": "2026-11-20T09:49:36Z",
        "started_at": "2026-11-20T09:49:39Z",
        "completed_at": "2026-11-20T09:50:49Z",
    }
    reused_jobs = [
        {
            **_sample_job(
                job_id=jid,
                run_id=44713410255,
                attempt=2,
                name=name,
                conclusion="success",
            )["workflow_job"],
            "created_at": "2026-11-20T09:49:40Z",
            "started_at": "2026-11-20T09:45:32Z",
            "completed_at": "2026-11-20T09:47:10Z",
        }
        for jid, name in [
            (136212523496, "python-ci"),
            (136212524904, "dagger-gate"),
            (136212602878, "sync-tool-tests"),
        ]
    ]
    all_jobs = [exec_job, *reused_jobs]
    cap = _normalized_span_capture(
        run_id=44713410255,
        attempt=2,
        run_conclusion="success",
        jobs=[exec_job],
    )
    witness = {
        "attempt": {
            "id": 44713410255,
            "run_attempt": 2,
            "status": "completed",
            "conclusion": "success",
            "run_started_at": "2026-11-20T09:49:34Z",
            "head_sha": "7ea85c24a62e65a9384a0abe9eb541ca54064cbc",
            "previous_attempt_url": "https://api.github.com/repos/dymzz/engineering-ci/actions/runs/44713410255/attempts/1",
            "repository": {"html_url": "https://github.com/dymzz/engineering-ci"},
        },
        "job_pages": [{"total_count": 4, "jobs": all_jobs}],
    }
    res = cicd_telemetry.reconcile_attempt_witness_capture(witness, cap)
    assert res["ok"] is True
    assert res["result_completed_jobs"] == [
        "136212522105",
        "136212523496",
        "136212524904",
        "136212602878",
    ]
    assert [row["job_id"] for row in res["result_inventory"]] == [
        "136212522105",
        "136212523496",
        "136212524904",
        "136212602878",
    ]
    assert res["executed_jobs"] == ["136212522105"]
    assert res["reused_jobs"] == [
        "136212523496",
        "136212524904",
        "136212602878",
    ]

    without_start = copy.deepcopy(witness)
    without_start["attempt"].pop("run_started_at")
    unknown_res = cicd_telemetry.reconcile_attempt_witness_capture(without_start, cap)
    assert unknown_res["ok"] is False
    assert "execution_attribution_unknown" in unknown_res["errors"]
    assert unknown_res["task_coverage"] == "execution_attribution_unknown"

    cap_wrong_reuse = _normalized_span_capture(
        run_id=44713410255,
        attempt=2,
        run_conclusion="success",
        jobs=[exec_job, reused_jobs[0]],
    )
    bad = cicd_telemetry.reconcile_attempt_witness_capture(witness, cap_wrong_reuse)
    assert bad["ok"] is False
    assert "wrong_reuse_attribution" in bad["errors"]


def test_issue52_gate_chain_rejects_exit0_when_terminal_correlation_fails(tmp_path):
    """P1-D: terminal-live-check exit 0 + failed correlation/gate cannot pass #66-C/#63/#61."""
    jobs = [
        _sample_job(
            job_id=8401, run_id=7006, attempt=1, name="lint", conclusion="success"
        )["workflow_job"]
    ]
    cap = _normalized_span_capture(
        run_id=7006, attempt=1, run_conclusion="success", jobs=jobs
    )
    outcome = _run_issue52_gate_chain(
        stage_dir=tmp_path / "corr-fail",
        run_id=7006,
        attempt=1,
        run_conclusion="success",
        jobs=jobs,
        capture=cap,
        terminal_status="passed",
        terminal_exit_code=0,
        gate_status="failed",
        gate_exit_code=1,
        terminal_correlation_status="failed",
        terminal_correlation_reason="weaver_terminal_correlation_failed",
    )
    bound_term = next(
        e
        for e in outcome["receipt"]["entries"]
        if e["expected_check_ref"] == outcome["term_entry_ref"]
    )
    assert bound_term["binding_status"] == "insufficient"
    assert bound_term["reason"] == "terminal_correlation_unproven"
    assert bound_term["native_satisfaction"]["status"] == "unsatisfied"
    assert bound_term["selected_result_ids"] == []
    admitted_term = next(
        e
        for e in outcome["admission"]["entries"]
        if e["expected_check_ref"] == outcome["term_entry_ref"]
    )
    assert admitted_term["status"] == "unsatisfied"
    assert admitted_term["obligation_status"] == "unsatisfied"
    assert outcome["admission"]["status"] != "admitted"
    assert outcome["admission"]["summary"]["admitted"] == 0
    assert outcome["post_assessment"]["status"] == "blocked"
    assert outcome["post_stage"]["status"] == "rejected"


def test_issue52_negative_wrong_attempt_missing_root_and_scope_mismatch(tmp_path):
    """P1-D: explicit negative coverage for wrong Attempt, Missing Root, and Scope mismatch."""
    jobs = [
        _sample_job(
            job_id=8501, run_id=7007, attempt=2, name="tests", conclusion="success"
        )["workflow_job"]
    ]
    wrong_cap = _normalized_span_capture(
        run_id=7007, attempt=1, run_conclusion="success", jobs=jobs
    )
    witness = {
        "attempt": {
            "id": 7007,
            "run_attempt": 2,
            "status": "completed",
            "conclusion": "success",
            "run_started_at": "2026-11-10T10:00:01Z",
            "head_sha": "7ea85c24a62e65a9384a0abe9eb541ca54064cbc",
            "previous_attempt_url": "https://api.github.com/repos/dymzz/engineering-ci/actions/runs/7007/attempts/1",
            "repository": {"html_url": "https://github.com/dymzz/engineering-ci"},
        },
        "job_pages": [{"total_count": 1, "jobs": jobs}],
    }
    wrong_attempt = cicd_telemetry.reconcile_attempt_witness_capture(witness, wrong_cap)
    assert wrong_attempt["ok"] is False
    assert "root_trace_id_mismatch" in wrong_attempt["errors"]
    assert "root_span_id_mismatch" in wrong_attempt["errors"]

    # Missing Root Span in githubreceiver capture fails #52 witness reconciliation.
    cap_no_root = _normalized_span_capture(
        run_id=7007, attempt=2, run_conclusion="success", jobs=jobs
    )
    cap_no_root = copy.deepcopy(cap_no_root)
    for rs in cap_no_root.get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            ss["spans"] = [s for s in ss.get("spans", []) if s.get("parentSpanId")]
    no_root = cicd_telemetry.reconcile_attempt_witness_capture(witness, cap_no_root)
    assert no_root["ok"] is False
    assert "root_span_missing" in no_root["errors"]

    # Scope / Source subject mismatch refuses Terminal Expected Binding.
    repo_root = tmp_path / "repo"
    expected_plan, _ = _prepare_git_expected_plans(repo_root)
    post_expected = expected_plan["expected_checks"]
    term_entry = next(
        item for item in post_expected["checks"] if item["name"] == "weaver_terminal"
    )
    closure_dir = tmp_path / "closure"
    shutil.copytree(WEAVER_FIXTURE_DIR, closure_dir)
    shutil.copy2(WEAVER_MATCHER_TOML, closure_dir / ".weaver.toml")
    (closure_dir / "capture.json").write_text(
        json.dumps(
            _normalized_span_capture(
                run_id=7007, attempt=2, run_conclusion="success", jobs=jobs
            )
        ),
        encoding="utf-8",
    )
    (closure_dir / "expected.json").write_text(
        json.dumps(
            gr.build_weaver_expectations(
                run_id=7007,
                attempt=2,
                run_status="completed",
                run_conclusion="success",
                jobs=jobs,
                run_started_at="2026-11-10T10:00:01Z",
            )
        ),
        encoding="utf-8",
    )
    (closure_dir / "ci-weaver.toml").write_text(
        "\n".join(
            [
                "schema_version = 1",
                'registry = "registry"',
                'registry_manifest = "registry-sha256.json"',
                'matcher_config = ".weaver.toml"',
                f"expected_matchers = {json.dumps(gr.expected_matchers_for_jobs(jobs))}",
                'otlp = "capture.json"',
                'expectations = "expected.json"',
                'advice_policies = "advice-policies"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    plan = selection.post_completion_plan(
        root=closure_dir,
        config_path=closure_dir / "pyproject.toml",
        weaver_config="ci-weaver.toml",
        run_id="7007/2",
        attempt=2,
        result="success",
    )
    sealed = te.seal_phase_witness(
        scope_id=post_expected["scope_id"],
        expected_set_ref=post_expected["expected_set_ref"],
        phase=check_contract.POST_COMPLETION_PHASE,
        profile_ref=PROFILE_REF,
        profile_revision=PROFILE_REVISION,
        issuer_ref=PHASE_WITNESS_ISSUER,
        validated_at=EVALUATED_AT,
        domain_complete=True,
    )
    bad_binding = te.bind_terminal_expected(
        post_expected,
        plan=plan,
        completed_run={
            "run_id": "7007/2",
            "attempt": 2,
            "result": "success",
            "status": "completed",
            "source": "github_attempt_api",
            "subject": {
                "declared_target_tree": "0" * 40,
                "head_commit": term_entry["event_ref"].get("head_commit"),
                "base_commit": term_entry["event_ref"].get("base_commit"),
                "head_ref": term_entry["event_ref"].get("head_ref"),
                "base_ref": term_entry["event_ref"].get("base_ref"),
                "expected_check_ref": term_entry["expected_check_ref"],
                "scope_id": post_expected["scope_id"],
            },
        },
        root=closure_dir,
        expected_authority=lambda _ctx: {
            "accepted": True,
            "status": "authorized",
            "authority_ref": AUTHORITY_REF,
        },
        completed_run_authority=lambda _ctx: {
            "accepted": True,
            "status": "authorized",
            "subject_verified": True,
            "authority_ref": AUTHORITY_REF,
        },
        phase_witness=sealed,
        phase_witness_authority=lambda _ctx: {
            "accepted": True,
            "status": "authorized",
            "authority_ref": AUTHORITY_REF,
        },
        projection_git=expected_plan["git"],
        expected_plan=expected_plan,
        expected_root=repo_root,
    )
    assert bad_binding["authorized"] is False
    assert "terminal_subject_declared_target_tree_mismatch" in bad_binding["reasons"]
