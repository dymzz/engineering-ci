"""End-to-end tests for controlled Workflow, ShellCheck and zizmor integration (Refs #58)."""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

from engineering_ci import (
    check_contract,
    execution,
    expected_checks,
    hygiene,
    selection,
    workflow_analysis,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_FIXTURE = ROOT / "tests/fixtures/workflow-app"
CLEAN_ZIZMOR_WORKFLOW = """\
name: clean-ci
on:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
        with:
          persist-credentials: false
      - shell: pwsh
        run: Write-Output "ok"
"""
UNPINNED_VALID_ACTIONLINT_WORKFLOW = """\
name: ci
on:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - shell: bash
        run: echo "ok"
"""
INLINE_SC2086_WORKFLOW = """\
name: inline-sc
on:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - shell: bash
        run: echo $UNQUOTED
"""
SYNTAX_BROKEN_WORKFLOW = """\
name: broken
on: [push]
jobs:
  broken:
    steps:
      - run: echo "missing runs-on"
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _init_repo(
    tmp_path: Path,
    *,
    checks: tuple[str, ...] = (
        "actionlint",
        "shellcheck",
        "workflow_shellcheck",
        "zizmor",
    ),
    workflows: dict[str, str] | None = None,
    shells: dict[str, str] | None = None,
    dependency_edges: list[dict] | None = None,
    workflow_checks_extra: str = "",
) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src/mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "tests/test_mod.py").write_text(
        "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): pass\n",
        encoding="utf-8",
    )
    checks_toml = ", ".join(f'"{name}"' for name in checks)
    edges_toml = ""
    for edge in dependency_edges or ():
        edges_toml += (
            "\n[[dependency_edges]]\n"
            f'producer_ref = {{ kind = "check", name = "{edge["from"]}" }}\n'
            f'consumer_ref = {{ kind = "check", name = "{edge["to"]}" }}\n'
            f'dependency_kind = "{edge.get("kind", "pre_execution_pass")}"\n'
        )
    (repo / "ci-selection.toml").write_text(
        f"""\
schema_version = 1
checks = [{checks_toml}]

[[non_code_rules]]
prefix = ".github/workflows/"
tests = []
reason = "workflow files"

[[non_code_rules]]
prefix = "scripts/"
tests = []
reason = "shell scripts"
{workflow_checks_extra}
{edges_toml}
""",
        encoding="utf-8",
    )
    (repo / "ci-hygiene.toml").write_text(
        'schema_version = 1\nchecks = ["whitespace"]\n',
        encoding="utf-8",
    )
    (repo / "tach.toml").write_text(
        'source_roots = ["src"]\n[[modules]]\npath = "src"\ndepends_on = []\n',
        encoding="utf-8",
    )
    for rel, content in (workflows or {}).items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    for rel, content in (shells or {}).items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write LF line endings so ShellCheck never trips on CRLF SC1017 on Windows.
        target.write_text(content, encoding="utf-8", newline="\n")

    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "ECI Test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    head = _git(repo, "rev-parse", "HEAD").strip()
    return repo, head


def _build_plan_and_expected(
    repo: Path,
    changed: list[str],
    *,
    effective_profile: dict | None = None,
):
    cfg_path = repo / "ci-selection.toml"
    cfg = selection.load_config(cfg_path)
    hyg_path = repo / "ci-hygiene.toml"
    hyg_cfg = hygiene.load_config(hyg_path)
    head_tree = _git(repo, "rev-parse", "HEAD^{tree}").strip()
    head_commit = _git(repo, "rev-parse", "HEAD").strip()
    changeset = selection.ChangeSet(
        root=repo,
        base_ref="origin/main",
        head_ref="HEAD",
        base_commit=head_commit,
        head_commit=head_commit,
        tree=head_tree,
        paths=tuple(changed),
        worktree_dirty=False,
        complete=True,
        snapshot="worktree",
    )
    hyg_id = hygiene.config_identity(hyg_path, hyg_cfg, root=repo)
    sel = selection.select(changeset, cfg, root=repo)
    plan_doc = selection.plan(
        sel,
        config=cfg,
        config_path=cfg_path,
        root=repo,
        hygiene_config=hyg_cfg,
        hygiene_config_path=hyg_path,
        effective_profile=effective_profile,
    )
    expected = expected_checks.derive_expected_checks(
        changeset,
        plan_doc["check_scope"],
        config_path=cfg_path,
        consumer="dymzz/engineering-ci",
        hygiene_config=hyg_cfg,
        hygiene_identity=hyg_id,
        selection_config=cfg,
        effective_profile=effective_profile,
    )
    return plan_doc, expected


class _CountingRunner:
    """Wrap real local execution (or stub tool exits) while counting tool invocations."""

    def __init__(self, *, overrides=None):
        self.overrides = overrides or {}
        self.calls: list[list[str]] = []

    def __call__(self, argv, cwd, data=None):
        argv_list = [str(item) for item in argv]
        self.calls.append(argv_list)
        for predicate, response in self.overrides.items():
            matched = predicate(argv_list)
            if matched:
                return response(argv_list) if callable(response) else response
        return hygiene._run(argv_list, cwd, data)


def _write_policy_state(tmp_path: Path, rules: list[dict], *, now: dt.datetime):
    from test_security_policy import NOW as POLICY_NOW
    from test_security_policy import policy_document, receive

    doc = policy_document(rules=rules)
    state_path = tmp_path / "policy-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = receive(doc, state=state_path, now=POLICY_NOW)
    return {
        "snapshot": snapshot,
        "state_path": str(state_path),
        "selection_id": "sel-workflow-security",
        "selection_policy_revision": "rev-1",
        "protected_profile": "ci-security",
        "platform": {"os": "linux", "arch": "x86_64"},
        "now": POLICY_NOW,
    }


def test_controlled_check_registry_and_partition():
    assert set(check_contract.CHECK_IDS.values()) == (
        check_contract.INTERNAL_CHECK_IDS | check_contract.PROCESS_CHECK_IDS
    )
    assert not (check_contract.INTERNAL_CHECK_IDS & check_contract.PROCESS_CHECK_IDS)
    for name in ("actionlint", "shellcheck", "workflow_shellcheck", "zizmor"):
        definition = check_contract.definition_for(name)
        assert definition is not None
        assert definition.check_id in check_contract.PROCESS_CHECK_IDS
        assert len(definition.tool_requirements) == 1
    sc_def = check_contract.definition_for("shellcheck")
    sc_alt_ids = {
        alt.access_id for req in sc_def.tool_requirements for alt in req.alternatives
    }
    assert sc_alt_ids == {"shellcheck/check"}
    wf_sc_def = check_contract.definition_for("workflow_shellcheck")
    wf_sc_alt_ids = {
        alt.access_id for req in wf_sc_def.tool_requirements for alt in req.alternatives
    }
    assert wf_sc_alt_ids == {"actionlint/shellcheck-inline"}


def test_selection_expected_checks_v1_and_v2_binding(tmp_path):
    repo, _ = _init_repo(
        tmp_path,
        workflows={".github/workflows/ci.yml": CLEAN_ZIZMOR_WORKFLOW},
        shells={"scripts/check.sh": "#!/bin/sh\necho ok\n"},
    )
    plan_v1, exp_v1 = _build_plan_and_expected(
        repo, [".github/workflows/ci.yml", "scripts/check.sh"]
    )
    profile = expected_checks.build_effective_profile(
        profile_ref=check_contract.profile_ref("eci/default", "1.0.0"),
        profile_revision="rev-1",
        approval_source="governance-signed",
        effective_at="2026-11-15T00:00:00Z",
        evaluated_at="2026-11-15T00:00:00Z",
        context="worktree",
        action="worktree",
        target="HEAD",
        declared_checks=[
            "whitespace",
            "actionlint",
            "shellcheck",
            "workflow_shellcheck",
            "zizmor",
        ],
        required_obligations=[
            {"obligation_ref": f"req-{name}", "check_names": [name]}
            for name in (
                "whitespace",
                "actionlint",
                "shellcheck",
                "workflow_shellcheck",
                "zizmor",
            )
        ],
        domain_complete=True,
        authority_verified=True,
    )
    # Attach independent [effective_profile] approval binding in ci-selection.toml
    binding = profile["approval_binding"]
    declared = profile["declared_checks"]
    cfg_path = repo / "ci-selection.toml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8")
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
    cfg = selection.load_config(cfg_path)
    plan_v2, exp_v2 = _build_plan_and_expected(
        repo,
        [".github/workflows/ci.yml", "scripts/check.sh"],
        effective_profile=profile,
    )
    scope_by_name = {item["name"]: item for item in plan_v1["check_scope"]}
    assert scope_by_name["actionlint"]["status"] == "selected"
    assert scope_by_name["shellcheck"]["status"] == "selected"
    assert scope_by_name["workflow_shellcheck"]["status"] == "selected"
    assert scope_by_name["zizmor"]["status"] == "selected"
    v2_by_name = {entry["name"]: entry for entry in exp_v2["checks"]}
    assert exp_v2["definitions_status"] == "domain_complete"
    assert exp_v2["status"] == "complete"
    assert v2_by_name["actionlint"]["rule_status"] == "resolved"
    assert v2_by_name["shellcheck"]["rule_status"] == "resolved"
    assert v2_by_name["workflow_shellcheck"]["rule_status"] == "resolved"
    assert v2_by_name["zizmor"]["rule_status"] == "resolved"
    # v1 preserves expected input_refs
    v1_by_name = {entry["name"]: entry for entry in exp_v1["checks"]}
    assert v1_by_name["shellcheck"]["definition_status"] == "resolved"
    assert v1_by_name["workflow_shellcheck"]["definition_status"] == "resolved"
    assert plan_v2["selection"]["checks"] == [
        "actionlint",
        "shellcheck",
        "workflow_shellcheck",
        "zizmor",
    ]
    assert cfg.checks == ("actionlint", "shellcheck", "workflow_shellcheck", "zizmor")


def test_real_tools_positive_clean_and_single_shared_actionlint_run(tmp_path):
    repo, _ = _init_repo(
        tmp_path,
        workflows={".github/workflows/ci.yml": CLEAN_ZIZMOR_WORKFLOW},
        shells={"scripts/ok.sh": '#!/bin/sh\necho "clean"\n'},
    )
    rules = expected_checks.governed_domain_rules(
        repo, config_path=repo / "ci-selection.toml"
    )
    profile = expected_checks.build_effective_profile(
        profile_ref=check_contract.profile_ref("eci/default", 1),
        profile_revision="rev-1",
        approval_source="governance-signed",
        effective_at="2026-11-15T00:00:00Z",
        evaluated_at="2026-11-15T00:00:00Z",
        context="worktree",
        action="worktree",
        target="HEAD",
        declared_checks=[
            "whitespace",
            "actionlint",
            "shellcheck",
            "workflow_shellcheck",
            "zizmor",
        ],
        required_obligations=[
            {"obligation_ref": f"req-{name}", "check_names": [name]}
            for name in (
                "whitespace",
                "actionlint",
                "shellcheck",
                "workflow_shellcheck",
                "zizmor",
            )
        ],
        domain_complete=True,
        authority_verified=True,
        governed_rules=rules,
    )
    plan_doc, _ = _build_plan_and_expected(
        repo,
        [".github/workflows/ci.yml", "scripts/ok.sh"],
        effective_profile=profile,
    )
    runner = _CountingRunner()
    report = execution.execute(plan_doc, root=repo, runner=runner)

    assert report["status"] == "passed", report
    assert report["exit_code"] == 0
    by_name = {entry["name"]: entry for entry in report["checks"]}
    for name in ("actionlint", "shellcheck", "workflow_shellcheck", "zizmor"):
        entry = by_name[name]
        assert entry["status"] == "passed", entry
        native = entry["native_result"]
        assert native["status"] == "passed"
        assert native["tool"]["version"] is not None
        assert entry["native_satisfaction"]["status"] == "satisfied"
        # Single CheckSatisfaction + ExpectedChecks verification against real Result
        definition = check_contract.definition_for(name)
        sat = check_contract.evaluate_check_satisfaction(
            definition,
            entry["native_results"],
        )
        assert sat["status"] == "satisfied"

    sc_evidence = by_name["shellcheck"]["native_result"]["evidence"]
    assert sc_evidence["coverage"]["standalone_shell"] is True
    assert sc_evidence["coverage"]["per_script_clean_receipts_available"] is False
    wf_sc_evidence = by_name["workflow_shellcheck"]["native_result"]["evidence"]
    assert wf_sc_evidence["coverage"]["workflow_inline_shell"] is True
    assert wf_sc_evidence["shared_actionlint"] is True
    # Actionlint main execution ran ONCE (excluding -version probe), shared with workflow_shellcheck!
    actionlint_scans = [
        call
        for call in runner.calls
        if "actionlint" in " ".join(call) and "-format" in call
    ]
    assert len(actionlint_scans) == 1


def test_real_tools_dag_mode_shares_single_actionlint_execution(tmp_path):
    repo, _ = _init_repo(
        tmp_path,
        workflows={".github/workflows/ci.yml": CLEAN_ZIZMOR_WORKFLOW},
        shells={"scripts/ok.sh": '#!/bin/sh\necho "clean"\n'},
        dependency_edges=[{"from": "actionlint", "to": "workflow_shellcheck"}],
    )
    plan_doc, _ = _build_plan_and_expected(
        repo, [".github/workflows/ci.yml", "scripts/ok.sh"]
    )
    runner = _CountingRunner()
    report = execution.execute(plan_doc, root=repo, runner=runner)
    assert report["status"] == "passed", report
    actionlint_scans = [
        call
        for call in runner.calls
        if "actionlint" in " ".join(call) and "-format" in call
    ]
    assert len(actionlint_scans) == 1


def test_real_tools_valid_actionlint_still_fails_zizmor_unpinned_uses(tmp_path):
    repo, _ = _init_repo(
        tmp_path,
        checks=("actionlint", "workflow_shellcheck", "zizmor"),
        workflows={".github/workflows/valid.yml": UNPINNED_VALID_ACTIONLINT_WORKFLOW},
    )
    plan_doc, _ = _build_plan_and_expected(repo, [".github/workflows/valid.yml"])
    report = execution.execute(plan_doc, root=repo)

    assert report["status"] == "failed"
    assert report["exit_code"] == 1
    by_name = {entry["name"]: entry for entry in report["checks"]}
    assert by_name["actionlint"]["status"] == "passed"
    assert by_name["actionlint"]["native_result"]["native_exit_code"] == 0
    assert by_name["workflow_shellcheck"]["status"] == "passed"
    assert by_name["zizmor"]["status"] == "failed"
    assert by_name["zizmor"]["native_result"]["native_exit_code"] == 14
    rules = {
        item["rule"]
        for item in by_name["zizmor"]["native_result"]["evidence"]["findings"]
    }
    assert "unpinned-uses" in rules


def test_real_tools_inline_shellcheck_finding_and_standalone_failure(tmp_path):
    repo, _ = _init_repo(
        tmp_path,
        checks=("actionlint", "shellcheck", "workflow_shellcheck"),
        workflows={".github/workflows/inline_sc.yml": INLINE_SC2086_WORKFLOW},
        shells={"scripts/bad.sh": "#!/bin/sh\necho $BAD_VAR\n"},
    )
    plan_doc, _ = _build_plan_and_expected(
        repo, [".github/workflows/inline_sc.yml", "scripts/bad.sh"]
    )
    report = execution.execute(plan_doc, root=repo)

    assert report["status"] == "failed"
    by_name = {entry["name"]: entry for entry in report["checks"]}
    assert by_name["actionlint"]["status"] == "passed"
    assert by_name["actionlint"]["native_result"]["native_exit_code"] == 0
    assert by_name["workflow_shellcheck"]["status"] == "failed"
    assert by_name["workflow_shellcheck"]["native_result"]["native_exit_code"] == 1
    wf_sc_ev = by_name["workflow_shellcheck"]["native_result"]["evidence"]
    assert any(
        f["rule"] == "shellcheck" and "SC2086" in f["message"]
        for f in wf_sc_ev["findings"]
    )
    assert by_name["shellcheck"]["status"] == "failed"
    assert by_name["shellcheck"]["native_result"]["native_exit_code"] == 1
    sc_rules = {
        f["rule"]
        for f in by_name["shellcheck"]["native_result"]["evidence"]["findings"]
    }
    assert "SC2086" in sc_rules


def test_disabled_or_syntax_failed_actionlint_never_counts_as_shellcheck_pass(tmp_path):
    # Case A: actionlint syntactically fails without emitting ShellCheck findings ->
    # workflow_shellcheck MUST stay not_executed, never passed.
    repo_a, _ = _init_repo(
        tmp_path / "case_a",
        checks=("actionlint", "workflow_shellcheck"),
        workflows={".github/workflows/broken.yml": SYNTAX_BROKEN_WORKFLOW},
    )
    plan_a, _ = _build_plan_and_expected(repo_a, [".github/workflows/broken.yml"])
    rep_a = execution.execute(plan_a, root=repo_a)
    by_name_a = {entry["name"]: entry for entry in rep_a["checks"]}
    assert by_name_a["actionlint"]["status"] == "failed"
    assert by_name_a["workflow_shellcheck"]["status"] == "not_executed"
    assert by_name_a["workflow_shellcheck"]["native_result"]["native_exit_code"] is None
    assert (
        by_name_a["workflow_shellcheck"]["reason"]
        == "actionlint_failed_without_shellcheck_verdict"
    )
    assert (
        by_name_a["workflow_shellcheck"]["coverage"]["workflow_inline_shell"] is False
    )

    # Case B: shellcheck_inline = false disables inline ShellCheck in ci-selection.toml ->
    # workflow_shellcheck becomes not_applicable (shellcheck_inline_disabled), NOT passed!
    repo_b, _ = _init_repo(
        tmp_path / "case_b",
        checks=("actionlint", "workflow_shellcheck"),
        workflows={".github/workflows/ci.yml": CLEAN_ZIZMOR_WORKFLOW},
        workflow_checks_extra="[workflow_checks]\nshellcheck_inline = false\n",
    )
    plan_b, _ = _build_plan_and_expected(repo_b, [".github/workflows/ci.yml"])
    scope_b = {item["name"]: item for item in plan_b["check_scope"]}
    assert scope_b["workflow_shellcheck"]["status"] == "not_applicable"
    assert scope_b["workflow_shellcheck"]["reason"] == "shellcheck_inline_disabled"
    rep_b = execution.execute(plan_b, root=repo_b)
    by_name_b = {entry["name"]: entry for entry in rep_b["checks"]}
    assert by_name_b["actionlint"]["status"] == "passed"
    assert "workflow_shellcheck" not in by_name_b

    # Case C: shellcheck binary / uvx unavailable fails closed as not_executed!
    repo_c, _ = _init_repo(
        tmp_path / "case_c",
        checks=("actionlint", "workflow_shellcheck"),
        workflows={".github/workflows/ci.yml": CLEAN_ZIZMOR_WORKFLOW},
    )
    plan_c, _ = _build_plan_and_expected(repo_c, [".github/workflows/ci.yml"])
    missing_sc_runner = _CountingRunner(
        overrides={
            (lambda argv: "--version" in argv and "shellcheck" in " ".join(argv)): (
                lambda argv: subprocess.CompletedProcess(
                    argv, 127, "", "shellcheck not installed"
                )
            ),
            (lambda argv: "-version" in argv and "actionlint" in " ".join(argv)): (
                lambda argv: subprocess.CompletedProcess(
                    argv, 0, workflow_analysis.ACTIONLINT_VERSION + "\n", ""
                )
            ),
        }
    )
    rep_c = execution.execute(plan_c, root=repo_c, runner=missing_sc_runner)
    by_name_c = {entry["name"]: entry for entry in rep_c["checks"]}
    assert by_name_c["workflow_shellcheck"]["status"] == "not_executed"
    assert by_name_c["workflow_shellcheck"]["reason"] == "shellcheck_unprepared"
    assert rep_c["status"] == "blocked"
    assert rep_c["exit_code"] == 5


def test_protected_admission_fail_closed_and_allowed(tmp_path):
    repo, _ = _init_repo(
        tmp_path,
        checks=("actionlint",),
        workflows={".github/workflows/ci.yml": CLEAN_ZIZMOR_WORKFLOW},
    )
    plan_doc, _ = _build_plan_and_expected(repo, [".github/workflows/ci.yml"])
    counting = _CountingRunner()

    # Protected run without admission state fails closed without running tool.
    plan_doc["protected_profile"] = "ci-security"
    blocked = execution.execute(plan_doc, root=repo, runner=counting)
    assert blocked["status"] == "blocked"
    assert blocked["exit_code"] == 5
    blocked_al = next(c for c in blocked["checks"] if c["name"] == "actionlint")
    assert blocked_al["status"] == "not_executed"
    assert blocked_al["reason"] == "policy_admission_missing"
    assert not any("actionlint" in " ".join(c) for c in counting.calls)

    # Protected run with explicit deny rule fails closed without running tool.
    now = dt.datetime(2026, 11, 1, 0, 0, tzinfo=dt.UTC)
    deny_adm = _write_policy_state(
        tmp_path / "deny",
        [
            {
                "rule_id": "deny-actionlint",
                "effect": "deny",
                "reason": "maintenance_block",
                "asset": {
                    "tool_id": "actionlint",
                    "access_id": "actionlint/check",
                    "version": workflow_analysis.ACTIONLINT_VERSION,
                    "platform": None,
                    "source": None,
                    "digest": None,
                },
            }
        ],
        now=now,
    )
    denied = execution.execute(
        plan_doc, root=repo, runner=counting, policy_admission=deny_adm
    )
    assert denied["status"] == "blocked"
    assert denied["exit_code"] == 5
    denied_al = next(c for c in denied["checks"] if c["name"] == "actionlint")
    assert denied_al["status"] == "not_executed"
    assert denied_al["reason"] == "matched_deny"
    assert not any("actionlint" in " ".join(c) for c in counting.calls)

    # Protected run with signed allow rule permits execution.
    allow_adm = _write_policy_state(
        tmp_path / "allow",
        [
            {
                "rule_id": "allow-actionlint",
                "effect": "allow",
                "reason": "approved",
                "asset": {
                    "tool_id": "actionlint",
                    "access_id": "actionlint/check",
                    "version": workflow_analysis.ACTIONLINT_VERSION,
                    "platform": {"os": "linux", "arch": "x86_64"},
                    "source": None,
                    "digest": None,
                },
            }
        ],
        now=now,
    )
    allowed = execution.execute(
        plan_doc, root=repo, runner=counting, policy_admission=allow_adm
    )
    assert allowed["status"] == "passed", allowed
    allowed_al = next(c for c in allowed["checks"] if c["name"] == "actionlint")
    assert allowed_al["status"] == "passed"


def test_omitted_workflow_check_from_plan_blocks_execution(tmp_path):
    repo, _ = _init_repo(
        tmp_path,
        checks=("actionlint", "zizmor"),
        workflows={".github/workflows/ci.yml": CLEAN_ZIZMOR_WORKFLOW},
    )
    plan_doc, _ = _build_plan_and_expected(repo, [".github/workflows/ci.yml"])
    # Simulate an incomplete planner omitting zizmor from check_scope/expected_checks
    # while ci-selection.toml (inputs.config) still requires zizmor.
    plan_doc["check_scope"] = [
        item for item in plan_doc["check_scope"] if item["name"] != "zizmor"
    ]
    plan_doc["selection"]["checks"] = [
        name for name in plan_doc["selection"]["checks"] if name != "zizmor"
    ]
    cfg_path = repo / "ci-selection.toml"
    hyg_path = repo / "ci-hygiene.toml"
    cfg = selection.load_config(cfg_path)
    hyg_cfg = hygiene.load_config(hyg_path)
    head_tree = _git(repo, "rev-parse", "HEAD^{tree}").strip()
    head_commit = _git(repo, "rev-parse", "HEAD").strip()
    cs = selection.ChangeSet(
        root=repo,
        base_ref="origin/main",
        head_ref="HEAD",
        base_commit=head_commit,
        head_commit=head_commit,
        tree=head_tree,
        paths=(".github/workflows/ci.yml",),
        worktree_dirty=False,
        complete=True,
        snapshot="worktree",
    )
    plan_doc["expected_checks"] = expected_checks.derive_expected_checks(
        cs,
        plan_doc["check_scope"],
        config_path=cfg_path,
        content_binding=plan_doc["git"].get("content_binding"),
        hygiene_config=hyg_cfg,
        hygiene_identity=hygiene.config_identity(hyg_path, hyg_cfg, root=repo),
        selection_config=cfg,
    )
    report = execution.execute(plan_doc, root=repo)
    assert report["status"] == "blocked"
    assert report["exit_code"] == 5
    assert report["reason"] == "configured_check_omitted_from_plan"


def test_p0_1_separate_shellcheck_and_workflow_shellcheck_native_results(tmp_path):
    # Counterexample 1: standalone .sh is CLEAN while workflow has inline SC2086.
    # Standalone shellcheck MUST pass with genuine exit=0 while workflow_shellcheck fails
    # with genuine exit=1 and valid independent CheckContract NativeResults.
    repo1, _ = _init_repo(
        tmp_path / "c1",
        checks=("shellcheck", "workflow_shellcheck"),
        workflows={".github/workflows/inline_sc.yml": INLINE_SC2086_WORKFLOW},
        shells={"scripts/clean.sh": '#!/bin/sh\necho "clean"\n'},
    )
    plan1, _ = _build_plan_and_expected(
        repo1, [".github/workflows/inline_sc.yml", "scripts/clean.sh"]
    )
    rep1 = execution.execute(plan1, root=repo1)
    assert rep1["status"] == "failed"
    by1 = {entry["name"]: entry for entry in rep1["checks"]}
    sc1 = by1["shellcheck"]
    wf_sc1 = by1["workflow_shellcheck"]
    assert sc1["status"] == "passed"
    assert sc1["native_result"]["native_exit_code"] == 0
    assert sc1["native_result"]["tool"]["access_id"] == "shellcheck/check"
    assert sc1["check_contract"]["status"] == "valid"
    check_contract.validate_result(sc1["native_result"])
    assert (
        check_contract.evaluate_check_satisfaction(
            check_contract.definition_for("shellcheck"), sc1["native_results"]
        )["status"]
        == "satisfied"
    )
    assert wf_sc1["status"] == "failed"
    assert wf_sc1["native_result"]["native_exit_code"] == 1
    assert (
        wf_sc1["native_result"]["tool"]["access_id"] == "actionlint/shellcheck-inline"
    )
    assert wf_sc1["check_contract"]["status"] == "valid"
    check_contract.validate_result(wf_sc1["native_result"])
    assert (
        check_contract.evaluate_check_satisfaction(
            check_contract.definition_for("workflow_shellcheck"),
            wf_sc1["native_results"],
        )["status"]
        == "unsatisfied"
    )

    # Counterexample 2: standalone .sh is CLEAN while workflow fails YAML syntax.
    # Standalone shellcheck passes (exit=0), workflow_shellcheck MUST stay not_executed
    # (exit=None) rather than sharing exit=0 or reusing non-zero exit.
    repo2, _ = _init_repo(
        tmp_path / "c2",
        checks=("actionlint", "shellcheck", "workflow_shellcheck"),
        workflows={".github/workflows/broken.yml": SYNTAX_BROKEN_WORKFLOW},
        shells={"scripts/clean.sh": '#!/bin/sh\necho "clean"\n'},
    )
    plan2, _ = _build_plan_and_expected(
        repo2, [".github/workflows/broken.yml", "scripts/clean.sh"]
    )
    rep2 = execution.execute(plan2, root=repo2)
    by2 = {entry["name"]: entry for entry in rep2["checks"]}
    assert by2["shellcheck"]["status"] == "passed"
    assert by2["shellcheck"]["native_result"]["native_exit_code"] == 0
    assert by2["actionlint"]["status"] == "failed"
    assert by2["workflow_shellcheck"]["status"] == "not_executed"
    assert by2["workflow_shellcheck"]["native_result"]["native_exit_code"] is None
    assert by2["workflow_shellcheck"]["check_contract"]["status"] == "valid"
    check_contract.validate_result(by2["workflow_shellcheck"]["native_result"])


def test_p0_2_composite_admission_and_shared_cache_revocation(tmp_path):
    now = dt.datetime(2026, 11, 1, 0, 0, tzinfo=dt.UTC)
    repo, _ = _init_repo(
        tmp_path / "repo",
        checks=("actionlint", "shellcheck", "workflow_shellcheck"),
        workflows={".github/workflows/ci.yml": CLEAN_ZIZMOR_WORKFLOW},
        shells={"scripts/ok.sh": '#!/bin/sh\necho "clean"\n'},
    )
    plan_doc, _ = _build_plan_and_expected(
        repo, [".github/workflows/ci.yml", "scripts/ok.sh"]
    )
    plan_doc["protected_profile"] = "ci-security"

    allow_al = {
        "rule_id": "allow-actionlint",
        "effect": "allow",
        "reason": "approved",
        "asset": {
            "tool_id": "actionlint",
            "access_id": "actionlint/check",
            "version": workflow_analysis.ACTIONLINT_VERSION,
            "platform": {"os": "linux", "arch": "x86_64"},
            "source": None,
            "digest": None,
        },
    }
    deny_al = {
        "rule_id": "deny-actionlint",
        "effect": "deny",
        "reason": "blocked",
        "asset": {
            "tool_id": "actionlint",
            "access_id": "actionlint/check",
            "version": workflow_analysis.ACTIONLINT_VERSION,
            "platform": None,
            "source": None,
            "digest": None,
        },
    }
    allow_sc = {
        "rule_id": "allow-shellcheck",
        "effect": "allow",
        "reason": "approved",
        "asset": {
            "tool_id": "shellcheck",
            "access_id": "shellcheck/check",
            "version": workflow_analysis.SHELLCHECK_VERSION,
            "platform": {"os": "linux", "arch": "x86_64"},
            "source": None,
            "digest": None,
        },
    }
    deny_sc = {
        "rule_id": "deny-shellcheck",
        "effect": "deny",
        "reason": "blocked",
        "asset": {
            "tool_id": "shellcheck",
            "access_id": "shellcheck/check",
            "version": workflow_analysis.SHELLCHECK_VERSION,
            "platform": None,
            "source": None,
            "digest": None,
        },
    }

    # Case A: actionlint allowed + shellcheck denied -> workflow_shellcheck and shellcheck
    # both fail closed before any subprocess/probe; actionlint runs ONLY with -shellcheck=.
    adm_a = _write_policy_state(tmp_path / "adm_a", [allow_al, deny_sc], now=now)
    calls_a = _CountingRunner()
    rep_a = execution.execute(
        plan_doc, root=repo, runner=calls_a, policy_admission=adm_a
    )
    by_a = {c["name"]: c for c in rep_a["checks"]}
    assert by_a["actionlint"]["status"] == "passed"
    assert (
        by_a["actionlint"]["native_result"]["evidence"]["coverage"][
            "shellcheck_inline_enabled"
        ]
        is False
    )
    assert by_a["workflow_shellcheck"]["status"] == "not_executed"
    assert by_a["workflow_shellcheck"]["reason"] == "matched_deny"
    assert by_a["shellcheck"]["status"] == "not_executed"
    assert by_a["shellcheck"]["reason"] == "matched_deny"
    assert not any("shellcheck-py" in " ".join(c) for c in calls_a.calls)
    assert not any(c and "shellcheck" in Path(c[0]).name for c in calls_a.calls)
    al_scans_a = [c for c in calls_a.calls if "-format" in c]
    assert al_scans_a and all("-shellcheck=" in c for c in al_scans_a)

    # Case B: workflow_shellcheck selected + actionlint denied + standalone shellcheck allowed ->
    # zero actionlint invocations, workflow_shellcheck blocked, standalone shellcheck passes!
    adm_b = _write_policy_state(tmp_path / "adm_b", [deny_al, allow_sc], now=now)
    calls_b = _CountingRunner()
    rep_b = execution.execute(
        plan_doc, root=repo, runner=calls_b, policy_admission=adm_b
    )
    by_b = {c["name"]: c for c in rep_b["checks"]}
    assert by_b["actionlint"]["status"] == "not_executed"
    assert by_b["actionlint"]["reason"] == "matched_deny"
    assert by_b["workflow_shellcheck"]["status"] == "not_executed"
    assert by_b["workflow_shellcheck"]["reason"] == "matched_deny"
    assert by_b["shellcheck"]["status"] == "passed"
    assert not any("actionlint" in " ".join(c) for c in calls_b.calls)

    # Case C: Cached facts in shared_cache from an allowed run MUST NOT be reused when
    # policy revokes permission in a subsequent run.
    adm_allow_both = _write_policy_state(
        tmp_path / "adm_both", [allow_al, allow_sc], now=now
    )
    wf_sc_item = next(
        c for c in plan_doc["check_scope"] if c["name"] == "workflow_shellcheck"
    )
    shared_cache: dict = {}
    calls_c1 = _CountingRunner()
    rep_c1 = execution.run_host_check(
        plan_doc,
        wf_sc_item,
        root=repo,
        runner=calls_c1,
        policy_admission=adm_allow_both,
        shared_cache=shared_cache,
    )
    assert rep_c1["status"] == "passed", rep_c1
    assert shared_cache  # Cache populated from allowed run.

    calls_c2 = _CountingRunner()
    rep_c2 = execution.run_host_check(
        plan_doc,
        wf_sc_item,
        root=repo,
        runner=calls_c2,
        policy_admission=adm_a,
        shared_cache=shared_cache,
    )
    assert rep_c2["status"] == "not_executed"
    assert rep_c2["reason"] == "matched_deny"
    assert not calls_c2.calls


def test_p1_1_actionlint_vs_workflow_shellcheck_independent_exit(tmp_path):
    # Scenario 1: workflow has ONLY inline SC2086 (no workflow syntax error) ->
    # actionlint MUST run independently with -shellcheck= and pass with genuine exit=0,
    # while workflow_shellcheck fails with exit=1 and SC2086.
    repo1, _ = _init_repo(
        tmp_path / "only_sc",
        checks=("actionlint", "workflow_shellcheck"),
        workflows={".github/workflows/inline_sc.yml": INLINE_SC2086_WORKFLOW},
    )
    plan1, _ = _build_plan_and_expected(repo1, [".github/workflows/inline_sc.yml"])
    calls1 = _CountingRunner()
    rep1 = execution.execute(plan1, root=repo1, runner=calls1)
    by1 = {c["name"]: c for c in rep1["checks"]}
    assert by1["actionlint"]["status"] == "passed"
    assert by1["actionlint"]["native_result"]["native_exit_code"] == 0
    assert (
        by1["actionlint"]["native_result"]["evidence"]["coverage"][
            "shellcheck_inline_enabled"
        ]
        is False
    )
    assert by1["workflow_shellcheck"]["status"] == "failed"
    assert by1["workflow_shellcheck"]["native_result"]["native_exit_code"] == 1
    assert any(
        f["rule"] == "shellcheck" and "SC2086" in f["message"]
        for f in by1["workflow_shellcheck"]["native_result"]["evidence"]["findings"]
    )
    al_scans = [c for c in calls1.calls if "-format" in c]
    assert len(al_scans) == 2
    assert any("-shellcheck=" not in c for c in al_scans)
    assert any("-shellcheck=" in c for c in al_scans)

    # Scenario 2: ONLY workflow syntax error -> actionlint fails, workflow_shellcheck not_executed.
    repo2, _ = _init_repo(
        tmp_path / "only_syntax",
        checks=("actionlint", "workflow_shellcheck"),
        workflows={".github/workflows/broken.yml": SYNTAX_BROKEN_WORKFLOW},
    )
    plan2, _ = _build_plan_and_expected(repo2, [".github/workflows/broken.yml"])
    rep2 = execution.execute(plan2, root=repo2)
    by2 = {c["name"]: c for c in rep2["checks"]}
    assert by2["actionlint"]["status"] == "failed"
    assert by2["actionlint"]["native_result"]["native_exit_code"] != 0
    assert by2["workflow_shellcheck"]["status"] == "not_executed"
    assert by2["workflow_shellcheck"]["native_result"]["native_exit_code"] is None

    # Scenario 3: BOTH workflow syntax error (bad runner/uses or step syntax) AND inline SC2086 ->
    # both actionlint and workflow_shellcheck fail independently!
    both_yml = """\
name: both
on:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  lint:
    runs-on: [self-hosted, unknown-label-never-valid]
    steps:
      - shell: bash
        run: echo $UNQUOTED
  bad_syntax:
    needs: missing-job
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""
    repo3, _ = _init_repo(
        tmp_path / "both",
        checks=("actionlint", "workflow_shellcheck"),
        workflows={".github/workflows/both.yml": both_yml},
    )
    plan3, _ = _build_plan_and_expected(repo3, [".github/workflows/both.yml"])
    rep3 = execution.execute(plan3, root=repo3)
    by3 = {c["name"]: c for c in rep3["checks"]}
    assert by3["actionlint"]["status"] == "failed"
    assert by3["actionlint"]["native_result"]["native_exit_code"] == 1
    assert by3["actionlint"]["native_result"]["evidence"]["findings"]
    assert by3["workflow_shellcheck"]["status"] == "failed"
    assert by3["workflow_shellcheck"]["native_result"]["native_exit_code"] == 1
    assert any(
        f["rule"] == "shellcheck" and "SC2086" in f["message"]
        for f in by3["workflow_shellcheck"]["native_result"]["evidence"]["findings"]
    )


def test_p1_2_config_only_triggers_and_expected_checks(tmp_path):
    def build_for_diff(
        repo: Path, base_commit: str, paths: list[str], *, full: bool = False
    ):
        cfg_path = repo / "ci-selection.toml"
        cfg = selection.load_config(cfg_path)
        hyg_path = repo / "ci-hygiene.toml"
        hyg_cfg = hygiene.load_config(hyg_path)
        head_commit = _git(repo, "rev-parse", "HEAD").strip()
        head_tree = _git(repo, "rev-parse", "HEAD^{tree}").strip()
        cs = selection.ChangeSet(
            root=repo,
            base_ref="origin/main",
            head_ref="HEAD",
            base_commit=base_commit,
            head_commit=head_commit,
            tree=head_tree,
            paths=tuple(paths),
            worktree_dirty=False,
            complete=True,
            snapshot="worktree",
        )
        sel = selection.select(cs, cfg, root=repo, full=full)
        plan_doc = selection.plan(
            sel,
            config=cfg,
            config_path=cfg_path,
            root=repo,
            hygiene_config=hyg_cfg,
            hygiene_config_path=hyg_path,
        )
        exp = expected_checks.derive_expected_checks(
            cs,
            plan_doc["check_scope"],
            config_path=cfg_path,
            consumer="dymzz/engineering-ci",
            hygiene_config=hyg_cfg,
            hygiene_identity=hygiene.config_identity(hyg_path, hyg_cfg, root=repo),
            selection_config=cfg,
        )
        return plan_doc, exp

    repo, base_sha = _init_repo(
        tmp_path,
        checks=("actionlint", "shellcheck", "workflow_shellcheck", "zizmor"),
        workflows={".github/workflows/ci.yml": CLEAN_ZIZMOR_WORKFLOW},
        shells={"scripts/ok.sh": '#!/bin/sh\necho "ok"\n'},
        workflow_checks_extra="[workflow_checks]\nshellcheck_inline = true\nzizmor_offline = true\n",
    )
    cfg_path = repo / "ci-selection.toml"
    base_cfg_text = cfg_path.read_text(encoding="utf-8")

    # Positive 1: config-only zizmor_persona change triggers zizmor (and preserves expected_checks)
    # while leaving actionlint/shellcheck/workflow_shellcheck not_applicable.
    cfg_path.write_text(
        base_cfg_text.replace(
            "zizmor_offline = true",
            'zizmor_offline = true\nzizmor_persona = "pedantic"',
        ),
        encoding="utf-8",
    )
    _git(repo, "add", "ci-selection.toml")
    _git(repo, "commit", "-q", "-m", "zizmor persona change")
    p1, e1 = build_for_diff(repo, base_sha, ["ci-selection.toml"])
    s1 = {item["name"]: item for item in p1["check_scope"]}
    assert s1["zizmor"]["status"] == "selected"
    assert s1["zizmor"]["inputs"] == [".github/workflows/ci.yml"]
    assert s1["actionlint"]["status"] == "not_applicable"
    assert s1["shellcheck"]["status"] == "not_applicable"
    assert s1["workflow_shellcheck"]["status"] == "not_applicable"
    e1_names = {entry["name"]: entry for entry in e1["checks"]}
    assert e1_names["zizmor"]["applicability"]["status"] == "selected"
    assert e1_names["zizmor"]["input_refs"] == [
        {"kind": "changed_path", "path": ".github/workflows/ci.yml"}
    ]

    # Negative (equivalence): changing only a comment in ci-selection.toml triggers NO checks.
    head1 = _git(repo, "rev-parse", "HEAD").strip()
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8") + "\n# harmless comment change\n",
        encoding="utf-8",
    )
    _git(repo, "add", "ci-selection.toml")
    _git(repo, "commit", "-q", "-m", "comment-only change")
    p_eq, e_eq = build_for_diff(repo, head1, ["ci-selection.toml"])
    s_eq = {item["name"]: item for item in p_eq["check_scope"]}
    for name in ("actionlint", "shellcheck", "workflow_shellcheck", "zizmor"):
        assert s_eq[name]["status"] == "not_applicable"
    e_eq_names = {entry["name"]: entry for entry in e_eq["checks"]}
    assert e_eq_names["zizmor"]["applicability"]["status"] == "not_applicable"

    # Positive 2: shellcheck_inline flipped false -> true triggers workflow_shellcheck only.
    cfg_path.write_text(
        base_cfg_text.replace("shellcheck_inline = true", "shellcheck_inline = false"),
        encoding="utf-8",
    )
    _git(repo, "add", "ci-selection.toml")
    _git(repo, "commit", "-q", "-m", "disable inline shellcheck")
    head_disabled = _git(repo, "rev-parse", "HEAD").strip()
    cfg_path.write_text(base_cfg_text, encoding="utf-8")
    _git(repo, "add", "ci-selection.toml")
    _git(repo, "commit", "-q", "-m", "re-enable inline shellcheck")
    p2, e2 = build_for_diff(repo, head_disabled, ["ci-selection.toml"])
    s2 = {item["name"]: item for item in p2["check_scope"]}
    assert s2["workflow_shellcheck"]["status"] == "selected"
    assert s2["workflow_shellcheck"]["inputs"] == [".github/workflows/ci.yml"]
    assert s2["actionlint"]["status"] == "not_applicable"
    assert s2["zizmor"]["status"] == "not_applicable"
    assert s2["shellcheck"]["status"] == "not_applicable"
    e2_names = {entry["name"]: entry for entry in e2["checks"]}
    assert e2_names["workflow_shellcheck"]["applicability"]["status"] == "selected"
    assert e2_names["workflow_shellcheck"]["input_refs"] == [
        {"kind": "changed_path", "path": ".github/workflows/ci.yml"}
    ]

    # Positive 3: full=True triggers all enabled workflow/script checks even with non-workflow diff.
    head_enabled = _git(repo, "rev-parse", "HEAD").strip()
    p3, _ = build_for_diff(repo, head_enabled, ["README.md"], full=True)
    s3 = {item["name"]: item for item in p3["check_scope"]}
    for name in ("actionlint", "shellcheck", "workflow_shellcheck", "zizmor"):
        assert s3[name]["status"] == "selected", (name, s3[name])
