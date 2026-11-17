"""Contract regressions for #66-P0 pre-execution Source proof & reuse handoff."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from engineering_ci import check_contract, execution, hygiene, plan_contract, selection


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        ["git", *args],
        cwd=root,
        env=merged,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _init_repo(tmp_path: Path, name: str = "repo") -> tuple[Path, str]:
    root = tmp_path / name
    root.mkdir()
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (root / "tach.toml").write_text(
        'source_roots = ["src"]\nexact = true\n',
        encoding="utf-8",
    )
    (root / "ci-selection.toml").write_text(
        'schema_version = 1\ntach = "tach.toml"\ntests_dir = "tests"\n'
        'src_root = "src"\nframework = "unittest"\n'
        'checks = ["hygiene", "tach", "vulture"]\n',
        encoding="utf-8",
    )
    (root / "ci-hygiene.toml").write_text(
        "schema_version = 1\n"
        'checks = ["whitespace", "forwarding", "docs", "vulture", "tach"]\n'
        '[lychee]\narguments = ["--offline"]\n'
        '[tach]\npath = "tach.toml"\nexternal = false\n',
        encoding="utf-8",
    )
    (root / "ci-sources.toml").write_text(
        'schema_version = 1\nacquired_roots = ["data/acquired"]\nmanifest = "sources.json"\n',
        encoding="utf-8",
    )
    (root / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "pkg" / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "docs.md").write_text("# Doc\n", encoding="utf-8")

    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "p0@example.invalid")
    _git(root, "config", "user.name", "P0 Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root, _git(root, "rev-parse", "HEAD").strip()


def _worktree_plan(root: Path, base: str):
    cfg = selection.load_config(root / "ci-selection.toml")
    hyg_cfg = hygiene.load_config(root / "ci-hygiene.toml")
    sel = selection.select(
        selection.changeset(root, base=base, mode="worktree"), cfg, root=root
    )
    return selection.plan(
        sel,
        config=cfg,
        config_path=root / "ci-selection.toml",
        hygiene_config=hyg_cfg,
        hygiene_config_path=root / "ci-hygiene.toml",
        sources_config_path=root / "ci-sources.toml",
        root=root,
        tach_validation={
            "status": "passed",
            "tach_sha256": selection._digest(root / cfg.tach),
            "source_tree": sel.changeset.execution_tree or sel.changeset.tree,
        },
    )


class _HostRunner:
    def __init__(self):
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv, root, data=None):
        self.calls.append(tuple(argv))
        if argv[0] in {"git", sys.executable}:
            return hygiene._run(argv, root, data)
        return subprocess.CompletedProcess(argv, 0, "ok\n", "")


def test_pre_execution_proof_binds_closed_checks_and_detects_gitattributes_and_drift(
    tmp_path,
):
    root, base = _init_repo(tmp_path)
    (root / "src" / "pkg" / "mod.py").write_text("VALUE = 2\n", encoding="utf-8")
    plan = _worktree_plan(root, base)

    ws_proof = execution.build_pre_execution_source_proof(plan, "whitespace", root=root)
    assert ws_proof["phase"] == "pre_execution"
    assert ws_proof["execution_started"] is False
    assert ws_proof["trusted"] is False
    assert ws_proof["required"] is False
    assert ws_proof["status"] == "verified_candidate_inputs"
    assert ws_proof["baseline_ref"]["base_commit"] == base
    assert ws_proof["baseline_source_ref"]["name"] == "git-odb-commit"
    assert ws_proof["rule_ref"]["sha256"].startswith("sha256:")
    ws_src = ws_proof["source_ref"]
    assert ws_src["scope_kind"] == "check_input_closure"
    assert ws_src["binding_status"] == "content_digest"
    assert ws_src["actual_execution_source"] == "planned_host_worktree"
    assert "src/pkg/mod.py" in ws_src["verified_input_scope"]

    # Mutating changed file after Plan causes fail-closed worktree_snapshot_stale:
    (root / "src" / "pkg" / "mod.py").write_text("VALUE = 3\n", encoding="utf-8")
    stale = execution.build_pre_execution_source_proof(plan, "whitespace", root=root)
    assert stale["status"] == "unverified"
    assert stale["reason"] == "worktree_snapshot_stale"
    assert stale["source_ref"] is None

    # Re-plan with .gitattributes committed, then edit .gitattributes -> closure changes:
    (root / ".gitattributes").write_text("*.py text\n", encoding="utf-8")
    _git(root, "add", ".gitattributes")
    _git(root, "commit", "-q", "-m", "add gitattributes")
    base2 = _git(root, "rev-parse", "HEAD").strip()
    (root / "src" / "pkg" / "mod.py").write_text("VALUE = 4\n", encoding="utf-8")
    plan_attr_1 = _worktree_plan(root, base2)
    proof_attr_1 = execution.build_pre_execution_source_proof(
        plan_attr_1, "whitespace", root=root
    )
    assert ".gitattributes" in proof_attr_1["source_ref"]["verified_input_scope"]

    (root / ".gitattributes").write_text("*.py -whitespace\n", encoding="utf-8")
    plan_attr_2 = _worktree_plan(root, base2)
    proof_attr_2 = execution.build_pre_execution_source_proof(
        plan_attr_2, "whitespace", root=root
    )
    assert proof_attr_2["closure_digest"] != proof_attr_1["closure_digest"]
    assert (
        proof_attr_2["source_ref"]["content_digest"]
        != proof_attr_1["source_ref"]["content_digest"]
    )


def test_pre_execution_open_world_external_and_dynamic_checks_stay_unverified(
    tmp_path,
):
    root, base = _init_repo(tmp_path)
    (root / "src" / "pkg" / "mod.py").write_text("VALUE = 5\n", encoding="utf-8")
    plan = _worktree_plan(root, base)

    # Open-world vulture cannot claim closure before execution:
    vul_proof = execution.build_pre_execution_source_proof(plan, "vulture", root=root)
    assert vul_proof["status"] == "unverified"
    assert vul_proof["source_ref"] is None
    assert vul_proof["execution_started"] is False

    # Partial changed_paths PASS from real execution must NEVER be accepted as closure-equivalent:
    report = execution.execute(plan, root=root, runner=_HostRunner())
    vul_nr = next(c for c in report["checks"] if c["name"] == "vulture")[
        "native_result"
    ]
    assert vul_nr["status"] == "passed"
    assert vul_nr["source_ref"]["scope_kind"] == "changed_paths"
    assert not execution.compare_pre_execution_to_historical_result(vul_proof, vul_nr)[
        "equivalent"
    ]

    # Dynamic/external checks: source, tach_external, online docs, dagger_export:
    src_proof = execution.build_pre_execution_source_proof(
        {
            "selection": {"check_scope": [{"name": "source", "status": "selected"}]},
            "git": {"snapshot": "worktree", "changed_paths": ["src/pkg/mod.py"]},
        },
        "source",
        root=root,
    )
    assert src_proof["status"] == "unverified"
    assert src_proof["source_ref"] is None

    dagger_proof = execution.build_pre_execution_source_proof(
        plan, "tach", root=root, execution_context="dagger_export"
    )
    assert dagger_proof["status"] == "unverified"
    assert dagger_proof["reason"] == "dagger_export_pre_execution_unverified"

    (root / "ci-hygiene.toml").write_text(
        "schema_version = 1\n"
        'checks = ["whitespace", "forwarding", "docs", "vulture", "tach", "tach_external"]\n'
        '[tach]\npath = "tach.toml"\nexternal = true\n',
        encoding="utf-8",
    )
    (root / "docs.md").write_text("# Changed Doc\n", encoding="utf-8")
    ext_plan = _worktree_plan(root, base)
    assert (
        execution.build_pre_execution_source_proof(
            ext_plan, "tach_external", root=root
        )["status"]
        == "unverified"
    )
    assert (
        execution.build_pre_execution_source_proof(ext_plan, "docs", root=root)[
            "status"
        ]
        == "unverified"
    )


def test_pre_execution_refuses_index_worktree_divergence_missing_odb_and_config_drift(
    tmp_path,
):
    root, base = _init_repo(tmp_path)
    (root / "src" / "pkg" / "mod.py").write_text("VALUE = 10\n", encoding="utf-8")
    index_b = tmp_path / "index.b"
    env_b = {"GIT_INDEX_FILE": str(index_b)}
    _git(root, "read-tree", "HEAD", env=env_b)
    _git(root, "add", "src/pkg/mod.py", env=env_b)

    cfg = selection.load_config(root / "ci-selection.toml")
    hyg_cfg = hygiene.load_config(root / "ci-hygiene.toml")
    idx_cs = selection.changeset(root, base=base, mode="index", index_file=str(index_b))
    idx_sel = selection.select(idx_cs, cfg, root=root)
    idx_plan = selection.plan(
        idx_sel,
        config=cfg,
        config_path=root / "ci-selection.toml",
        hygiene_config=hyg_cfg,
        hygiene_config_path=root / "ci-hygiene.toml",
        root=root,
        tach_validation={
            "status": "passed",
            "tach_sha256": selection._digest(root / cfg.tach),
            "source_tree": idx_sel.changeset.execution_tree or idx_sel.changeset.tree,
        },
    )

    # Matched Index=B == Worktree=B -> full_input_closure downgraded from byte_tree to content_digest:
    idx_proof = execution.build_pre_execution_source_proof(idx_plan, "tach", root=root)
    assert idx_proof["status"] == "verified_candidate_inputs"
    assert idx_proof["source_ref"]["scope_kind"] == "full_input_closure"
    assert idx_proof["source_ref"]["binding_status"] == "content_digest"
    assert "materialization_ref" not in idx_proof["source_ref"]
    assert idx_proof["source_ref"]["actual_execution_source"] == "planned_host_worktree"

    # Worktree=C diverges from Index=B -> explicit index_worktree_mismatch refusal:
    (root / "src" / "pkg" / "mod.py").write_text(
        "VALUE = 11  # drift C\n", encoding="utf-8"
    )
    drifted_idx = execution.build_pre_execution_source_proof(
        idx_plan, "tach", root=root
    )
    assert drifted_idx["status"] == "unverified"
    assert drifted_idx["reason"] == "index_worktree_mismatch"
    assert drifted_idx["source_ref"] is None

    # Config drift in ci-hygiene.toml after Plan creation -> refuses instead of passing stale config:
    (root / "src" / "pkg" / "mod.py").write_text("VALUE = 10\n", encoding="utf-8")
    wt_cfg_plan = _worktree_plan(root, base)
    (root / "ci-hygiene.toml").write_text(
        'schema_version = 1\nchecks = ["tach"]\n[tach]\npath = "tach.toml"\nexternal = false\n',
        encoding="utf-8",
    )
    cfg_drifted = execution.build_pre_execution_source_proof(
        wt_cfg_plan, "tach", root=root
    )
    assert cfg_drifted["status"] == "unverified"
    assert cfg_drifted["reason"] == "hygiene_config_digest_mismatch"

    # Baseline-requiring Checks refuse missing base_commit or unavailable Git ODB object:
    missing_base = execution.build_pre_execution_source_proof(
        {
            "selection": {
                "check_scope": [{"name": "whitespace", "status": "selected"}]
            },
            "git": {"snapshot": "worktree", "changed_paths": ["src/pkg/mod.py"]},
        },
        "whitespace",
        root=root,
    )
    assert missing_base["status"] == "unverified"
    assert missing_base["reason"] == "pre_execution_baseline_missing"

    missing_odb = execution.build_pre_execution_source_proof(
        {
            "selection": {
                "check_scope": [{"name": "forwarding", "status": "selected"}]
            },
            "git": {
                "snapshot": "worktree",
                "base_commit": "f" * 40,
                "changed_paths": ["src/pkg/mod.py"],
            },
        },
        "forwarding",
        root=root,
    )
    assert missing_odb["status"] == "unverified"
    assert missing_odb["reason"] == "baseline_object_unavailable"


def test_pre_execution_equivalence_enforces_baseline_rule_tool_and_enables_no_rerun(
    tmp_path, monkeypatch
):
    root, base_a = _init_repo(tmp_path)
    # Create second base commit base_b so we can push same target tree with two different baselines:
    (root / "src" / "pkg" / "mod.py").write_text("VALUE = 20\n", encoding="utf-8")
    _git(root, "add", "src/pkg/mod.py")
    _git(root, "commit", "-q", "-m", "second base")
    base_b = _git(root, "rev-parse", "HEAD").strip()

    (root / "src" / "pkg" / "mod.py").write_text("VALUE = 30\n", encoding="utf-8")
    _git(root, "add", "src/pkg/mod.py")
    _git(root, "commit", "-q", "-m", "target commit")
    head = _git(root, "rev-parse", "HEAD").strip()

    cfg = selection.load_config(root / "ci-selection.toml")
    hyg_cfg = hygiene.load_config(root / "ci-hygiene.toml")

    def commit_plan(base_oid):
        cs = selection.changeset(root, base=base_oid, head=head, mode="commit")
        sel = selection.select(cs, cfg, root=root)
        return selection.plan(
            sel,
            config=cfg,
            config_path=root / "ci-selection.toml",
            hygiene_config=hyg_cfg,
            hygiene_config_path=root / "ci-hygiene.toml",
            root=root,
            tach_validation={
                "status": "passed",
                "tach_sha256": selection._digest(root / cfg.tach),
                "source_tree": sel.changeset.execution_tree or sel.changeset.tree,
            },
        )

    plan_from_a = commit_plan(base_a)
    plan_from_b = commit_plan(base_b)
    exec_report = execution.execute(plan_from_a, root=root, runner=_HostRunner())
    hist_ws = next(c for c in exec_report["checks"] if c["name"] == "whitespace")[
        "native_result"
    ]
    hist_tach = next(c for c in exec_report["checks"] if c["name"] == "tach")[
        "native_result"
    ]

    # Current pre-execution proof for same commit + SAME base_a matches that single Native Result's inputs:
    pre_a_ws = execution.build_pre_execution_source_proof(
        plan_from_a, "whitespace", root=root
    )
    eq = execution.compare_pre_execution_to_historical_result(pre_a_ws, hist_ws)
    assert eq["comparison_scope"] == "single_native_result"
    assert eq["equivalent"] is True
    assert eq["single_result_inputs_equivalent"] is True
    assert eq["matched_requirement_id"] == "diff-hygiene"
    assert eq["whole_check_reuse_admitted"] is False
    assert eq["check_satisfaction_evaluated"] is False
    assert eq["reason"] is None
    assert eq["trusted"] is False
    assert eq["admission_status"] == "not_evaluated"

    # Historical Result missing recorded git_whitespace_config cannot prove whitespace config parity:
    legacy_ws = check_contract.native_result(
        check_contract.definition_for("whitespace"),
        source=hist_ws["source_ref"],
        status="passed",
        result_origin=hist_ws["result_origin"],
        result_instance="legacy-ws",
        native_exit_code=0,
        tool=check_contract.tool_ref("git/diff-check"),
        evidence={"stdout": ""},
        rule_ref=hist_ws["check"]["rule_ref"],
        runner_ref=hist_ws["runner_ref"],
        baseline_ref=hist_ws["baseline_ref"],
    )
    assert (
        execution.compare_pre_execution_to_historical_result(pre_a_ws, legacy_ws)[
            "reason"
        ]
        == "whitespace_config_mismatch"
    )

    # Identical source files, HEAD, Baseline and ci-hygiene.toml, changing ONLY effective core.whitespace:
    _git(root, "config", "core.whitespace", "blank-at-eol")
    pre_ws_eol = execution.build_pre_execution_source_proof(
        plan_from_a, "whitespace", root=root
    )
    assert pre_ws_eol["environment_inputs"]["core.whitespace"] == "blank-at-eol"
    assert pre_ws_eol["closure_digest"] != pre_a_ws["closure_digest"]
    assert (
        execution.compare_pre_execution_to_historical_result(pre_ws_eol, hist_ws)[
            "reason"
        ]
        == "whitespace_config_mismatch"
    )
    exec_eol = execution.execute(plan_from_a, root=root, runner=_HostRunner())
    hist_ws_eol = next(c for c in exec_eol["checks"] if c["name"] == "whitespace")[
        "native_result"
    ]
    assert (
        execution.compare_pre_execution_to_historical_result(pre_ws_eol, hist_ws_eol)[
            "equivalent"
        ]
        is True
    )
    _git(root, "config", "core.whitespace", "-blank-at-eol")
    pre_ws_no_eol = execution.build_pre_execution_source_proof(
        plan_from_a, "whitespace", root=root
    )
    assert pre_ws_no_eol["environment_inputs"]["core.whitespace"] == "-blank-at-eol"
    assert (
        execution.compare_pre_execution_to_historical_result(
            pre_ws_no_eol, hist_ws_eol
        )["reason"]
        == "whitespace_config_mismatch"
    )
    _git(root, "config", "--unset", "core.whitespace")

    # Same target Tree (head), DIFFERENT old->new baseline (base_b != base_a) -> REFUSED:
    pre_b_ws = execution.build_pre_execution_source_proof(
        plan_from_b, "whitespace", root=root
    )
    assert pre_b_ws["closure_digest"] != pre_a_ws["closure_digest"]
    diff_base = execution.compare_pre_execution_to_historical_result(pre_b_ws, hist_ws)
    assert diff_base["equivalent"] is False
    assert diff_base["reason"] == "baseline_mismatch"

    # Rule and check identity mismatches before mutating registered tach definition:
    pre_tach = execution.build_pre_execution_source_proof(
        plan_from_a, "tach", root=root
    )
    assert (
        execution.compare_pre_execution_to_historical_result(pre_tach, hist_tach)[
            "equivalent"
        ]
        is True
    )
    assert (
        execution.compare_pre_execution_to_historical_result(pre_tach, hist_ws)[
            "reason"
        ]
        == "check_identity_mismatch"
    )
    altered_rule_proof = {
        **pre_tach,
        "rule_ref": check_contract.rule_ref(
            "engineering-ci.hygiene", 1, digest="0" * 64
        ),
    }
    assert (
        execution.compare_pre_execution_to_historical_result(
            altered_rule_proof, hist_tach
        )["reason"]
        == "rule_mismatch"
    )

    # Tool identity/pin change (e.g. pinned ToolIdentity version changed in current pre-proof) -> REFUSED:
    tach_def = check_contract.definition_for("tach")
    from dataclasses import replace

    both_tach_def = replace(
        tach_def,
        tool_requirements=check_contract.validate_tool_requirements(
            (
                check_contract.ToolRequirement(
                    "tach-check",
                    (check_contract.ToolIdentity("tach/check", version="0.10.0"),),
                ),
            )
        ),
    )
    monkeypatch.setattr(
        check_contract,
        "_CURRENT_BY_ID",
        {**check_contract._CURRENT_BY_ID, tach_def.check_id: both_tach_def},
    )
    monkeypatch.setattr(
        check_contract,
        "_BUILTIN_BY_VERSION",
        {
            **check_contract._BUILTIN_BY_VERSION,
            (tach_def.check_id, tach_def.definition_version): both_tach_def,
        },
    )
    wrong_tool_nr = check_contract.native_result(
        both_tach_def,
        source=hist_tach["source_ref"],
        status="passed",
        result_origin=hist_tach["result_origin"],
        result_instance="other-tool",
        native_exit_code=0,
        tool=check_contract.tool_ref("tach/check", version="0.10.0"),
        evidence={"stdout": "ok"},
        rule_ref=hist_tach["check"]["rule_ref"],
        runner_ref=hist_tach["runner_ref"],
        baseline_ref=hist_tach.get("baseline_ref"),
    )
    pre_tach_v2 = execution.build_pre_execution_source_proof(
        plan_from_a, "tach", root=root
    )
    upgraded_proof = {
        **pre_tach_v2,
        "tool_identities": [
            {
                "requirement_id": "tach-check",
                "after": [],
                "alternatives": [
                    {
                        "access_id": "tach/check",
                        "tool_id": None,
                        "tool_id_source": None,
                        "version": "0.20.0",
                        "artifact_sha256": None,
                        "authority": "eci_local_entry",
                    }
                ],
            }
        ],
    }
    bad_tach = execution.compare_pre_execution_to_historical_result(
        upgraded_proof, wrong_tool_nr
    )
    assert bad_tach["equivalent"] is False
    assert bad_tach["reason"] == "tool_mismatch"


def test_pre_execution_multi_ref_per_ref_and_data_prerequisite_boundaries(tmp_path):
    root, base_a = _init_repo(tmp_path)
    (root / "src" / "pkg" / "mod.py").write_text("VALUE = 40\n", encoding="utf-8")
    _git(root, "add", "src/pkg/mod.py")
    _git(root, "commit", "-q", "-m", "commit B")
    base_b = _git(root, "rev-parse", "HEAD").strip()

    (root / "src" / "pkg" / "mod.py").write_text("VALUE = 50\n", encoding="utf-8")
    _git(root, "add", "src/pkg/mod.py")
    _git(root, "commit", "-q", "-m", "target head")
    head = _git(root, "rev-parse", "HEAD").strip()
    head_tree = _git(root, "rev-parse", f"{head}^{{tree}}").strip()

    cfg = selection.load_config(root / "ci-selection.toml")
    hyg_cfg = hygiene.load_config(root / "ci-hygiene.toml")
    multi_cs = selection.changeset_multi(
        root,
        [
            {
                "remote_ref": "refs/heads/ref-a",
                "local_ref": "refs/heads/ref-a",
                "source_ref": "refs/heads/ref-a",
                "before": base_a,
                "after": head,
                "tree": head_tree,
                "ref_action": "update",
                "baseline_ref": base_a,
            },
            {
                "remote_ref": "refs/heads/ref-b",
                "local_ref": "refs/heads/ref-b",
                "source_ref": "refs/heads/ref-b",
                "before": base_b,
                "after": head,
                "tree": head_tree,
                "ref_action": "update",
                "baseline_ref": base_b,
            },
            {
                "remote_ref": "refs/heads/deleted",
                "local_ref": "(delete)",
                "source_ref": "refs/heads/deleted",
                "before": base_a,
                "after": "0" * 40,
                "tree": None,
                "ref_action": "delete",
                "baseline_ref": None,
            },
        ],
    )
    sel = selection.select(multi_cs, cfg, root=root)
    multi_plan = selection.plan(
        sel,
        config=cfg,
        config_path=root / "ci-selection.toml",
        hygiene_config=hyg_cfg,
        hygiene_config_path=root / "ci-hygiene.toml",
        root=root,
        tach_validation={
            "status": "passed",
            "tach_sha256": selection._digest(root / cfg.tach),
            "source_tree": head_tree,
        },
    )
    per_ref_scopes = multi_plan["expected_checks"]["per_ref"]
    scope_a = next(s for s in per_ref_scopes if s["source_ref"] == "refs/heads/ref-a")
    scope_b = next(s for s in per_ref_scopes if s["source_ref"] == "refs/heads/ref-b")
    scope_del = next(
        s for s in per_ref_scopes if s["source_ref"] == "refs/heads/deleted"
    )

    # Ambiguous multi-ref call without scope_id/expected_check_ref is rejected:
    with pytest.raises(execution.ExecutionError, match="pre_execution_scope_required"):
        execution.build_pre_execution_source_proof(multi_plan, "whitespace", root=root)

    proof_a = execution.build_pre_execution_source_proof(
        multi_plan, "whitespace", root=root, scope_id=scope_a["scope_id"]
    )
    proof_b = execution.build_pre_execution_source_proof(
        multi_plan, "whitespace", root=root, scope_id=scope_b["scope_id"]
    )
    assert proof_a["status"] == "verified_candidate_inputs"
    assert proof_b["status"] == "verified_candidate_inputs"
    assert proof_a["scope_id"] == scope_a["scope_id"]
    assert proof_b["scope_id"] == scope_b["scope_id"]
    assert proof_a["baseline_ref"]["base_commit"] == base_a
    assert proof_b["baseline_ref"]["base_commit"] == base_b
    assert proof_a["closure_digest"] != proof_b["closure_digest"]

    # Deleted Ref keeps safe unverified boundary:
    del_tach = execution.build_pre_execution_source_proof(
        multi_plan, "tach", root=root, scope_id=scope_del["scope_id"]
    )
    assert del_tach["status"] == "unverified"
    assert del_tach["source_ref"] is None

    # Pre-execution data prerequisite: non-empty unrelated artifact_paths NEVER grants closure;
    # genuine verified Producer + output_ref under artifact_root IS bound & checked on reuse:
    wt_plan = _worktree_plan(root, base_b)
    real_producer_action = {
        "type": "command",
        "argv": [
            sys.executable,
            "-c",
            (
                "import pathlib, sys; "
                "p = pathlib.Path(sys.argv[1]) / 'generated' / 'schema.py'; "
                "p.parent.mkdir(parents=True, exist_ok=True); "
                "p.write_text('SCHEMA = 1\\n', encoding='utf-8')"
            ),
            "{artifact_dir}",
        ],
    }
    data_edge = {
        "producer_ref": {"kind": "prerequisite_action", "name": "codegen"},
        "consumer_ref": {"kind": "check", "name": "tach"},
        "dependency_kind": "data",
        "requires_pass": True,
        "execution_phase": "pre_execution",
        "output_ref": {"kind": "file", "path": "generated/schema.py"},
        "source_binding_ref": "git",
        "declaration_ref": {
            "path": "ci-selection.toml",
            "sha256": selection._digest(root / "ci-selection.toml"),
            "section": "prerequisite_actions.codegen",
        },
        "resolution": "resolved",
        "unresolved_reason": None,
    }
    wt_with_data_dep = {
        **wt_plan,
        "selection": {
            **wt_plan["selection"],
            "prerequisites": ["codegen"],
        },
        "prerequisite_actions": {"codegen": real_producer_action},
        "dependency_contract": plan_contract.graph_contract(
            [data_edge], required_semantics=["data_dependency"]
        ),
    }
    (root / "unrelated.txt").write_text("decoy\n", encoding="utf-8")
    dep_unproven = execution.build_pre_execution_source_proof(
        wt_with_data_dep,
        "tach",
        root=root,
        artifact_paths=("unrelated.txt",),
    )
    (root / "unrelated.txt").unlink()
    assert dep_unproven["status"] == "unverified"
    assert dep_unproven["reason"] == "pre_execution_prerequisite_unproven"

    artifact_root = tmp_path / "artifacts"
    wrong_ns = execution.producer_artifact_root(
        artifact_root, {"kind": "prerequisite_action", "name": "other"}
    )
    (wrong_ns / "generated").mkdir(parents=True)
    (wrong_ns / "generated" / "schema.py").write_text("X = 1\n", encoding="utf-8")
    assert (
        execution.build_pre_execution_source_proof(
            wt_with_data_dep,
            "tach",
            root=root,
            artifact_root=artifact_root,
            prerequisite_results={"codegen": {"status": "passed"}},
        )["reason"]
        == "data_artifact_missing"
    )

    real_ns = execution.producer_artifact_root(
        artifact_root, {"kind": "prerequisite_action", "name": "codegen"}
    )
    (real_ns / "generated").mkdir(parents=True)
    schema_file = real_ns / "generated" / "schema.py"
    schema_file.write_text("SCHEMA = 1\n", encoding="utf-8")
    assert (
        execution.build_pre_execution_source_proof(
            wt_with_data_dep,
            "tach",
            root=root,
            artifact_root=artifact_root,
            prerequisite_results={"codegen": {"status": "failed"}},
        )["reason"]
        == "data_artifact_missing"
        or execution.build_pre_execution_source_proof(
            {
                **wt_with_data_dep,
                "dependency_contract": {
                    "schema_version": 2,
                    "edges": [
                        {
                            **wt_with_data_dep["dependency_contract"]["edges"][0],
                            "requires_pass": True,
                        }
                    ],
                },
            },
            "tach",
            root=root,
            artifact_root=artifact_root,
            prerequisite_results={"codegen": {"status": "failed"}},
        )["reason"]
        == "pre_execution_prerequisite_failed"
    )

    dep_verified = execution.build_pre_execution_source_proof(
        wt_with_data_dep,
        "tach",
        root=root,
        artifact_root=artifact_root,
        prerequisite_results={"codegen": {"status": "passed"}},
    )
    assert dep_verified["status"] == "verified_candidate_inputs"
    assert "generated/schema.py" in dep_verified["source_ref"]["verified_input_scope"]
    gen_refs = dep_verified["prerequisite_inputs"][0]["generated_input_refs"]
    assert gen_refs[0]["path"] == "generated/schema.py"
    assert gen_refs[0]["sha256"] == check_contract.file_digest(schema_file)

    # Real Producer -> Consumer View (build_consumer_view) -> execution.execute() parity,
    # without fabricating source_ref or generated_input refs:
    real_dep_rep = execution.execute(wt_with_data_dep, root=root, runner=_HostRunner())
    assert real_dep_rep["status"] == "passed", real_dep_rep
    real_tach_nr = next(c for c in real_dep_rep["checks"] if c["name"] == "tach")[
        "native_result"
    ]
    assert "generated/schema.py" in real_tach_nr["source_ref"]["verified_input_scope"]
    assert any(
        ref.get("kind") == "generated_input"
        and ref.get("path") == "generated/schema.py"
        and ref.get("sha256") == check_contract.file_digest(schema_file)
        for ref in real_tach_nr["input_refs"]
    )
    eq_real_dep = execution.compare_pre_execution_to_historical_result(
        dep_verified, real_tach_nr
    )
    assert eq_real_dep["equivalent"] is True
    assert eq_real_dep["single_result_inputs_equivalent"] is True
    assert eq_real_dep["whole_check_reuse_admitted"] is False

    # Mutating generated/schema.py in artifact_root changes content_digest + generated_input -> refused:
    schema_file.write_text("SCHEMA = 2\n", encoding="utf-8")
    dep_verified_mut = execution.build_pre_execution_source_proof(
        wt_with_data_dep,
        "tach",
        root=root,
        artifact_root=artifact_root,
        prerequisite_results={"codegen": {"status": "passed"}},
    )
    bad_changed_artifact = execution.compare_pre_execution_to_historical_result(
        dep_verified_mut, real_tach_nr
    )
    assert bad_changed_artifact["equivalent"] is False
    assert bad_changed_artifact["reason"] == "source_content_mismatch"

    # Historical result executed WITHOUT data dependency (missing generated/schema.py overlay) -> refused:
    wt_rep = execution.execute(wt_plan, root=root, runner=_HostRunner())
    hist_without_dep = next(c for c in wt_rep["checks"] if c["name"] == "tach")[
        "native_result"
    ]
    assert (
        execution.compare_pre_execution_to_historical_result(
            dep_verified, hist_without_dep
        )["reason"]
        == "source_content_mismatch"
    )
    hist_missing_input_ref = check_contract.native_result(
        check_contract.definition_for("tach"),
        source=real_tach_nr["source_ref"],
        status="passed",
        result_origin="host",
        result_instance="tach-no-gen-ref",
        native_exit_code=0,
        tool=check_contract.tool_ref("tach/check"),
        evidence={"stdout": "ok"},
        rule_ref=dep_verified["rule_ref"],
        runner_ref="execution:host",
        baseline_ref=dep_verified["baseline_ref"],
        input_refs=[],
    )
    assert (
        execution.compare_pre_execution_to_historical_result(
            dep_verified, hist_missing_input_ref
        )["reason"]
        == "prerequisite_artifact_mismatch"
    )


def test_pre_execution_single_result_match_never_implies_composite_check_reuse(
    tmp_path,
):
    root, base = _init_repo(tmp_path)
    (root / "docs.md").write_text("# Updated\n", encoding="utf-8")
    plan = _worktree_plan(root, base)

    docs_pre = execution.build_pre_execution_source_proof(plan, "docs", root=root)
    assert docs_pre["status"] == "verified_candidate_inputs"
    assert [t["requirement_id"] for t in docs_pre["tool_identities"]] == [
        "inventory",
        "links",
    ]

    # Build strict Expected for docs from the plan:
    docs_expected_entry = next(
        c for c in plan["expected_checks"]["checks"] if c["name"] == "docs"
    )
    strict_docs = {
        "check_id": docs_expected_entry["check_id"],
        "definition_version": docs_expected_entry["definition_version"],
        "rule_ref": docs_pre["rule_ref"],
        "baseline_ref": docs_pre["baseline_ref"],
        "source_ref": {
            **docs_pre["source_ref"],
            "actual_execution_source": "worktree",
        },
        "input_refs": [],
    }
    docs_def = check_contract.definition_for("docs")
    inv_only = check_contract.native_result(
        docs_def,
        source=strict_docs["source_ref"],
        status="passed",
        result_origin="host",
        result_instance="inv-1",
        native_exit_code=0,
        tool=check_contract.tool_ref("git/ls-files"),
        evidence={"stdout": "docs.md\n"},
        rule_ref=docs_pre["rule_ref"],
        runner_ref="execution:host",
        baseline_ref=docs_pre["baseline_ref"],
    )

    # Single-Result input equivalence certifies ONLY the 'inventory' result, NEVER full Check reuse:
    eq_inv = execution.compare_pre_execution_to_historical_result(docs_pre, inv_only)
    assert eq_inv["comparison_scope"] == "single_native_result"
    assert eq_inv["equivalent"] is True
    assert eq_inv["single_result_inputs_equivalent"] is True
    assert eq_inv["matched_requirement_id"] == "inventory"
    assert eq_inv["whole_check_reuse_admitted"] is False
    assert eq_inv["check_satisfaction_evaluated"] is False

    # Confirm via #52 that inv_only alone leaves composite docs UNSATISFIED (cannot skip links!):
    partial_sat = check_contract.evaluate_expected_satisfaction([inv_only], strict_docs)
    assert partial_sat["status"] == "unsatisfied"

    # When both inventory AND links exist, each passes single-Result input check AND #52 satisfies:
    links_nr = check_contract.native_result(
        docs_def,
        source=strict_docs["source_ref"],
        status="passed",
        result_origin="host",
        result_instance="links-1",
        native_exit_code=0,
        tool=check_contract.tool_ref("lychee/docs"),
        evidence={"stdout": "0 errors\n"},
        rule_ref=docs_pre["rule_ref"],
        runner_ref="execution:host",
        baseline_ref=docs_pre["baseline_ref"],
        input_refs=[
            {
                "kind": "check_result",
                "result_id": inv_only["result_id"],
                "path": "git/ls-files",
            }
        ],
    )
    eq_links = execution.compare_pre_execution_to_historical_result(docs_pre, links_nr)
    assert eq_links["equivalent"] is True
    assert eq_links["matched_requirement_id"] == "links"
    assert eq_links["whole_check_reuse_admitted"] is False
    full_sat = check_contract.evaluate_expected_satisfaction(
        [inv_only, links_nr], strict_docs
    )
    assert full_sat["status"] == "satisfied"


def test_whitespace_linked_worktree_core_attributes_and_drift(tmp_path, monkeypatch):
    main_root, base = _init_repo(tmp_path, name="main_wt")
    linked = tmp_path / "linked_wt"
    _git(main_root, "worktree", "add", "-q", "-b", "feature", str(linked))
    (linked / "src" / "pkg" / "mod.py").write_text("VALUE = 9\n", encoding="utf-8")

    # 1. Shared .git/info/attributes is resolved through linked worktree commondir:
    plan_wt = _worktree_plan(linked, base)
    proof_initial = execution.build_pre_execution_source_proof(
        plan_wt, "whitespace", root=linked
    )
    assert proof_initial["status"] == "verified_candidate_inputs"
    assert proof_initial["environment_inputs"]["info_attributes_sha256"] is None
    assert proof_initial["environment_inputs"]["core_attributes_file_sha256"] is None

    rep_wt = execution.execute(plan_wt, root=linked, runner=_HostRunner())
    ws_nr_initial = next(c for c in rep_wt["checks"] if c["name"] == "whitespace")[
        "native_result"
    ]
    assert (
        execution.compare_pre_execution_to_historical_result(
            proof_initial, ws_nr_initial
        )["equivalent"]
        is True
    )

    # Mutating main repo's shared .git/info/attributes changes linked worktree closure:
    shared_info_attrs = main_root / ".git" / "info" / "attributes"
    shared_info_attrs.parent.mkdir(parents=True, exist_ok=True)
    shared_info_attrs.write_text("*.py -whitespace\n", encoding="utf-8")
    proof_shared_info = execution.build_pre_execution_source_proof(
        plan_wt, "whitespace", root=linked
    )
    assert proof_shared_info["environment_inputs"]["info_attributes_sha256"] is not None
    assert proof_shared_info["closure_digest"] != proof_initial["closure_digest"]
    bad_info = execution.compare_pre_execution_to_historical_result(
        proof_shared_info, ws_nr_initial
    )
    assert bad_info["equivalent"] is False
    assert bad_info["reason"] == "whitespace_config_mismatch"

    # 2. core.attributesFile is hashed when set, distinguished if missing, and blocks mismatch:
    global_attrs = tmp_path / "custom.gitattributes"
    global_attrs.write_text("*.py whitespace=tab-in-indent\n", encoding="utf-8")
    _git(linked, "config", "core.attributesfile", str(global_attrs))
    proof_attr_v1 = execution.build_pre_execution_source_proof(
        plan_wt, "whitespace", root=linked
    )
    attr_digest = proof_attr_v1["environment_inputs"]["core_attributes_file_sha256"]
    assert isinstance(attr_digest, str) and attr_digest.startswith("sha256:")

    rep_attr_v1 = execution.execute(plan_wt, root=linked, runner=_HostRunner())
    ws_nr_attr_v1 = next(c for c in rep_attr_v1["checks"] if c["name"] == "whitespace")[
        "native_result"
    ]
    assert (
        execution.compare_pre_execution_to_historical_result(
            proof_attr_v1, ws_nr_attr_v1
        )["equivalent"]
        is True
    )

    global_attrs.write_text("*.py whitespace=trailing-space\n", encoding="utf-8")
    proof_attr_v2 = execution.build_pre_execution_source_proof(
        plan_wt, "whitespace", root=linked
    )
    assert (
        proof_attr_v2["environment_inputs"]["core_attributes_file_sha256"]
        != attr_digest
    )
    bad_global_attr = execution.compare_pre_execution_to_historical_result(
        proof_attr_v2, ws_nr_attr_v1
    )
    assert bad_global_attr["equivalent"] is False
    assert bad_global_attr["reason"] == "whitespace_config_mismatch"

    global_attrs.unlink()
    proof_attr_missing = execution.build_pre_execution_source_proof(
        plan_wt, "whitespace", root=linked
    )
    assert str(
        proof_attr_missing["environment_inputs"]["core_attributes_file_sha256"]
    ).startswith("missing:")

    # 3. Mid-execution git whitespace config drift keeps native PASS fact intact
    #    while omitting git_whitespace_config from evidence (no post-hoc fallback):
    _git(linked, "config", "--unset", "core.attributesfile")
    shared_info_attrs.unlink()
    orig_hygiene_run = hygiene.run

    def _drifting_hygiene_run(changes, cfg, **kwargs):
        res = orig_hygiene_run(changes, cfg, **kwargs)
        _git(linked, "config", "core.whitespace", "tab-in-indent")
        return res

    monkeypatch.setattr(hygiene, "run", _drifting_hygiene_run)
    drifted_rep = execution.execute(plan_wt, root=linked, runner=_HostRunner())
    drifted_ws = next(c for c in drifted_rep["checks"] if c["name"] == "whitespace")
    assert drifted_ws["status"] == "passed"
    drifted_nr = drifted_ws["native_result"]
    assert drifted_nr["status"] == "passed"
    assert "git_whitespace_config" not in (drifted_nr.get("evidence") or {})

    proof_post_drift = execution.build_pre_execution_source_proof(
        plan_wt, "whitespace", root=linked
    )
    refused_drift = execution.compare_pre_execution_to_historical_result(
        proof_post_drift, drifted_nr
    )
    assert refused_drift["equivalent"] is False
    assert refused_drift["reason"] == "whitespace_config_mismatch"


def test_whitespace_default_user_attributes_enter_effective_rule_identity(
    tmp_path, monkeypatch
):
    xdg = tmp_path / "xdg"
    (xdg / "git").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    root, base = _init_repo(tmp_path)
    # No explicit core.attributesFile anywhere in this scenario.
    (root / "src" / "pkg" / "mod.py").write_text("VALUE = 2   \n", encoding="utf-8")
    plan = _worktree_plan(root, base)

    proof_before = execution.build_pre_execution_source_proof(
        plan, "whitespace", root=root
    )
    assert proof_before["status"] == "verified_candidate_inputs"
    assert proof_before["environment_inputs"]["core_attributes_file_sha256"] is None
    before_rep = execution.execute(plan, root=root, runner=_HostRunner())
    ws_before = next(c for c in before_rep["checks"] if c["name"] == "whitespace")
    assert ws_before["status"] == "failed"
    nr_before = ws_before["native_result"]
    assert (
        execution.compare_pre_execution_to_historical_result(proof_before, nr_before)[
            "equivalent"
        ]
        is True
    )

    # Only the default user-level attributes change; bytes/HEAD/baseline do not.
    (xdg / "git" / "attributes").write_text("*.py -whitespace\n", encoding="utf-8")
    proof_after = execution.build_pre_execution_source_proof(
        plan, "whitespace", root=root
    )
    assert proof_after["status"] == "verified_candidate_inputs"
    assert proof_after["environment_inputs"]["core_attributes_file_sha256"] is None
    assert (
        proof_after["environment_inputs"]["effective_git_attributes_sha256"]
        != proof_before["environment_inputs"]["effective_git_attributes_sha256"]
    )
    assert proof_after["closure_digest"] != proof_before["closure_digest"]
    stale = execution.compare_pre_execution_to_historical_result(proof_after, nr_before)
    assert stale["equivalent"] is False
    assert stale["reason"] == "whitespace_config_mismatch"

    after_rep = execution.execute(plan, root=root, runner=_HostRunner())
    ws_after = next(c for c in after_rep["checks"] if c["name"] == "whitespace")
    assert ws_after["status"] == "passed"
    assert (
        execution.compare_pre_execution_to_historical_result(
            proof_after, ws_after["native_result"]
        )["equivalent"]
        is True
    )


@pytest.mark.parametrize(
    ("content", "default_attributes"),
    [
        (
            "<<<<<<< ours\nVALUE = 1\n=======\nVALUE = 2\n>>>>>>> theirs\n",
            "* conflict-marker-size=8\n",
        ),
        ("VALUE = 2   \n", "* -diff\n"),
    ],
    ids=["conflict-marker-size", "diff"],
)
def test_default_user_attributes_beyond_whitespace_enter_rule_identity(
    tmp_path, monkeypatch, content, default_attributes
):
    xdg = tmp_path / "xdg"
    (xdg / "git").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    root, base = _init_repo(tmp_path)
    (root / "src" / "pkg" / "mod.py").write_text(content, encoding="utf-8")
    plan = _worktree_plan(root, base)

    proof_before = execution.build_pre_execution_source_proof(
        plan, "whitespace", root=root
    )
    before_rep = execution.execute(plan, root=root, runner=_HostRunner())
    ws_before = next(c for c in before_rep["checks"] if c["name"] == "whitespace")
    assert ws_before["status"] == "failed"
    nr_before = ws_before["native_result"]
    assert (
        execution.compare_pre_execution_to_historical_result(proof_before, nr_before)[
            "equivalent"
        ]
        is True
    )

    (xdg / "git" / "attributes").write_text(default_attributes, encoding="utf-8")
    # whitespace itself stays unspecified; only another attribute changed.
    assert (
        _git(root, "check-attr", "whitespace", "--", "src/pkg/mod.py")
        .strip()
        .endswith("unspecified")
    )
    proof_after = execution.build_pre_execution_source_proof(
        plan, "whitespace", root=root
    )
    assert proof_after["closure_digest"] != proof_before["closure_digest"]
    assert (
        proof_after["environment_inputs"]["effective_git_attributes_sha256"]
        != proof_before["environment_inputs"]["effective_git_attributes_sha256"]
    )
    stale = execution.compare_pre_execution_to_historical_result(proof_after, nr_before)
    assert stale["equivalent"] is False
    assert stale["reason"] == "whitespace_config_mismatch"

    after_rep = execution.execute(plan, root=root, runner=_HostRunner())
    ws_after = next(c for c in after_rep["checks"] if c["name"] == "whitespace")
    assert ws_after["status"] == "passed"
    assert (
        execution.compare_pre_execution_to_historical_result(
            proof_after, ws_after["native_result"]
        )["equivalent"]
        is True
    )


def _plan_with_data_edge(plan, *, producer_name, check_name, artifact_path):
    producer_action = {
        "type": "command",
        "argv": [sys.executable, "-c", "pass"],
    }
    data_edge = {
        "producer_ref": {"kind": "prerequisite_action", "name": producer_name},
        "consumer_ref": {"kind": "check", "name": check_name},
        "dependency_kind": "data",
        "requires_pass": True,
        "execution_phase": "pre_execution",
        "output_ref": {"kind": "file", "path": artifact_path},
        "source_binding_ref": "git",
        "declaration_ref": {
            "path": "ci-selection.toml",
            "sha256": "a" * 64,
            "section": f"prerequisite_actions.{producer_name}",
        },
        "resolution": "resolved",
        "unresolved_reason": None,
    }
    return {
        **plan,
        "selection": {
            **plan["selection"],
            "prerequisites": [producer_name],
        },
        "prerequisite_actions": {producer_name: producer_action},
        "dependency_contract": plan_contract.graph_contract(
            [data_edge], required_semantics=["data_dependency"]
        ),
    }


def test_pre_execution_overlay_subset_digest_uses_effective_view(tmp_path):
    """Producer regression: overlay intersecting Expected changed_paths.

    Subset digest and closed SourceRef must come from the same effective
    consumer view. An authenticated overlay that mutates a changed_paths
    member must not mint a verified P0 that silently binds Expected digest X
    to overlaid closure Y.
    """
    root, base = _init_repo(tmp_path)
    (root / "src" / "pkg" / "mod.py").write_text("VALUE = 7\n", encoding="utf-8")
    plan = _worktree_plan(root, base)
    tach_expected = next(
        c for c in plan["expected_checks"]["checks"] if c["name"] == "tach"
    )
    expected_source = tach_expected["source_ref"]
    assert expected_source["scope_kind"] == "changed_paths"
    assert "src/pkg/mod.py" in expected_source["verified_input_scope"]
    expected_digest = expected_source["content_digest"]

    # Overlay outside the Expected subset: subset digest stays X, overlay
    # intersection is empty, closed proof remains verified.
    outside_plan = _plan_with_data_edge(
        plan,
        producer_name="codegen",
        check_name="tach",
        artifact_path="generated/schema.py",
    )
    artifact_root = tmp_path / "artifacts"
    outside_ns = execution.producer_artifact_root(
        artifact_root, {"kind": "prerequisite_action", "name": "codegen"}
    )
    (outside_ns / "generated").mkdir(parents=True)
    (outside_ns / "generated" / "schema.py").write_text(
        "SCHEMA = 1\n", encoding="utf-8"
    )
    outside_proof = execution.build_pre_execution_source_proof(
        outside_plan,
        "tach",
        root=root,
        artifact_root=artifact_root,
        prerequisite_results={"codegen": {"status": "passed"}},
    )
    assert outside_proof["status"] == "verified_candidate_inputs"
    assert outside_proof["changed_paths_content_digest"] == expected_digest
    assert outside_proof["overlay_subset_paths"] == []
    assert "generated/schema.py" in outside_proof["source_ref"]["verified_input_scope"]

    # Overlay mutates a changed_paths member: plan content_binding rejects the
    # drifted effective view before any verified P0 can be minted.
    mutate_plan = _plan_with_data_edge(
        plan,
        producer_name="rewrite",
        check_name="tach",
        artifact_path="src/pkg/mod.py",
    )
    mutate_root = tmp_path / "mutate-artifacts"
    mutate_ns = execution.producer_artifact_root(
        mutate_root, {"kind": "prerequisite_action", "name": "rewrite"}
    )
    (mutate_ns / "src" / "pkg").mkdir(parents=True)
    (mutate_ns / "src" / "pkg" / "mod.py").write_text("VALUE = 999\n", encoding="utf-8")
    mutated = execution.build_pre_execution_source_proof(
        mutate_plan,
        "tach",
        root=root,
        artifact_root=mutate_root,
        prerequisite_results={"rewrite": {"status": "passed"}},
    )
    assert mutated["status"] == "unverified"
    assert mutated["reason"] == "pre_execution_input_closure_unproven"
    assert mutated["source_ref"] is None

    # Without plan path-binding, the same overlay still fails: subset digest is
    # taken from the effective consumer-view snapshot (overlaid bytes), which
    # no longer equals Expected changed_paths content_digest X.
    unbound_git = {
        key: value
        for key, value in (mutate_plan.get("git") or {}).items()
        if key != "content_binding"
    }
    unbound_plan = {**mutate_plan, "git": unbound_git}
    mismatched = execution.build_pre_execution_source_proof(
        unbound_plan,
        "tach",
        root=root,
        artifact_root=mutate_root,
        prerequisite_results={"rewrite": {"status": "passed"}},
    )
    assert mismatched["status"] == "unverified"
    assert mismatched["reason"] == "pre_execution_changed_paths_digest_mismatch"
    assert mismatched["source_ref"] is None
    assert mismatched.get("changed_paths_content_digest") is None

    # Same overlay path, identical bytes: subset digest still equals Expected X,
    # but overlay_subset_paths records the intersection with the effective view.
    same_root = tmp_path / "same-artifacts"
    same_ns = execution.producer_artifact_root(
        same_root, {"kind": "prerequisite_action", "name": "rewrite"}
    )
    (same_ns / "src" / "pkg").mkdir(parents=True)
    (same_ns / "src" / "pkg" / "mod.py").write_text("VALUE = 7\n", encoding="utf-8")
    same_bytes = execution.build_pre_execution_source_proof(
        mutate_plan,
        "tach",
        root=root,
        artifact_root=same_root,
        prerequisite_results={"rewrite": {"status": "passed"}},
    )
    assert same_bytes["status"] == "verified_candidate_inputs"
    assert same_bytes["changed_paths_content_digest"] == expected_digest
    assert same_bytes["overlay_subset_paths"] == ["src/pkg/mod.py"]
    assert same_bytes["source_ref"]["scope_kind"] in {
        "check_input_closure",
        "full_input_closure",
    }
