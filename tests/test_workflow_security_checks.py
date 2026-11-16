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
    reconciliation,
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
      - shell: bash
        run: echo "ok"
"""
PWSH_ONLY_WORKFLOW = """\
name: pwsh-only-ci
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


def test_pwsh_only_and_unproven_debug_never_satisfy_workflow_shellcheck(tmp_path):
    # P0-1: pwsh-only workflow has zero applicable inline POSIX shell steps ->
    # actionlint passes, but workflow_shellcheck MUST report not_applicable (NOT passed)
    # and MUST return "unsatisfied" against an explicit Required ExpectedCheck.
    repo_pwsh, _ = _init_repo(
        tmp_path / "pwsh",
        checks=("actionlint", "workflow_shellcheck"),
        workflows={".github/workflows/pwsh.yml": PWSH_ONLY_WORKFLOW},
    )
    plan_pwsh, _ = _build_plan_and_expected(repo_pwsh, [".github/workflows/pwsh.yml"])
    rep_pwsh = execution.execute(plan_pwsh, root=repo_pwsh)
    by_pwsh = {c["name"]: c for c in rep_pwsh["checks"]}
    assert by_pwsh["actionlint"]["status"] == "passed"
    wf_sc = by_pwsh["workflow_shellcheck"]
    assert wf_sc["status"] == "not_applicable"
    assert wf_sc["reason"] == "no_applicable_inline_shell_steps"
    assert wf_sc["coverage"]["workflow_inline_shell"] is False
    assert wf_sc["coverage"]["inline_shell_steps_invoked"] == 0
    wf_sc_def = check_contract.definition_for("workflow_shellcheck")
    nr = wf_sc["native_result"]
    assert nr["native_exit_code"] == 0
    assert nr["status"] == "not_applicable"
    req_expected = {
        "check_id": wf_sc_def.check_id,
        "definition_version": wf_sc_def.definition_version,
        "rule_ref": nr["check"]["rule_ref"],
        "source_ref": {
            **nr["source_ref"],
            "binding_status": "content_digest",
            "scope_kind": "check_input_closure",
        },
    }
    satisfaction_no_exp = check_contract.evaluate_check_satisfaction(
        wf_sc_def, wf_sc["native_results"]
    )
    assert satisfaction_no_exp["status"] == "unsatisfied"
    satisfaction = check_contract.evaluate_check_satisfaction(
        wf_sc_def, wf_sc["native_results"], expected=req_expected
    )
    assert satisfaction["status"] == "unsatisfied"

    # P0-2: If actionlint -debug output lacks shellcheck execution proof -> fail closed not_executed.
    repo_unproven, _ = _init_repo(
        tmp_path / "unproven",
        checks=("actionlint", "workflow_shellcheck"),
        workflows={".github/workflows/bash.yml": CLEAN_ZIZMOR_WORKFLOW},
    )
    plan_unp, _ = _build_plan_and_expected(
        repo_unproven, [".github/workflows/bash.yml"]
    )
    unproven_runner = _CountingRunner(
        overrides={
            (lambda argv: "-version" in argv and "actionlint" in " ".join(argv)): (
                lambda argv: subprocess.CompletedProcess(
                    argv, 0, f"{workflow_analysis.ACTIONLINT_VERSION}\n", ""
                )
            ),
            (lambda argv: "--version" in argv and "shellcheck" in " ".join(argv)): (
                lambda argv: subprocess.CompletedProcess(
                    argv, 0, f"version: {workflow_analysis.SHELLCHECK_VERSION}\n", ""
                )
            ),
            (lambda argv: "actionlint" in " ".join(argv) and "-format" in argv): (
                lambda argv: subprocess.CompletedProcess(argv, 0, "[]\n", "")
            ),
        }
    )
    rep_unp = execution.execute(plan_unp, root=repo_unproven, runner=unproven_runner)
    by_unp = {c["name"]: c for c in rep_unp["checks"]}
    wf_unp = by_unp["workflow_shellcheck"]
    assert wf_unp["status"] == "not_executed"
    assert wf_unp["reason"] == "shellcheck_execution_unproven"
    assert wf_unp["native_result"]["native_exit_code"] is None
    assert rep_unp["status"] == "blocked"


def test_shellcheck_rcfile_isolation_and_config_tracking(tmp_path):
    # Case A: ambient .shellcheckrc exists, shellcheck_config is NOT declared ->
    # execution MUST pass --norc so ambient .shellcheckrc cannot weaken standalone shellcheck.
    repo_amb, _ = _init_repo(
        tmp_path / "ambient",
        checks=("shellcheck",),
        shells={"scripts/warn.sh": '#!/bin/sh\nval="hello world"\necho $val\n'},
    )
    (repo_amb / ".shellcheckrc").write_text(
        "disable=SC2086\n", encoding="utf-8", newline="\n"
    )
    _git(repo_amb, "add", ".shellcheckrc")
    _git(repo_amb, "commit", "-q", "-m", "add ambient shellcheckrc")
    plan_amb, _ = _build_plan_and_expected(repo_amb, ["scripts/warn.sh"])
    calls_amb = _CountingRunner()
    rep_amb = execution.execute(plan_amb, root=repo_amb, runner=calls_amb)
    by_amb = {c["name"]: c for c in rep_amb["checks"]}
    assert by_amb["shellcheck"]["status"] == "failed"
    assert by_amb["shellcheck"]["native_result"]["native_exit_code"] == 1
    assert any(
        f["rule"] == "SC2086"
        for f in by_amb["shellcheck"]["native_result"]["evidence"]["findings"]
    )
    sc_scans_amb = [c for c in calls_amb.calls if "--format=json1" in c]
    assert sc_scans_amb and all("--norc" in c for c in sc_scans_amb)

    # Case B: declared shellcheck_config = ".shellcheckrc" passes --rcfile=.shellcheckrc,
    # applies its rule, AND triggers shellcheck + expected_checks when .shellcheckrc changes.
    repo_cfg, base_sha = _init_repo(
        tmp_path / "declared",
        checks=("actionlint", "shellcheck", "workflow_shellcheck", "zizmor"),
        workflows={".github/workflows/ci.yml": CLEAN_ZIZMOR_WORKFLOW},
        shells={"scripts/warn.sh": '#!/bin/sh\nval="hello world"\necho $val\n'},
        workflow_checks_extra=(
            '[[non_code_rules]]\nprefix = ".shellcheckrc"\ntests = []\n'
            'reason = "shellcheck config"\n\n'
            '[workflow_checks]\nshellcheck_config = ".shellcheckrc"\n'
        ),
    )
    (repo_cfg / ".shellcheckrc").write_text(
        "disable=SC2086\n", encoding="utf-8", newline="\n"
    )
    _git(repo_cfg, "add", ".shellcheckrc")
    _git(repo_cfg, "commit", "-q", "-m", "add declared shellcheckrc")

    cfg_path = repo_cfg / "ci-selection.toml"
    cfg = selection.load_config(cfg_path)
    hyg_path = repo_cfg / "ci-hygiene.toml"
    hyg_cfg = hygiene.load_config(hyg_path)
    head_commit = _git(repo_cfg, "rev-parse", "HEAD").strip()
    head_tree = _git(repo_cfg, "rev-parse", "HEAD^{tree}").strip()
    cs = selection.ChangeSet(
        root=repo_cfg,
        base_ref="origin/main",
        head_ref="HEAD",
        base_commit=base_sha,
        head_commit=head_commit,
        tree=head_tree,
        paths=(".shellcheckrc",),
        worktree_dirty=False,
        complete=True,
        snapshot="worktree",
    )
    sel = selection.select(cs, cfg, root=repo_cfg)
    plan_rc = selection.plan(
        sel,
        config=cfg,
        config_path=cfg_path,
        root=repo_cfg,
        hygiene_config=hyg_cfg,
        hygiene_config_path=hyg_path,
    )
    exp_rc = expected_checks.derive_expected_checks(
        cs,
        plan_rc["check_scope"],
        config_path=cfg_path,
        consumer="dymzz/engineering-ci",
        hygiene_config=hyg_cfg,
        hygiene_identity=hygiene.config_identity(hyg_path, hyg_cfg, root=repo_cfg),
        selection_config=cfg,
    )
    scope_rc = {item["name"]: item for item in plan_rc["check_scope"]}
    assert scope_rc["shellcheck"]["status"] == "selected"
    assert scope_rc["shellcheck"]["inputs"] == ["scripts/warn.sh"]
    assert scope_rc["actionlint"]["status"] == "not_applicable"
    assert scope_rc["workflow_shellcheck"]["status"] == "not_applicable"
    assert scope_rc["zizmor"]["status"] == "not_applicable"
    exp_sc = next(entry for entry in exp_rc["checks"] if entry["name"] == "shellcheck")
    assert exp_sc["applicability"]["status"] == "selected"
    assert exp_sc["input_refs"] == [{"kind": "changed_path", "path": "scripts/warn.sh"}]

    calls_rc = _CountingRunner()
    rep_rc = execution.execute(plan_rc, root=repo_cfg, runner=calls_rc)
    assert rep_rc["status"] == "passed", (
        rep_rc.get("reason"),
        rep_rc.get("tests"),
        [
            (c["name"], c["status"], c.get("reason"), c.get("findings"))
            for c in rep_rc["checks"]
        ],
    )
    by_rc = {c["name"]: c for c in rep_rc["checks"]}
    assert by_rc["shellcheck"]["status"] == "passed"
    assert by_rc["shellcheck"]["native_result"]["native_exit_code"] == 0
    sc_scans_rc = [c for c in calls_rc.calls if "--format=json1" in c]
    assert sc_scans_rc and all("--rcfile=.shellcheckrc" in c for c in sc_scans_rc)


def test_scoped_execution_scans_and_reports_only_selected_inputs(tmp_path):
    # Repo contains clean modified workflow a.yml + ok.sh AND untouched failing
    # workflow b.yml (zizmor unpinned-uses + SC2086 + syntax error) + bad.sh (SC2086).
    # Incremental selection for a.yml + ok.sh MUST pass only a.yml / ok.sh to
    # native tools, NativeResult.input_refs, ExpectedChecks, and Report.
    repo, _ = _init_repo(
        tmp_path,
        checks=("actionlint", "shellcheck", "workflow_shellcheck", "zizmor"),
        workflows={
            ".github/workflows/a.yml": CLEAN_ZIZMOR_WORKFLOW,
            ".github/workflows/b.yml": UNPINNED_VALID_ACTIONLINT_WORKFLOW.replace(
                'echo "ok"', "echo $UNQUOTED"
            ),
        },
        shells={
            "scripts/ok.sh": '#!/bin/sh\necho "clean"\n',
            "scripts/bad.sh": "#!/bin/sh\necho $BAD_VAR\n",
        },
    )
    plan_doc, exp_doc = _build_plan_and_expected(
        repo, [".github/workflows/a.yml", "scripts/ok.sh"]
    )
    exp_by = {entry["name"]: entry for entry in exp_doc["checks"]}
    for name in ("actionlint", "workflow_shellcheck", "zizmor"):
        assert exp_by[name]["input_refs"] == [
            {"kind": "changed_path", "path": ".github/workflows/a.yml"}
        ]
    assert exp_by["shellcheck"]["input_refs"] == [
        {"kind": "changed_path", "path": "scripts/ok.sh"}
    ]

    calls = _CountingRunner()
    report = execution.execute(plan_doc, root=repo, runner=calls)
    assert report["status"] == "passed", report
    by_name = {entry["name"]: entry for entry in report["checks"]}
    for name in ("actionlint", "workflow_shellcheck", "zizmor"):
        assert by_name[name]["status"] == "passed"
        ref_paths = {
            ref["path"]
            for ref in by_name[name]["native_result"]["input_refs"]
            if ref.get("kind") == "changed_path"
        }
        assert ref_paths == {".github/workflows/a.yml"}
    sc_paths = {
        ref["path"]
        for ref in by_name["shellcheck"]["native_result"]["input_refs"]
        if ref.get("kind") == "changed_path"
    }
    assert sc_paths == {"scripts/ok.sh"}

    al_scans = [c for c in calls.calls if "-format" in c]
    sc_scans = [c for c in calls.calls if "--format=json1" in c]
    zz_scans = [c for c in calls.calls if "--format" in c and "json" in c]
    assert al_scans and all(
        ".github/workflows/a.yml" in c and ".github/workflows/b.yml" not in c
        for c in al_scans
    )
    assert sc_scans and all(
        "scripts/ok.sh" in c and "scripts/bad.sh" not in c for c in sc_scans
    )
    assert zz_scans and all(
        ".github/workflows/a.yml" in c and ".github/workflows/b.yml" not in c
        for c in zz_scans
    )


def test_profile_to_native_result_to_expected_strict_binding(tmp_path):
    from test_reconciliation import (
        ATTEMPT_QUALIFICATION_REF,
        ATTEMPT_REF,
        BASELINE_SOURCE_REF,
        JOB_ISSUER_REF,
        RULE_SOURCE_REF,
        RUN_REF,
        RUNNER_REF,
        SOURCE_RECEIPT_REF,
        report_ref_for,
    )

    # Positive E2E: Authority-verified EffectiveProfile v2 -> Plan/CheckScope/Expected ->
    # real native execution (whitespace + actionlint + shellcheck + workflow_shellcheck + zizmor) ->
    # evaluate_expected_satisfaction & #104 reconcile_expected_results.
    repo, _ = _init_repo(
        tmp_path / "verified",
        workflows={".github/workflows/ci.yml": CLEAN_ZIZMOR_WORKFLOW},
        shells={"scripts/ok.sh": '#!/bin/sh\necho "clean"\n'},
    )
    declared = [
        "whitespace",
        "actionlint",
        "shellcheck",
        "workflow_shellcheck",
        "zizmor",
    ]
    prof_ref_v1 = check_contract.profile_ref("eci/default", "1.0.0")
    profile = expected_checks.build_effective_profile(
        profile_ref=prof_ref_v1,
        profile_revision="rev-1",
        approval_source="governance-signed",
        effective_at="2026-11-15T00:00:00Z",
        evaluated_at="2026-11-15T00:00:00Z",
        context="worktree",
        action="worktree",
        target="HEAD",
        declared_checks=declared,
        required_obligations=[
            {"obligation_ref": f"req-{name}", "check_names": [name]}
            for name in declared
        ],
        domain_complete=True,
        authority_verified=True,
    )
    binding = profile["approval_binding"]
    cfg_path = repo / "ci-selection.toml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8", newline="\n")
        + f"""
[effective_profile]
profile_ref = {{ name = "{prof_ref_v1["name"]}", version = "{prof_ref_v1["version"]}" }}
profile_revision = "{profile["profile_revision"]}"
approval_source = "{binding["approval_source"]}"
approval_digest = "{binding["approval_digest"]}"
effective_at = "{profile["effective_at"]}"
declared_checks = {json.dumps(declared)}
""",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", "ci-selection.toml")
    _git(repo, "commit", "-q", "-m", "bind effective profile policy")

    plan_v2, exp_v2 = _build_plan_and_expected(
        repo,
        [".github/workflows/ci.yml", "scripts/ok.sh"],
        effective_profile=profile,
    )
    assert exp_v2["schema_version"] == 2
    assert exp_v2["authority_status"] == "verified"
    assert exp_v2["status"] == "complete"

    report = execution.execute(plan_v2, root=repo)
    assert report["status"] == "passed", report
    by_name = {entry["name"]: entry for entry in report["checks"]}

    # Common Execution Producer binds verified profile_ref onto every Check family
    # while keeping Rule, Source, and Input identities intact.
    for name in declared:
        nr = by_name[name]["native_result"]
        assert nr["check"]["profile_ref"] == prof_ref_v1
        assert nr["check"]["rule_ref"]["sha256"]
        assert nr["source_ref"]["binding_status"] == "content_digest"
        if name in {"actionlint", "workflow_shellcheck", "zizmor"}:
            assert {"kind": "changed_path", "path": ".github/workflows/ci.yml"} in nr[
                "input_refs"
            ]
        elif name == "shellcheck":
            assert {"kind": "changed_path", "path": "scripts/ok.sh"} in nr["input_refs"]

    # Closure-scoped Expected satisfaction + #104 Reconciliation using actual executed results.
    exp_by_name = {entry["name"]: entry for entry in exp_v2["checks"]}
    al_nr = by_name["actionlint"]["native_result"]
    closure_source = {
        **al_nr["source_ref"],
        "scope_kind": "check_input_closure",
    }
    strict_al = {
        "check_id": al_nr["check"]["check_id"],
        "definition_version": al_nr["check"]["definition_version"],
        "rule_ref": al_nr["check"]["rule_ref"],
        "profile_ref": exp_by_name["actionlint"]["profile_ref"],
        "source_ref": closure_source,
        "input_refs": al_nr["input_refs"],
    }
    # Actual unmodified host worktree result matches profile/rule/input identities under
    # #52 result_matches_expected when compared against worktree source_ref, and refuses
    # a mismatched profile_ref.
    assert check_contract.result_matches_expected(
        al_nr,
        {**strict_al, "source_ref": al_nr["source_ref"]},
    )
    assert not check_contract.result_matches_expected(
        al_nr,
        {
            **strict_al,
            "source_ref": al_nr["source_ref"],
            "profile_ref": check_contract.profile_ref("eci/default", "2.0.0"),
        },
    )

    # Raw unmodified execution.execute() NativeResults already bind profile_ref,
    # per-check effective rule_ref, and input_refs; passing them unmodified against
    # a #66-P0 check_input_closure resolution stops ONLY at source_mismatch because
    # host worktree execution emits scope_kind="changed_paths" until #66-P0 Closure
    # lands upstream.
    closure_source = {
        **by_name["actionlint"]["native_result"]["source_ref"],
        "scope_kind": "check_input_closure",
    }
    raw_results = [by_name[name]["native_result"] for name in declared]
    raw_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [{"name": "raw", "native_results": raw_results}],
    }
    raw_report_ref = report_ref_for(raw_report)
    raw_observations = [
        {
            "job_ref": "job-1",
            "run_ref": RUN_REF,
            "attempt_ref": ATTEMPT_REF,
            "runner_ref": RUNNER_REF,
            "report": raw_report,
            "report_ref": raw_report_ref,
            "native_results": raw_results,
            "job_receipt": {
                "kind": reconciliation.JOB_RECEIPT_KIND,
                "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
                "job_ref": "job-1",
                "run_ref": RUN_REF,
                "attempt_ref": ATTEMPT_REF,
                "runner_ref": RUNNER_REF,
                "report_ref": raw_report_ref,
                "issuer_ref": dict(JOB_ISSUER_REF),
                "verification_status": "upstream_verified",
            },
        }
    ]
    closure_resolutions_for_raw = []
    closure_routings_for_raw = []
    for name in declared:
        orig_nr = by_name[name]["native_result"]
        entry = exp_by_name[name]
        res_item = {
            "expected_check_ref": entry["expected_check_ref"],
            "rule_ref": orig_nr["check"]["rule_ref"],
            "source_ref": closure_source,
            "baseline_ref": orig_nr.get("baseline_ref"),
            "input_refs": orig_nr["input_refs"],
            "rule_source_ref": dict(RULE_SOURCE_REF),
            "source_receipt_ref": dict(SOURCE_RECEIPT_REF),
        }
        if orig_nr.get("baseline_ref") is not None:
            res_item["baseline_source_ref"] = dict(BASELINE_SOURCE_REF)
        closure_resolutions_for_raw.append(res_item)
        closure_routings_for_raw.append(
            {
                "expected_check_ref": entry["expected_check_ref"],
                "job_refs": ["job-1"],
                "job_instances": [
                    {
                        "job_ref": "job-1",
                        "run_ref": RUN_REF,
                        "attempt_ref": ATTEMPT_REF,
                        "runner_ref": RUNNER_REF,
                        "attempt_eligibility": "eligible",
                        "qualification_ref": dict(ATTEMPT_QUALIFICATION_REF),
                    }
                ],
                "mapping_status": "routed",
                "scheduler_ref": {"name": "lefthook", "version": "2.2.2"},
                "effective_config_ref": {"name": "hook-job", "version": "1"},
                "reason": None,
            }
        )
    raw_vs_closure = reconciliation.reconcile_expected_results(
        exp_v2,
        routing=closure_routings_for_raw,
        resolutions=closure_resolutions_for_raw,
        observations=raw_observations,
    )
    raw_by_name = {item["name"]: item for item in raw_vs_closure["entries"]}
    for wf_check in ("actionlint", "shellcheck", "workflow_shellcheck", "zizmor"):
        assert raw_by_name[wf_check]["binding_status"] == "insufficient"
        assert raw_by_name[wf_check]["reason"] == "execution_object_mismatch"
        assert (
            raw_by_name[wf_check]["rejected_results"][0]["reason"] == "source_mismatch"
        )

    # Simulated #66-P0 Source Closure handoff: upgrading only source_ref to
    # scope_kind="check_input_closure" proves full #52 satisfaction + #104 matched.
    closure_results_by_name = {}
    resolutions = []
    routings = []
    all_closure_results = []
    for name in declared:
        orig_nr = by_name[name]["native_result"]
        entry = exp_by_name[name]
        defn = check_contract.definition_for(name)
        closed_nr = check_contract.native_result(
            defn,
            source=closure_source,
            status=orig_nr["status"],
            result_origin=orig_nr["result_origin"],
            result_instance=orig_nr["result_instance"],
            native_exit_code=orig_nr["native_exit_code"],
            input_refs=orig_nr["input_refs"],
            coverage=orig_nr.get("coverage"),
            rule_ref=orig_nr["check"]["rule_ref"],
            profile_ref=orig_nr["check"]["profile_ref"],
            evidence=orig_nr["evidence"],
            runner_ref=RUNNER_REF,
            baseline_ref=orig_nr.get("baseline_ref"),
            tool=orig_nr.get("tool"),
        )
        closure_results_by_name[name] = closed_nr
        all_closure_results.append(closed_nr)
        res_item = {
            "expected_check_ref": entry["expected_check_ref"],
            "rule_ref": orig_nr["check"]["rule_ref"],
            "source_ref": closure_source,
            "baseline_ref": orig_nr.get("baseline_ref"),
            "input_refs": orig_nr["input_refs"],
            "rule_source_ref": dict(RULE_SOURCE_REF),
            "source_receipt_ref": dict(SOURCE_RECEIPT_REF),
        }
        if orig_nr.get("baseline_ref") is not None:
            res_item["baseline_source_ref"] = dict(BASELINE_SOURCE_REF)
        resolutions.append(res_item)
        routings.append(
            {
                "expected_check_ref": entry["expected_check_ref"],
                "job_refs": ["job-1"],
                "job_instances": [
                    {
                        "job_ref": "job-1",
                        "run_ref": RUN_REF,
                        "attempt_ref": ATTEMPT_REF,
                        "runner_ref": RUNNER_REF,
                        "attempt_eligibility": "eligible",
                        "qualification_ref": dict(ATTEMPT_QUALIFICATION_REF),
                    }
                ],
                "mapping_status": "routed",
                "scheduler_ref": {"name": "lefthook", "version": "2.2.2"},
                "effective_config_ref": {"name": "hook-job", "version": "1"},
                "reason": None,
            }
        )

    for wf_check in ("actionlint", "shellcheck", "workflow_shellcheck", "zizmor"):
        nr_w = closure_results_by_name[wf_check]
        sat = check_contract.evaluate_expected_satisfaction(
            [nr_w],
            {
                "check_id": nr_w["check"]["check_id"],
                "definition_version": nr_w["check"]["definition_version"],
                "rule_ref": nr_w["check"]["rule_ref"],
                "profile_ref": exp_by_name[wf_check]["profile_ref"],
                "source_ref": closure_source,
                "input_refs": nr_w["input_refs"],
            },
        )
        assert sat["status"] == "satisfied", (wf_check, sat)

    rec_report = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [{"name": "reconciled", "native_results": all_closure_results}],
    }
    rec_report_ref = report_ref_for(rec_report)
    rec_observation = {
        "job_ref": "job-1",
        "run_ref": RUN_REF,
        "attempt_ref": ATTEMPT_REF,
        "runner_ref": RUNNER_REF,
        "report": rec_report,
        "report_ref": rec_report_ref,
        "native_results": all_closure_results,
        "job_receipt": {
            "kind": reconciliation.JOB_RECEIPT_KIND,
            "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
            "job_ref": "job-1",
            "run_ref": RUN_REF,
            "attempt_ref": ATTEMPT_REF,
            "runner_ref": RUNNER_REF,
            "report_ref": rec_report_ref,
            "issuer_ref": dict(JOB_ISSUER_REF),
            "verification_status": "upstream_verified",
        },
    }
    matched_receipt = reconciliation.reconcile_expected_results(
        exp_v2,
        routing=routings,
        resolutions=resolutions,
        observations=[rec_observation],
    )
    matched_by_name = {item["name"]: item for item in matched_receipt["entries"]}
    for wf_check in ("actionlint", "shellcheck", "workflow_shellcheck", "zizmor"):
        assert matched_by_name[wf_check]["binding_status"] == "matched", (
            wf_check,
            matched_by_name[wf_check],
        )
    assert matched_receipt["summary"]["matched"] >= 4

    # Negative 1: Profile without [effective_profile] policy proof (authority_status == "unverified") ->
    # Execution Producer MUST keep native profile_ref None and strict satisfaction/reconciliation MUST fail.
    repo_unauth, _ = _init_repo(
        tmp_path / "unauth",
        workflows={".github/workflows/ci.yml": CLEAN_ZIZMOR_WORKFLOW},
        shells={"scripts/ok.sh": '#!/bin/sh\necho "clean"\n'},
    )
    rules_unauth = expected_checks.governed_domain_rules(
        repo_unauth, config_path=repo_unauth / "ci-selection.toml"
    )
    unauth_profile = expected_checks.build_effective_profile(
        profile_ref=prof_ref_v1,
        profile_revision="rev-1",
        approval_source="governance-signed",
        effective_at="2026-11-15T00:00:00Z",
        evaluated_at="2026-11-15T00:00:00Z",
        context="worktree",
        action="worktree",
        target="HEAD",
        declared_checks=declared,
        required_obligations=[
            {"obligation_ref": f"req-{name}", "check_names": [name]}
            for name in declared
        ],
        governed_rules=rules_unauth,
        domain_complete=True,
        authority_verified=True,
    )
    plan_unauth, exp_unauth = _build_plan_and_expected(
        repo_unauth,
        [".github/workflows/ci.yml", "scripts/ok.sh"],
        effective_profile=unauth_profile,
    )
    assert exp_unauth["authority_status"] == "unverified"
    rep_unauth = execution.execute(plan_unauth, root=repo_unauth)
    by_unauth = {entry["name"]: entry for entry in rep_unauth["checks"]}
    for name in declared:
        assert by_unauth[name]["native_result"]["check"]["profile_ref"] is None

    unauth_nr = by_unauth["actionlint"]["native_result"]
    unauth_sat = check_contract.evaluate_expected_satisfaction(
        [unauth_nr],
        {
            "check_id": unauth_nr["check"]["check_id"],
            "definition_version": unauth_nr["check"]["definition_version"],
            "rule_ref": unauth_nr["check"]["rule_ref"],
            "profile_ref": prof_ref_v1,
            "source_ref": {
                **unauth_nr["source_ref"],
                "scope_kind": "check_input_closure",
            },
        },
    )
    assert unauth_sat["status"] == "unsatisfied"

    al_closure_unauth = {
        **unauth_nr["source_ref"],
        "scope_kind": "check_input_closure",
    }
    al_unauth_closed = check_contract.native_result(
        check_contract.definition_for("actionlint"),
        source=al_closure_unauth,
        status=unauth_nr["status"],
        result_origin=unauth_nr["result_origin"],
        result_instance=unauth_nr["result_instance"],
        native_exit_code=unauth_nr["native_exit_code"],
        input_refs=unauth_nr["input_refs"],
        coverage=unauth_nr.get("coverage"),
        rule_ref=unauth_nr["check"]["rule_ref"],
        profile_ref=None,
        evidence=unauth_nr["evidence"],
        runner_ref=RUNNER_REF,
        tool=unauth_nr.get("tool"),
    )
    unauth_exp_al = next(e for e in exp_unauth["checks"] if e["name"] == "actionlint")
    unauth_rec_rep = {
        "kind": reconciliation.EXECUTION_REPORT_KIND,
        "schema_version": 1,
        "checks": [{"name": "reconciled", "native_results": [al_unauth_closed]}],
    }
    unauth_rec_ref = report_ref_for(unauth_rec_rep)
    unauth_rec = reconciliation.reconcile_expected_results(
        exp_unauth,
        routing=[
            {
                "expected_check_ref": unauth_exp_al["expected_check_ref"],
                "job_refs": ["job-1"],
                "job_instances": [
                    {
                        "job_ref": "job-1",
                        "run_ref": RUN_REF,
                        "attempt_ref": ATTEMPT_REF,
                        "runner_ref": RUNNER_REF,
                        "attempt_eligibility": "eligible",
                        "qualification_ref": dict(ATTEMPT_QUALIFICATION_REF),
                    }
                ],
                "mapping_status": "routed",
                "scheduler_ref": {"name": "lefthook", "version": "2.2.2"},
                "effective_config_ref": {"name": "hook-job", "version": "1"},
                "reason": None,
            }
        ],
        resolutions=[
            {
                "expected_check_ref": unauth_exp_al["expected_check_ref"],
                "rule_ref": unauth_nr["check"]["rule_ref"],
                "source_ref": al_closure_unauth,
                "input_refs": unauth_nr["input_refs"],
                "rule_source_ref": dict(RULE_SOURCE_REF),
                "source_receipt_ref": dict(SOURCE_RECEIPT_REF),
            }
        ],
        observations=[
            {
                "job_ref": "job-1",
                "run_ref": RUN_REF,
                "attempt_ref": ATTEMPT_REF,
                "runner_ref": RUNNER_REF,
                "report": unauth_rec_rep,
                "report_ref": unauth_rec_ref,
                "native_results": [al_unauth_closed],
                "job_receipt": {
                    "kind": reconciliation.JOB_RECEIPT_KIND,
                    "schema_version": reconciliation.JOB_RECEIPT_SCHEMA_VERSION,
                    "job_ref": "job-1",
                    "run_ref": RUN_REF,
                    "attempt_ref": ATTEMPT_REF,
                    "runner_ref": RUNNER_REF,
                    "report_ref": unauth_rec_ref,
                    "issuer_ref": dict(JOB_ISSUER_REF),
                    "verification_status": "upstream_verified",
                },
            }
        ],
    )
    unauth_al_entry = next(
        e for e in unauth_rec["entries"] if e["name"] == "actionlint"
    )
    assert unauth_al_entry["binding_status"] == "unknown"
    assert unauth_al_entry["reason"] == "expected_profile_binding_missing"
    assert unauth_al_entry["rejected_results"][0]["reason"] == "profile_binding_missing"

    # Negative 2: Expected binds a different Profile version (2.0.0 vs 1.0.0) ->
    # reconciliation refuses with expected_profile_mismatch and never returns matched.
    prof_ref_v2 = check_contract.profile_ref("eci/default", "2.0.0")
    profile_other = expected_checks.build_effective_profile(
        profile_ref=prof_ref_v2,
        profile_revision="rev-2",
        approval_source="governance-signed",
        effective_at="2026-11-15T00:00:00Z",
        evaluated_at="2026-11-15T00:00:00Z",
        context="worktree",
        action="worktree",
        target="HEAD",
        declared_checks=declared,
        required_obligations=[
            {"obligation_ref": f"req-{name}", "check_names": [name]}
            for name in declared
        ],
        domain_complete=True,
        authority_verified=True,
    )
    hyg_path = repo / "ci-hygiene.toml"
    hyg_cfg = hygiene.load_config(hyg_path)
    other_cs = selection.ChangeSet(
        root=repo,
        base_ref="origin/main",
        head_ref="HEAD",
        base_commit=plan_v2["git"]["base_commit"],
        head_commit=plan_v2["git"]["head_commit"],
        tree=plan_v2["git"]["tree"],
        paths=(".github/workflows/ci.yml", "scripts/ok.sh"),
        worktree_dirty=False,
        complete=True,
        snapshot="worktree",
    )
    exp_v2_other = expected_checks.derive_expected_checks(
        other_cs,
        plan_v2["check_scope"],
        config_path=cfg_path,
        consumer="dymzz/engineering-ci",
        content_binding=plan_v2["git"]["content_binding"],
        hygiene_config=hyg_cfg,
        hygiene_identity=hygiene.config_identity(hyg_path, hyg_cfg, root=repo),
        effective_profile=profile_other,
    )
    other_ref_by_name = {
        e["name"]: e["expected_check_ref"] for e in exp_v2_other["checks"]
    }
    orig_name_by_ref = {e["expected_check_ref"]: e["name"] for e in exp_v2["checks"]}
    other_routings = [
        {
            **r,
            "expected_check_ref": other_ref_by_name[
                orig_name_by_ref[r["expected_check_ref"]]
            ],
        }
        for r in routings
    ]
    other_resolutions = [
        {
            **res,
            "expected_check_ref": other_ref_by_name[
                orig_name_by_ref[res["expected_check_ref"]]
            ],
        }
        for res in resolutions
    ]
    mismatch_rec = reconciliation.reconcile_expected_results(
        exp_v2_other,
        routing=other_routings,
        resolutions=other_resolutions,
        observations=[rec_observation],
    )
    assert mismatch_rec["status"] != "matched"
    mismatch_by_name = {item["name"]: item for item in mismatch_rec["entries"]}
    for wf_check in ("actionlint", "shellcheck", "workflow_shellcheck", "zizmor"):
        item = mismatch_by_name[wf_check]
        assert item["binding_status"] == "unknown"
        assert item["reason"] == "expected_profile_mismatch"
        assert item["rejected_results"][0]["reason"] == "profile_mismatch"

    # Negative 3: Spoofed profile_ref override without Plan/inputs/config proof is rejected,
    # while verified Dagger host-adapter narrowing retains verified profile_ref.
    assert execution._profile_reference(plan_unauth, profile_ref=prof_ref_v1) is None
    narrowed_valid = json.loads(json.dumps(plan_v2))
    narrowed_valid.pop("expected_checks", None)
    assert (
        execution._profile_reference(narrowed_valid, profile_ref=prof_ref_v1)
        == prof_ref_v1
    )
    narrowed_valid["inputs"]["config"]["normalized"]["effective_profile"] = None
    assert execution._profile_reference(narrowed_valid, profile_ref=prof_ref_v1) is None


def test_tool_config_change_updates_effective_rule_ref_without_selection_edit(tmp_path):
    repo, _ = _init_repo(
        tmp_path / "rule_digest",
        workflows={".github/workflows/ci.yml": CLEAN_ZIZMOR_WORKFLOW},
        shells={"scripts/ok.sh": '#!/bin/sh\necho "clean"\n'},
    )
    declared = [
        "whitespace",
        "actionlint",
        "shellcheck",
        "workflow_shellcheck",
        "zizmor",
    ]
    (repo / ".shellcheckrc").write_text("enable=all\n", encoding="utf-8", newline="\n")
    (repo / ".zizmor.yml").write_text("rules: {}\n", encoding="utf-8", newline="\n")
    prof_ref = check_contract.profile_ref("eci/default", "1.0.0")
    profile = expected_checks.build_effective_profile(
        profile_ref=prof_ref,
        profile_revision="rev-1",
        approval_source="governance-signed",
        effective_at="2026-11-15T00:00:00Z",
        evaluated_at="2026-11-15T00:00:00Z",
        context="worktree",
        action="worktree",
        target="HEAD",
        declared_checks=declared,
        required_obligations=[
            {"obligation_ref": f"req-{name}", "check_names": [name]}
            for name in declared
        ],
        domain_complete=True,
        authority_verified=True,
    )
    binding = profile["approval_binding"]
    cfg_path = repo / "ci-selection.toml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8", newline="\n")
        + f"""
[workflow_checks]
shellcheck_config = ".shellcheckrc"
zizmor_config = ".zizmor.yml"

[[non_code_rules]]
exact = ".shellcheckrc"
tests = []
reason = "shellcheck rule config"

[[non_code_rules]]
exact = ".zizmor.yml"
tests = []
reason = "zizmor rule config"

[effective_profile]
profile_ref = {{ name = "{prof_ref["name"]}", version = "{prof_ref["version"]}" }}
profile_revision = "{profile["profile_revision"]}"
approval_source = "{binding["approval_source"]}"
approval_digest = "{binding["approval_digest"]}"
effective_at = "{profile["effective_at"]}"
declared_checks = {json.dumps(declared)}
""",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", ".shellcheckrc", ".zizmor.yml", "ci-selection.toml")
    _git(repo, "commit", "-q", "-m", "configure tool rules and effective profile")

    plan1, exp1 = _build_plan_and_expected(
        repo,
        [".github/workflows/ci.yml", "scripts/ok.sh"],
        effective_profile=profile,
    )
    rep1 = execution.execute(plan1, root=repo)
    assert rep1["status"] == "passed", rep1
    exp1_by = {c["name"]: c for c in exp1["checks"]}
    nr1_by = {c["name"]: c["native_result"] for c in rep1["checks"]}
    for name in ("actionlint", "workflow_shellcheck", "shellcheck", "zizmor"):
        assert exp1_by[name]["rule_ref"] == nr1_by[name]["check"]["rule_ref"]
    assert exp1_by["shellcheck"]["rule_ref"] != exp1["selection_rule_ref"]
    assert exp1_by["zizmor"]["rule_ref"] != exp1["selection_rule_ref"]

    # Edit ONLY .shellcheckrc while ci-selection.toml remains byte-for-byte unchanged.
    (repo / ".shellcheckrc").write_text(
        "disable=SC2086\n", encoding="utf-8", newline="\n"
    )
    plan2, exp2 = _build_plan_and_expected(
        repo,
        [".shellcheckrc"],
        effective_profile=profile,
    )
    rep2 = execution.execute(plan2, root=repo)
    assert rep2["status"] == "passed", rep2
    exp2_by = {c["name"]: c for c in exp2["checks"]}
    nr2_by = {c["name"]: c["native_result"] for c in rep2["checks"]}

    assert exp2["selection_rule_ref"] == exp1["selection_rule_ref"]
    assert (
        exp2_by["shellcheck"]["rule_ref"] == nr2_by["shellcheck"]["check"]["rule_ref"]
    )
    assert (
        exp2_by["shellcheck"]["rule_ref"]["sha256"]
        != exp1_by["shellcheck"]["rule_ref"]["sha256"]
    )
    for unchanged in ("actionlint", "workflow_shellcheck", "zizmor"):
        assert (
            exp2_by[unchanged]["rule_ref"]["sha256"]
            == exp1_by[unchanged]["rule_ref"]["sha256"]
        )
        assert unchanged not in nr2_by

    plan3, exp3 = _build_plan_and_expected(
        repo,
        [".shellcheckrc", ".github/workflows/ci.yml", "scripts/ok.sh"],
        effective_profile=profile,
    )
    rep3 = execution.execute(plan3, root=repo)
    assert rep3["status"] == "passed", rep3
    exp3_by = {c["name"]: c for c in exp3["checks"]}
    nr3_by = {c["name"]: c["native_result"] for c in rep3["checks"]}
    for name in ("actionlint", "workflow_shellcheck", "shellcheck", "zizmor"):
        assert exp3_by[name]["rule_ref"] == nr3_by[name]["check"]["rule_ref"]
    assert (
        exp3_by["shellcheck"]["rule_ref"]["sha256"]
        == exp2_by["shellcheck"]["rule_ref"]["sha256"]
    )
    for unchanged in ("actionlint", "workflow_shellcheck", "zizmor"):
        assert (
            exp3_by[unchanged]["rule_ref"]["sha256"]
            == exp1_by[unchanged]["rule_ref"]["sha256"]
        )

    # Editing .shellcheckrc on disk AFTER plan2 was created causes execution to reject
    # the stale plan at the worktree content binding boundary.
    (repo / ".shellcheckrc").write_text("enable=all\n", encoding="utf-8", newline="\n")
    rep_drift = execution.execute(plan2, root=repo)
    assert rep_drift["status"] == "not_executed", rep_drift

    # Editing ONLY .zizmor.yml updates ONLY zizmor's rule_ref in both Expected and NativeResult.
    (repo / ".zizmor.yml").write_text(
        "rules:\n  unpinned-uses:\n    disable: false\n",
        encoding="utf-8",
        newline="\n",
    )
    plan4, exp4 = _build_plan_and_expected(
        repo,
        [".zizmor.yml", ".github/workflows/ci.yml", "scripts/ok.sh"],
        effective_profile=profile,
    )
    rep4 = execution.execute(plan4, root=repo)
    assert rep4["status"] == "passed", rep4
    exp4_by = {c["name"]: c for c in exp4["checks"]}
    nr4_by = {c["name"]: c["native_result"] for c in rep4["checks"]}
    assert exp4_by["zizmor"]["rule_ref"] == nr4_by["zizmor"]["check"]["rule_ref"]
    assert (
        exp4_by["zizmor"]["rule_ref"]["sha256"]
        != exp3_by["zizmor"]["rule_ref"]["sha256"]
    )
    for unchanged in ("actionlint", "workflow_shellcheck"):
        assert (
            exp4_by[unchanged]["rule_ref"]["sha256"]
            == exp3_by[unchanged]["rule_ref"]["sha256"]
        )


def test_shellcheck_opts_env_does_not_bypass_standalone_or_inline_shellcheck(
    tmp_path, monkeypatch
):
    repo, _ = _init_repo(
        tmp_path / "env_bypass",
        workflows={
            ".github/workflows/inline_bad.yml": (
                "name: CI\n"
                "on: [push]\n"
                "jobs:\n"
                "  test:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - run: echo $UNQUOTED\n"
            )
        },
        shells={"scripts/bad.sh": "#!/bin/sh\necho $UNQUOTED\n"},
    )
    plan, _ = _build_plan_and_expected(
        repo, [".github/workflows/inline_bad.yml", "scripts/bad.sh"]
    )
    monkeypatch.setenv("SHELLCHECK_OPTS", "-e SC2086")
    report = execution.execute(plan, root=repo)
    by_name = {entry["name"]: entry for entry in report["checks"]}

    assert by_name["shellcheck"]["status"] == "failed"
    assert by_name["shellcheck"]["exit_code"] == 1
    assert any(
        f.get("rule") == "SC2086" or f.get("code") == 2086
        for f in by_name["shellcheck"].get("findings", [])
    )

    assert by_name["workflow_shellcheck"]["status"] == "failed"
    assert by_name["workflow_shellcheck"]["exit_code"] == 1
    assert any(
        "SC2086" in str(f.get("message") or f.get("code") or "")
        for f in by_name["workflow_shellcheck"].get("findings", [])
    )
