"""End-to-end #86 -> #66 Producer Artifact Handoff and independent Source Resolution tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest
from test_reconciliation import make_observation, make_routing
from test_workflow_security_checks import (
    CLEAN_ZIZMOR_WORKFLOW,
    _build_plan_and_expected,
    _git,
    _init_repo,
)

from engineering_ci import execution, hygiene, reconciliation, selection

GENERATED_WORKFLOW_YML = """\
name: Generated Workflow
on:
  push:
    branches:
      - main
permissions:
  contents: read
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - name: Echo ok
        run: echo ok
"""


def _artifact_authority(
    plan,
    bundle_dir,
    allowed_checks,
    *,
    expected_run_ref=None,
    expected_attempt_ref=None,
):
    plan_ref = execution._canonical_sha256(plan)
    expected_event = execution._plan_event_identity(plan)
    expected_candidate = execution._plan_candidate_identity(plan)
    resolved_bundle = Path(bundle_dir).resolve()

    def authorize(request):
        if request["kind"] == "producer_artifact":
            src = request["source"]
            ident = src.get("execution_identity") or {}
            if (
                expected_run_ref is not None
                and ident.get("run_ref") != expected_run_ref
            ):
                return False
            if (
                expected_attempt_ref is not None
                and ident.get("attempt_ref") != expected_attempt_ref
            ):
                return False
            return (
                request["plan_ref"] == plan_ref
                and request["event"] == expected_event
                and request["check"] in allowed_checks
                and src.get("candidate") == expected_candidate
                and Path(src["resolved_bundle_root"])
                .resolve()
                .is_relative_to(resolved_bundle)
            )
        return False

    return authorize


def _reconcile_one(
    plan,
    name,
    native,
    resolution,
    *,
    expected_entry=None,
    run_ref=None,
    attempt_ref=None,
):
    expected = plan["expected_checks"]
    entry = expected_entry or next(
        item for item in expected["checks"] if item["name"] == name
    )
    routing_kw = {}
    obs_kw = {}
    if run_ref is not None:
        routing_kw["run_ref"] = run_ref
        obs_kw["run_ref"] = run_ref
    if attempt_ref is not None:
        routing_kw["attempt_ref"] = attempt_ref
        obs_kw["attempt_ref"] = attempt_ref
    receipt = reconciliation.reconcile_expected_results(
        expected,
        routing=[make_routing(entry, **routing_kw)],
        resolutions=[resolution] if resolution is not None else [],
        observations=[make_observation("job-1", [native], **obs_kw)],
    )
    return next(item for item in receipt["entries"] if item["name"] == name)


def _re_sign_handoff(handoff):
    body = {k: v for k, v in handoff.items() if k != "handoff_digest"}
    return {**body, "handoff_digest": execution._canonical_sha256(body)}


def _setup_file_and_dir_producer_repo(
    tmp_path,
    *,
    snapshot="commit",
    shell_findings=False,
    include_generated_workflow=True,
    checks=("actionlint", "shellcheck"),
    extra_workflow_toml="",
):
    gen_file = "scripts/generated.sh"
    gen_file_bytes = (
        b"#!/bin/sh\necho $UNQUOTED\n"
        if shell_findings
        else b"#!/bin/sh\necho generated-file\n"
    )
    gen_file_sha = hashlib.sha256(gen_file_bytes).hexdigest()

    gen_dir = "scripts/generated_dir"
    dir_sub_file = "sub/helper.sh"
    dir_sub_bytes = b"#!/bin/sh\necho generated-dir-helper\n"
    dir_stage = tmp_path / "_stage_dir"
    (dir_stage / "sub").mkdir(parents=True)
    (dir_stage / "empty_subdir").mkdir()
    (dir_stage / dir_sub_file).write_bytes(dir_sub_bytes)
    gen_dir_sha = execution.artifact_directory_id(dir_stage)

    gen_workflow = ".github/workflows/generated.yml"
    gen_workflow_bytes = GENERATED_WORKFLOW_YML.encode("utf-8")
    gen_workflow_sha = hashlib.sha256(gen_workflow_bytes).hexdigest()

    gen_script = tmp_path / "_producer_emit.py"
    payload = {
        gen_file: gen_file_bytes.decode("utf-8"),
        f"{gen_dir}/{dir_sub_file}": dir_sub_bytes.decode("utf-8"),
    }
    if include_generated_workflow and "actionlint" in checks:
        payload[gen_workflow] = GENERATED_WORKFLOW_YML
    gen_script.write_text(
        f"""\
import os
from pathlib import Path

root = Path(os.environ["ENGINEERING_CI_ARTIFACT_DIR"])
payload = {payload!r}
for rel, content in payload.items():
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\\n")
(root / {gen_dir!r} / "empty_subdir").mkdir(parents=True, exist_ok=True)
""",
        encoding="utf-8",
    )

    edges = [
        f"""\
[[dependency_edges]]
producer_ref = {{ kind = "prerequisite_action", name = "generate" }}
consumer_ref = {{ kind = "check", name = "shellcheck" }}
dependency_kind = "data"
requires_pass = true
output_ref = {{ kind = "file", path = "{gen_file}", sha256 = "{gen_file_sha}" }}

[[dependency_edges]]
producer_ref = {{ kind = "prerequisite_action", name = "generate" }}
consumer_ref = {{ kind = "check", name = "shellcheck" }}
dependency_kind = "data"
requires_pass = true
output_ref = {{ kind = "directory", path = "{gen_dir}", sha256 = "{gen_dir_sha}" }}
"""
    ]
    if include_generated_workflow and "actionlint" in checks:
        edges.append(
            f"""\
[[dependency_edges]]
producer_ref = {{ kind = "prerequisite_action", name = "generate" }}
consumer_ref = {{ kind = "check", name = "actionlint" }}
dependency_kind = "data"
requires_pass = true
output_ref = {{ kind = "file", path = "{gen_workflow}", sha256 = "{gen_workflow_sha}" }}
"""
        )

    workflows = (
        {".github/workflows/ci.yml": CLEAN_ZIZMOR_WORKFLOW}
        if "actionlint" in checks
        else {}
    )
    root, _base_sha = _init_repo(
        tmp_path / "repo_wrap",
        checks=checks,
        workflows=workflows,
        shells={"scripts/check.sh": "#!/bin/sh\necho checked\n"},
        workflow_checks_extra=f"""\
{extra_workflow_toml}
[prerequisite_actions.generate]
type = "command"
argv = {json.dumps([sys.executable, str(gen_script)])}

{"".join(edges)}""",
    )
    changed = [p for p in (*workflows.keys(), "scripts/check.sh")]
    if snapshot == "index":
        cfg_path = root / "ci-selection.toml"
        cfg = selection.load_config(cfg_path)
        hyg_path = root / "ci-hygiene.toml"
        hyg_cfg = hygiene.load_config(hyg_path)
        head_tree = _git(root, "rev-parse", "HEAD^{tree}").strip()
        head_commit = _git(root, "rev-parse", "HEAD").strip()
        cs = selection.ChangeSet(
            root=root,
            base_ref="origin/main",
            head_ref="HEAD",
            base_commit=head_commit,
            head_commit=head_commit,
            tree=head_tree,
            index_tree=head_tree,
            paths=tuple(changed),
            worktree_dirty=False,
            complete=True,
            snapshot="index",
        )
        sel = selection.select(cs, cfg, root=root)
        plan = selection.plan(
            sel,
            config=cfg,
            config_path=cfg_path,
            root=root,
            hygiene_config=hyg_cfg,
            hygiene_config_path=hyg_path,
        )
    else:
        plan, _ = _build_plan_and_expected(root, changed, snapshot=snapshot)
    return {
        "root": root,
        "plan": plan,
        "gen_file": gen_file,
        "gen_file_sha": gen_file_sha,
        "gen_dir": gen_dir,
        "gen_dir_sha": gen_dir_sha,
        "gen_workflow": gen_workflow,
        "gen_workflow_sha": gen_workflow_sha,
        "dir_sub_rel": f"{gen_dir}/{dir_sub_file}",
    }


@pytest.mark.parametrize("snapshot", ["commit", "index"])
@pytest.mark.parametrize("shell_findings", [False, True])
def test_generated_file_and_directory_producer_e2e_reconciles_matched(
    tmp_path, snapshot, shell_findings
):
    setup = _setup_file_and_dir_producer_repo(
        tmp_path, snapshot=snapshot, shell_findings=shell_findings
    )
    root, plan = setup["root"], setup["plan"]
    bundle_dir = tmp_path / "retained_bundle"
    report = execution.execute(
        plan,
        root=root,
        produced_artifacts_dir=bundle_dir,
        run_ref="run-86",
        attempt_ref="1",
    )

    expected_exec_status = "failed" if shell_findings else "passed"
    assert report["status"] == expected_exec_status, report
    handoff = report["producer_artifact_handoff"]
    assert handoff["execution_identity"]["run_ref"] == "run-86"
    assert handoff["execution_identity"]["attempt_ref"] == "1"
    assert len(handoff["artifacts"]) == 3

    # Original repo stays clean and retained bundle holds identical bytes after temp cleanup.
    assert not (root / setup["gen_file"]).exists()
    assert not (root / setup["gen_dir"]).exists()
    assert not (root / setup["gen_workflow"]).exists()
    retained_file = (
        bundle_dir / "produced/prerequisite_action/generate" / setup["gen_file"]
    )
    retained_dir = (
        bundle_dir / "produced/prerequisite_action/generate" / setup["gen_dir"]
    )
    assert retained_file.is_file()
    assert (retained_dir / "sub/helper.sh").is_file()
    assert (retained_dir / "empty_subdir").is_dir()

    authority = execution.producer_artifact_authority_from_execution(
        plan,
        report,
        bundle_root=bundle_dir,
        allowed_checks={"shellcheck", "actionlint"},
        expected_run_ref="run-86",
        expected_attempt_ref="1",
    )
    for check_name, expected_native in (
        ("shellcheck", "failed" if shell_findings else "passed"),
        ("actionlint", "passed"),
    ):
        native = next(
            row["native_result"]
            for row in report["checks"]
            if row["name"] == check_name
        )
        assert native["status"] == expected_native
        assert native["source_ref"]["scope_kind"] == "check_input_closure"
        expected_actual = (
            "index_verified_worktree_with_generated_inputs"
            if snapshot == "index"
            else "worktree_with_generated_inputs"
        )
        assert native["source_ref"]["actual_execution_source"] == expected_actual

        resolution = execution.source_resolution_receipt(
            plan,
            check_name,
            root=root,
            producer_artifact_handoff=handoff,
            producer_artifacts_dir=bundle_dir,
            producer_artifact_authority=authority,
            expected_run_ref="run-86",
            expected_attempt_ref="1",
        )
        assert resolution["source_ref"] == native["source_ref"]
        assert resolution["producer_artifact_handoff_id"] == handoff["handoff_digest"]

        reconciled = _reconcile_one(
            plan,
            check_name,
            native,
            resolution,
            run_ref="run-86",
            attempt_ref="1",
        )
        assert reconciled["resolved_expected_ref"] is not None, reconciled
        if expected_native == "passed":
            assert reconciled["binding_status"] == "matched", reconciled
        else:
            assert reconciled["binding_status"] == "insufficient", reconciled
            assert reconciled["reason"] == "result_not_passed", reconciled
        assert reconciled["native_status"] == expected_native


def test_generated_directory_materialized_gitfree_source_reaches_matched(tmp_path):
    setup = _setup_file_and_dir_producer_repo(
        tmp_path,
        snapshot="commit",
        shell_findings=False,
        include_generated_workflow=False,
        checks=("shellcheck",),
    )
    repo = setup["root"]
    plan, _ = _build_plan_and_expected(
        repo, ["scripts/check.sh"], snapshot="commit", with_baseline=False
    )
    mat_dir = tmp_path / "materialized"
    shutil.copytree(repo, mat_dir, ignore=shutil.ignore_patterns(".git"))

    plan_gitfree = execution._rebased_consumer_plan(plan, repo)
    plan_gitfree = {
        **plan_gitfree,
        "git": {
            **plan_gitfree["git"],
            "source_tree_id": selection.directory_content_id(mat_dir),
        },
    }
    plan_ref = execution._canonical_sha256(plan_gitfree)
    event = execution._plan_event_identity(plan_gitfree)
    candidate = execution._plan_candidate_identity(plan_gitfree)
    bundle_dir = tmp_path / "retained_bundle"

    def unified_authority(request):
        if request["kind"] == "tree_materialization":
            return (
                request["plan_ref"] == plan_ref
                and request["event"] == event
                and request["check"] == "shellcheck"
                and request["source"]
                == {
                    "tree": plan_gitfree["git"]["execution_tree"],
                    "source_tree_id": plan_gitfree["git"]["source_tree_id"],
                }
            )
        if request["kind"] == "producer_artifact":
            src = request["source"]
            return (
                request["plan_ref"] == plan_ref
                and request["event"] == event
                and request["check"] == "shellcheck"
                and src.get("candidate") == candidate
            )
        return False

    report = execution.execute(
        plan_gitfree,
        root=mat_dir,
        workflow_source_authority=unified_authority,
        produced_artifacts_dir=bundle_dir,
    )
    shell_entry = next(row for row in report["checks"] if row["name"] == "shellcheck")
    assert shell_entry["status"] == "passed", shell_entry
    native = shell_entry["native_result"]
    assert (
        native["source_ref"]["actual_execution_source"]
        == "materialized_directory_with_generated_inputs"
    )
    assert native["source_ref"]["scope_kind"] == "check_input_closure"

    resolution = execution.build_source_resolution(
        plan_gitfree,
        "shellcheck",
        root=mat_dir,
        workflow_source_authority=unified_authority,
        producer_artifact_handoff=report["producer_artifact_handoff"],
        producer_artifacts_dir=bundle_dir,
    )
    assert resolution["source_ref"] == native["source_ref"]
    reconciled = _reconcile_one(plan_gitfree, "shellcheck", native, resolution)
    assert reconciled["binding_status"] == "matched", reconciled
    assert reconciled["native_status"] == "passed"


def test_open_worktree_generated_inputs_preserve_insufficient_scope(tmp_path):
    setup = _setup_file_and_dir_producer_repo(
        tmp_path,
        snapshot="worktree",
        shell_findings=False,
        include_generated_workflow=False,
        checks=("shellcheck",),
    )
    root, plan = setup["root"], setup["plan"]
    bundle_dir = tmp_path / "retained_bundle"
    report = execution.execute(plan, root=root, produced_artifacts_dir=bundle_dir)
    assert report["status"] == "passed", [
        (c.get("name"), c.get("status"), c.get("reason"), c.get("stderr"))
        for c in report["checks"]
    ]
    native = next(
        row["native_result"] for row in report["checks"] if row["name"] == "shellcheck"
    )
    assert native["source_ref"]["scope_kind"] == "changed_paths"

    authority = _artifact_authority(plan, bundle_dir, {"shellcheck"})
    resolution = execution.build_source_resolution(
        plan,
        "shellcheck",
        root=root,
        producer_artifact_handoff=report["producer_artifact_handoff"],
        producer_artifacts_dir=bundle_dir,
        producer_artifact_authority=authority,
    )
    assert resolution["source_ref"] == native["source_ref"]
    reconciled = _reconcile_one(plan, "shellcheck", native, resolution)
    assert reconciled["binding_status"] == "unknown"
    assert reconciled["reason"] == "expected_source_scope_insufficient"
    assert reconciled["native_status"] == "passed"


def test_closed_worktree_hygiene_with_generated_module_reaches_matched(tmp_path):
    gen_py = "src/pkg/generated_mod.py"
    gen_bytes = b"GENERATED = 42\n"
    gen_sha = hashlib.sha256(gen_bytes).hexdigest()
    writer = (
        "import sys; from pathlib import Path; "
        "p = Path(sys.argv[1]) / "
        f"{gen_py!r}; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes({gen_bytes!r})"
    )
    root = tmp_path / "repo"
    (root / "src/pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (root / "tach.toml").write_text(
        'source_roots = ["src"]\nexact = true\n', encoding="utf-8"
    )
    (root / "ci-selection.toml").write_text(
        f"""\
schema_version = 1
tach = "tach.toml"
tests_dir = "tests"
src_root = "src"
framework = "unittest"
checks = ["tach"]

[prerequisite_actions.gen_py]
type = "command"
argv = {json.dumps([sys.executable, "-c", writer, "{artifact_dir}"])}

[[dependency_edges]]
producer_ref = {{ kind = "prerequisite_action", name = "gen_py" }}
consumer_ref = {{ kind = "check", name = "tach" }}
dependency_kind = "data"
requires_pass = true
output_ref = {{ kind = "file", path = "{gen_py}", sha256 = "{gen_sha}" }}
""",
        encoding="utf-8",
    )
    (root / "ci-hygiene.toml").write_text(
        'schema_version = 1\nchecks = ["tach"]\n[tach]\npath = "tach.toml"\nexternal = false\n',
        encoding="utf-8",
    )
    (root / "src/pkg/__init__.py").write_text("", encoding="utf-8")
    (root / "src/pkg/mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "tach@example.invalid")
    _git(root, "config", "user.name", "Tach Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    base = _git(root, "rev-parse", "HEAD").strip()

    cfg = selection.load_config(root / "ci-selection.toml")
    hyg = hygiene.load_config(root / "ci-hygiene.toml")
    sel = selection.select(
        selection.ChangeSet(
            root=root,
            base_ref="origin/main",
            head_ref="HEAD",
            base_commit=base,
            head_commit=base,
            tree=_git(root, "rev-parse", "HEAD^{tree}").strip(),
            paths=("src/pkg/mod.py",),
            worktree_dirty=False,
            complete=True,
            snapshot="worktree",
        ),
        cfg,
        root=root,
    )
    plan = selection.plan(
        sel,
        config=cfg,
        config_path=root / "ci-selection.toml",
        hygiene_config=hyg,
        hygiene_config_path=root / "ci-hygiene.toml",
        root=root,
    )

    def runner(argv, cwd, data=None):
        if any("tach" in str(part) for part in (argv or ())):
            assert (Path(cwd) / gen_py).is_file()
            return type(
                "R", (), {"returncode": 0, "stdout": "tach ok\n", "stderr": ""}
            )()
        import subprocess

        return subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, check=False
        )

    bundle_dir = tmp_path / "retained_tach"
    report = execution.execute(
        plan,
        root=root,
        runner=runner,
        hygiene_config=root / "ci-hygiene.toml",
        produced_artifacts_dir=bundle_dir,
    )
    assert report["status"] == "passed", [
        (c.get("name"), c.get("status"), c.get("reason"), c.get("stderr"))
        for c in report["checks"]
    ]
    native = next(
        row["native_result"] for row in report["checks"] if row["name"] == "tach"
    )
    assert native["source_ref"]["scope_kind"] == "check_input_closure"
    authority = _artifact_authority(plan, bundle_dir, {"tach"})
    resolution = execution.build_source_resolution(
        plan,
        "tach",
        root=root,
        hygiene_config=root / "ci-hygiene.toml",
        producer_artifact_handoff=report["producer_artifact_handoff"],
        producer_artifacts_dir=bundle_dir,
        producer_artifact_authority=authority,
    )
    assert resolution["source_ref"] == native["source_ref"]
    reconciled = _reconcile_one(plan, "tach", native, resolution)
    assert reconciled["binding_status"] == "matched", reconciled


@pytest.mark.parametrize(
    ("mutation", "expected_res_status", "expected_bind_status", "expected_reason"),
    [
        (
            "none_handoff",
            "unverified",
            "unknown",
            "source_resolution_generated_inputs_unproven",
        ),
        (
            "native_self_report_only",
            "unverified",
            "unknown",
            "source_resolution_generated_inputs_unproven",
        ),
        (
            "no_authority",
            "unverified",
            "unknown",
            "producer_artifact_authority_unverified",
        ),
        (
            "denied_authority",
            "unverified",
            "unknown",
            "producer_artifact_authority_unverified",
        ),
        ("missing_retained_file", "unverified", "unknown", "producer_artifact_missing"),
        ("missing_retained_dir", "unverified", "unknown", "producer_artifact_missing"),
        (
            "missing_attempt_when_required",
            "unverified",
            "unknown",
            "producer_handoff_attempt_unverified",
        ),
        ("expired_retention", "unverified", "unknown", "producer_artifact_expired"),
        (
            "missing_edge_in_handoff",
            "unverified",
            "unknown",
            "producer_handoff_missing",
        ),
        (
            "tamper_file_bytes",
            "contradicted",
            "insufficient",
            "producer_artifact_digest_mismatch",
        ),
        (
            "tamper_dir_file_bytes",
            "contradicted",
            "insufficient",
            "producer_artifact_digest_mismatch",
        ),
        (
            "truncate_dir_remove_file",
            "contradicted",
            "insufficient",
            "producer_directory_truncated",
        ),
        (
            "truncate_dir_remove_empty_subdir",
            "contradicted",
            "insufficient",
            "producer_directory_truncated",
        ),
        (
            "inject_extra_dir_file",
            "contradicted",
            "insufficient",
            "producer_artifact_digest_mismatch",
        ),
        (
            "swap_file_with_directory",
            "contradicted",
            "insufficient",
            "producer_artifact_type_mismatch",
        ),
        (
            "wrong_producer",
            "contradicted",
            "insufficient",
            "producer_handoff_producer_mismatch",
        ),
        (
            "wrong_consumer",
            "contradicted",
            "insufficient",
            "producer_handoff_consumer_mismatch",
        ),
        (
            "wrong_output_ref",
            "contradicted",
            "insufficient",
            "producer_handoff_output_ref_mismatch",
        ),
        (
            "wrong_plan",
            "contradicted",
            "insufficient",
            "producer_handoff_plan_mismatch",
        ),
        (
            "wrong_event",
            "contradicted",
            "insufficient",
            "producer_handoff_event_mismatch",
        ),
        (
            "wrong_candidate",
            "contradicted",
            "insufficient",
            "producer_handoff_candidate_mismatch",
        ),
        (
            "wrong_attempt",
            "contradicted",
            "insufficient",
            "producer_handoff_attempt_mismatch",
        ),
        (
            "tampered_handoff_digest",
            "contradicted",
            "insufficient",
            "producer_handoff_digest_mismatch",
        ),
        (
            "producer_failed",
            "contradicted",
            "insufficient",
            "producer_execution_failed",
        ),
    ],
)
@pytest.mark.parametrize("shell_findings", [False, True])
def test_negative_producer_handoff_cases_fail_closed_preserving_native_status(
    tmp_path,
    mutation,
    expected_res_status,
    expected_bind_status,
    expected_reason,
    shell_findings,
):
    setup = _setup_file_and_dir_producer_repo(
        tmp_path,
        snapshot="commit",
        shell_findings=shell_findings,
        include_generated_workflow=False,
        checks=("actionlint", "shellcheck"),
    )
    root, plan = setup["root"], setup["plan"]
    bundle_dir = tmp_path / "retained_bundle"
    include_run_attempt = mutation != "missing_attempt_when_required"
    report = execution.execute(
        plan,
        root=root,
        produced_artifacts_dir=bundle_dir,
        **({"run_ref": "run-86", "attempt_ref": "1"} if include_run_attempt else {}),
    )
    native = next(
        row["native_result"] for row in report["checks"] if row["name"] == "shellcheck"
    )
    expected_native_status = "failed" if shell_findings else "passed"
    assert native["status"] == expected_native_status

    handoff = report["producer_artifact_handoff"]
    auth = _artifact_authority(plan, bundle_dir, {"shellcheck", "actionlint"})
    target_plan = plan
    exp_run = "run-86"
    exp_attempt = "2" if mutation == "wrong_attempt" else "1"
    prod_base = bundle_dir / "produced/prerequisite_action/generate"

    if mutation == "none_handoff":
        handoff = None
    elif mutation == "native_self_report_only":
        handoff = None
        bundle_dir = None
    elif mutation == "no_authority":
        auth = None
    elif mutation == "denied_authority":
        auth = lambda req: False
    elif mutation == "missing_retained_file":
        (prod_base / setup["gen_file"]).unlink()
    elif mutation == "missing_retained_dir":
        shutil.rmtree(prod_base / setup["gen_dir"])
    elif mutation == "expired_retention":
        mod = json.loads(json.dumps(handoff))
        mod["artifacts"][0]["retention"] = {"status": "expired", "valid": False}
        handoff = _re_sign_handoff(mod)
    elif mutation == "missing_edge_in_handoff":
        mod = json.loads(json.dumps(handoff))
        mod["artifacts"] = []
        handoff = _re_sign_handoff(mod)
    elif mutation == "tamper_file_bytes":
        (prod_base / setup["gen_file"]).write_bytes(b"#!/bin/sh\necho tampered\n")
    elif mutation == "tamper_dir_file_bytes":
        (prod_base / setup["dir_sub_rel"]).write_bytes(b"#!/bin/sh\necho tampered\n")
    elif mutation == "truncate_dir_remove_file":
        (prod_base / setup["dir_sub_rel"]).unlink()
    elif mutation == "truncate_dir_remove_empty_subdir":
        (prod_base / setup["gen_dir"] / "empty_subdir").rmdir()
    elif mutation == "inject_extra_dir_file":
        (prod_base / setup["gen_dir"] / "extra.sh").write_bytes(b"#!/bin/sh\n")
    elif mutation == "swap_file_with_directory":
        rf = prod_base / setup["gen_file"]
        rf.unlink()
        rf.mkdir()
    elif mutation == "wrong_producer":
        mod = json.loads(json.dumps(handoff))
        mod["artifacts"][0]["producer_ref"] = {
            "kind": "prerequisite_action",
            "name": "other_producer",
        }
        handoff = _re_sign_handoff(mod)
    elif mutation == "wrong_consumer":
        mod = json.loads(json.dumps(handoff))
        mod["artifacts"][0]["consumer_ref"] = {"kind": "check", "name": "actionlint"}
        handoff = _re_sign_handoff(mod)
    elif mutation == "wrong_output_ref":
        mod = json.loads(json.dumps(handoff))
        mod["artifacts"][0]["output_ref"] = {
            **mod["artifacts"][0]["output_ref"],
            "sha256": "f" * 64,
        }
        handoff = _re_sign_handoff(mod)
    elif mutation == "wrong_plan":
        mod = json.loads(json.dumps(handoff))
        mod["plan_ref"] = "sha256:" + ("0" * 64)
        handoff = _re_sign_handoff(mod)
    elif mutation == "wrong_event":
        mod = json.loads(json.dumps(handoff))
        mod["event"] = {**mod["event"], "head_commit": "0" * 40}
        handoff = _re_sign_handoff(mod)
    elif mutation == "wrong_candidate":
        mod = json.loads(json.dumps(handoff))
        mod["candidate"] = {**mod["candidate"], "content_binding_digest": "0" * 64}
        handoff = _re_sign_handoff(mod)
    elif mutation == "tampered_handoff_digest":
        mod = json.loads(json.dumps(handoff))
        mod["artifacts"][0]["sha256"] = "e" * 64
        handoff = mod
    elif mutation == "producer_failed":
        mod = json.loads(json.dumps(handoff))
        mod["artifacts"][0]["producer_execution"]["status"] = "failed"
        handoff = _re_sign_handoff(mod)

    evaluation = execution.evaluate_source_resolution(
        target_plan,
        "shellcheck",
        root=root,
        producer_artifact_handoff=handoff,
        producer_artifacts_dir=bundle_dir,
        producer_artifact_authority=auth,
        expected_run_ref=exp_run,
        expected_attempt_ref=exp_attempt,
    )
    assert evaluation["resolution_status"] == expected_res_status, evaluation
    assert evaluation["reason"] == expected_reason

    reconciled = _reconcile_one(plan, "shellcheck", native, evaluation)
    assert reconciled["binding_status"] == expected_bind_status, reconciled
    assert reconciled["reason"] == expected_reason
    assert reconciled["native_status"] == expected_native_status

    # Adjacent non-Data-Edge check (`actionlint`) remains completely unaffected & matched.
    al_native = next(
        row["native_result"] for row in report["checks"] if row["name"] == "actionlint"
    )
    al_resolution = execution.evaluate_source_resolution(
        plan,
        "actionlint",
        root=root,
        producer_artifact_handoff=handoff,
        producer_artifacts_dir=bundle_dir,
        producer_artifact_authority=auth,
    )
    assert "resolution_status" not in al_resolution
    al_reconciled = _reconcile_one(plan, "actionlint", al_native, al_resolution)
    assert al_reconciled["binding_status"] == "matched"
    assert al_reconciled["native_status"] == "passed"


def test_real_failed_producer_does_not_taint_adjacent_non_data_check(tmp_path):
    gen_file = "scripts/generated.sh"
    writer_fail = "import sys; sys.exit(2)"
    root, _base_sha = _init_repo(
        tmp_path,
        checks=("actionlint", "shellcheck"),
        workflows={".github/workflows/ci.yml": CLEAN_ZIZMOR_WORKFLOW},
        shells={"scripts/check.sh": "#!/bin/sh\necho ok\n"},
        workflow_checks_extra=f"""\
[prerequisite_actions.generate]
type = "command"
argv = {json.dumps([sys.executable, "-c", writer_fail])}

[[dependency_edges]]
producer_ref = {{ kind = "prerequisite_action", name = "generate" }}
consumer_ref = {{ kind = "check", name = "shellcheck" }}
dependency_kind = "data"
requires_pass = true
output_ref = {{ kind = "file", path = "{gen_file}", sha256 = "{"0" * 64}" }}
""",
    )
    plan, _ = _build_plan_and_expected(
        root, [".github/workflows/ci.yml", "scripts/check.sh"], snapshot="commit"
    )
    bundle_dir = tmp_path / "retained_bundle"
    report = execution.execute(plan, root=root, produced_artifacts_dir=bundle_dir)
    assert report["status"] == "failed"
    sc_row = next(row for row in report["checks"] if row["name"] == "shellcheck")
    assert sc_row["status"] == "not_executed"
    assert sc_row["reason"] == "pre_execution_prerequisite_failed"
    al_native = next(
        row["native_result"] for row in report["checks"] if row["name"] == "actionlint"
    )
    assert al_native["status"] == "passed"

    eval_sc = execution.evaluate_source_resolution(
        plan,
        "shellcheck",
        root=root,
        producer_artifact_handoff=report["producer_artifact_handoff"],
        producer_artifacts_dir=bundle_dir,
        producer_artifact_authority=lambda req: False,
    )
    assert eval_sc["resolution_status"] == "contradicted"
    assert eval_sc["reason"] == "producer_execution_failed"

    al_res = execution.build_source_resolution(plan, "actionlint", root=root)
    al_rec = _reconcile_one(plan, "actionlint", al_native, al_res)
    assert al_rec["binding_status"] == "matched"
    assert al_rec["native_status"] == "passed"


def test_p1_producer_execution_inconsistent_and_rehashed_claim_cannot_mint_authority(
    tmp_path,
):
    setup = _setup_file_and_dir_producer_repo(
        tmp_path,
        snapshot="commit",
        shell_findings=False,
        include_generated_workflow=False,
        checks=("shellcheck",),
    )
    root, plan = setup["root"], setup["plan"]
    bundle_dir = tmp_path / "retained_bundle"
    report = execution.execute(plan, root=root, produced_artifacts_dir=bundle_dir)
    assert report["status"] == "passed", report
    native = next(
        row["native_result"] for row in report["checks"] if row["name"] == "shellcheck"
    )
    handoff = report["producer_artifact_handoff"]

    # Legitimate real successful Producer + execution-grounded authority reaches matched.
    grounded_auth = execution.producer_artifact_authority_from_execution(
        plan, report, bundle_root=bundle_dir, allowed_checks={"shellcheck"}
    )
    ok_res = execution.source_resolution_receipt(
        plan,
        "shellcheck",
        root=root,
        producer_artifact_handoff=handoff,
        producer_artifacts_dir=bundle_dir,
        producer_artifact_authority=grounded_auth,
    )
    ok_rec = _reconcile_one(plan, "shellcheck", native, ok_res)
    assert ok_rec["binding_status"] == "matched", ok_rec
    assert ok_rec["native_status"] == "passed"

    # Directed negative 1: unchanged correct retained bytes + rehashed Handoff claiming
    # status='passed' with nonzero native process exit_code=1 MUST fail closed even if
    # an overly broad authority callback returns True.
    for bad_fields in (
        {"exit_code": 1},
        {"exit_code": None},
        {"exit_code": True},
        {"type": "git_commit_present"},
        {"reason": "prerequisite_command_failed"},
    ):
        forged = json.loads(json.dumps(handoff))
        for item in forged["artifacts"]:
            item["producer_execution"].update(bad_fields)
        for item in forged["producer_executions"]:
            item.update(bad_fields)
        forged = _re_sign_handoff(forged)

        ev = execution.evaluate_source_resolution(
            plan,
            "shellcheck",
            root=root,
            producer_artifact_handoff=forged,
            producer_artifacts_dir=bundle_dir,
            producer_artifact_authority=lambda req: True,
        )
        assert ev["resolution_status"] == "contradicted", (bad_fields, ev)
        assert ev["reason"] == "producer_execution_inconsistent", (bad_fields, ev)
        rec = _reconcile_one(plan, "shellcheck", native, ev)
        assert rec["binding_status"] == "insufficient", (bad_fields, rec)
        assert rec["reason"] == "producer_execution_inconsistent"
        assert rec["native_status"] == "passed"

    # Directed negative 2: rehashing a modified internally-consistent producer claim
    # (exit_code=0, altered prerequisite_result_sha256 / closure_sha256) cannot mint
    # original execution authority.
    for tamper in ("prereq_sha", "closure_sha"):
        rehashed = json.loads(json.dumps(handoff))
        if tamper == "prereq_sha":
            fake_sha = "sha256:" + ("a" * 64)
            for item in rehashed["artifacts"]:
                item["producer_execution"]["prerequisite_result_sha256"] = fake_sha
            for item in rehashed["producer_executions"]:
                item["prerequisite_result_sha256"] = fake_sha
        else:
            for item in rehashed["artifacts"]:
                item["closure_sha256"] = "sha256:" + ("b" * 64)
        rehashed = _re_sign_handoff(rehashed)

        ev = execution.evaluate_source_resolution(
            plan,
            "shellcheck",
            root=root,
            producer_artifact_handoff=rehashed,
            producer_artifacts_dir=bundle_dir,
            producer_artifact_authority=grounded_auth,
        )
        assert ev["resolution_status"] == "unverified", (tamper, ev)
        assert ev["reason"] == "producer_artifact_authority_unverified", (tamper, ev)
        rec = _reconcile_one(plan, "shellcheck", native, ev)
        assert rec["binding_status"] == "unknown", (tamper, rec)
        assert rec["reason"] == "producer_artifact_authority_unverified"
        assert rec["native_status"] == "passed"


def test_p1_bound_run_attempt_rejects_omission_and_cross_attempt_while_preserving_local_and_same_attempt(
    tmp_path,
):
    setup = _setup_file_and_dir_producer_repo(
        tmp_path,
        snapshot="commit",
        shell_findings=False,
        include_generated_workflow=False,
        checks=("shellcheck",),
    )
    root, plan = setup["root"], setup["plan"]

    # 1. Identity-free local execution positive still works when no Run/Attempt exists.
    bundle_local = tmp_path / "bundle_local"
    rep_local = execution.execute(plan, root=root, produced_artifacts_dir=bundle_local)
    assert rep_local["status"] == "passed", rep_local
    native_local = next(
        row["native_result"]
        for row in rep_local["checks"]
        if row["name"] == "shellcheck"
    )
    auth_local = execution.producer_artifact_authority_from_execution(
        plan, rep_local, bundle_root=bundle_local, allowed_checks={"shellcheck"}
    )
    res_local = execution.build_source_resolution(
        plan,
        "shellcheck",
        root=root,
        producer_artifact_handoff=rep_local["producer_artifact_handoff"],
        producer_artifacts_dir=bundle_local,
        producer_artifact_authority=auth_local,
    )
    rec_local = _reconcile_one(plan, "shellcheck", native_local, res_local)
    assert rec_local["binding_status"] == "matched", rec_local

    # 2. Bound consuming Attempt must reject an identity-free local Handoff.
    ev_bound_on_local = execution.evaluate_source_resolution(
        plan,
        "shellcheck",
        root=root,
        producer_artifact_handoff=rep_local["producer_artifact_handoff"],
        producer_artifacts_dir=bundle_local,
        producer_artifact_authority=auth_local,
        execution_context_identity={"run_ref": "run-99", "attempt_ref": "2"},
    )
    assert ev_bound_on_local["resolution_status"] == "unverified", ev_bound_on_local
    assert ev_bound_on_local["reason"] == "producer_handoff_attempt_unverified"

    # 3. Same-Attempt positive via trusted execution_context_identity reaches matched.
    bundle_a1 = tmp_path / "bundle_a1"
    rep_a1 = execution.execute(
        plan,
        root=root,
        produced_artifacts_dir=bundle_a1,
        run_ref="run-99",
        attempt_ref="1",
    )
    assert rep_a1["status"] == "passed", rep_a1
    native_a1 = next(
        row["native_result"] for row in rep_a1["checks"] if row["name"] == "shellcheck"
    )
    handoff_a1 = rep_a1["producer_artifact_handoff"]
    auth_a1 = execution.producer_artifact_authority_from_execution(
        plan,
        rep_a1,
        bundle_root=bundle_a1,
        allowed_checks={"shellcheck"},
        expected_run_ref="run-99",
        expected_attempt_ref="1",
    )
    res_a1 = execution.build_source_resolution(
        plan,
        "shellcheck",
        root=root,
        producer_artifact_handoff=handoff_a1,
        producer_artifacts_dir=bundle_a1,
        producer_artifact_authority=auth_a1,
        execution_context_identity={"run_ref": "run-99", "attempt_ref": "1"},
    )
    assert res_a1["producer_execution_identity"]["run_ref"] == "run-99"
    assert res_a1["producer_execution_identity"]["attempt_ref"] == "1"
    rec_same = _reconcile_one(
        plan,
        "shellcheck",
        native_a1,
        res_a1,
        run_ref="run-99",
        attempt_ref="1",
    )
    assert rec_same["binding_status"] == "matched", rec_same

    # 4. Directed negative: Handoff already carries Run/Attempt (run-99 / attempt 1),
    # caller omits expected_run_ref / expected_attempt_ref -> must NOT silently accept.
    ev_omitted = execution.evaluate_source_resolution(
        plan,
        "shellcheck",
        root=root,
        producer_artifact_handoff=handoff_a1,
        producer_artifacts_dir=bundle_a1,
        producer_artifact_authority=lambda req: True,
    )
    assert ev_omitted["resolution_status"] == "unverified", ev_omitted
    assert ev_omitted["reason"] == "producer_handoff_attempt_unverified"
    rec_omitted = _reconcile_one(
        plan,
        "shellcheck",
        native_a1,
        ev_omitted,
        run_ref="run-99",
        attempt_ref="2",
    )
    assert rec_omitted["binding_status"] == "unknown", rec_omitted
    assert rec_omitted["reason"] == "producer_handoff_attempt_unverified"
    assert rec_omitted["native_status"] == "passed"

    # 5. Directed negative: Attempt-1 verified Resolution reconciled against Attempt-2
    # Job Observation -> #66-C rejects cross-Attempt reuse cleanly.
    rec_cross = _reconcile_one(
        plan,
        "shellcheck",
        native_a1,
        res_a1,
        run_ref="run-99",
        attempt_ref="2",
    )
    assert rec_cross["binding_status"] == "insufficient", rec_cross
    assert rec_cross["reason"] == "producer_handoff_attempt_mismatch"
    assert rec_cross["native_status"] == "passed"
