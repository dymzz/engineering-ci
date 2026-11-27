"""CI applicability policy: cumulative input contract and fail-closed wiring."""

import ast
import fnmatch
import json
import re
import subprocess
from pathlib import Path

import pytest

from engineering_ci import ci_applicability, ci_check_inputs, selection

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
DAGGER_MAIN = ROOT / ".dagger/src/engineering_ci_module/main.py"
DAGGER_REPOSITORY = ROOT / ".dagger/src/engineering_ci_module/repository.py"
DAGGER_TOOLING = ROOT / ".dagger/src/engineering_ci_module/tooling.py"
CONTRACT = ROOT / "src/engineering_ci/ci_check_inputs.py"


def _git(root, *args):
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "applicability@example.invalid")
    _git(path, "config", "user.name", "Applicability Test")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    _git(path, "add", "base.txt")
    _git(path, "commit", "-q", "-m", "base")
    return path


def _commit(root, relative, text="# change\n"):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _git(root, "add", relative)
    _git(root, "commit", "-q", "-m", f"change {relative}")


def _changes(root, base="HEAD^"):
    return selection.changeset(root, base=base, head="HEAD", mode="commit")


def _report(root, *, base="HEAD^", full=False, full_reason=None, previous_commit=None):
    return ci_applicability.build_report(
        _changes(root, base=base) if root is not None else None,
        event="pull_request",
        full=full,
        full_reason=full_reason,
        requested_base="HEAD^" if root is not None else None,
        requested_head="HEAD",
        previous_commit=previous_commit,
    )


def _statuses(report):
    return {
        check["name"]: check["status"]
        for check in report["layer_3_planned_scope"]["checks"]
    }


def _check(report, name):
    return next(
        check
        for check in report["layer_3_planned_scope"]["checks"]
        if check["name"] == name
    )


def _only(*names):
    result = dict.fromkeys(
        ["lint", "format-check", "tests", "version", "lock"], "not_applicable"
    )
    for name in names:
        result[name] = "applicable"
    return result


@pytest.mark.parametrize(
    ("relative", "text"),
    [
        ("guides/common-tasks.md", "# guide\n"),
        ("design/execution-graph/README.md", "# design\n"),
        ("docs/architecture/local-checks.md", "# local\n"),
        ("AGENTS.md", "# agents\n"),
        (".agent/upstream-observations.md", "# observation\n"),
        ("scripts/README.md", "# scripts\n"),
    ],
)
def test_source_excluded_documentation_skips_every_check(tmp_path, relative, text):
    root = _repo(tmp_path / "repo")
    _commit(root, relative, text)
    report = _report(root)
    assert _statuses(report) == _only()
    assert report["layer_2_candidate"]["status"] == "not_applicable"
    assert report["increment"]["status"] == "unavailable"


def test_contract_schema_under_docs_keeps_the_pytest_check(tmp_path):
    root = _repo(tmp_path / "repo")
    _commit(root, "docs/architecture/release/manifest.schema.json", "{}\n")
    report = _report(root)
    assert _statuses(report) == _only("tests")
    assert _check(report, "tests")["reason"] == "repository_source_inputs"


def test_root_markdown_is_formatted_but_not_linted(tmp_path):
    # Ruff 0.16.10: format reads Markdown code blocks, check does not.
    root = _repo(tmp_path / "repo")
    _commit(root, "README.md", "# readme\n")
    report = _report(root)
    assert _statuses(report) == _only("format-check", "tests")
    assert _check(report, "lint")["reason"] == "no_check_inputs"


def test_notebook_and_nested_ruff_config_are_lint_and_format_inputs(tmp_path):
    root = _repo(tmp_path / "repo")
    _commit(root, "examples/probe.ipynb", "{}\n")
    _commit(root, "src/ruff.toml", "line-length = 100\n")
    _commit(root, "src/pyproject.toml", "[tool.ruff]\nline-length = 100\n")
    report = _report(root, base="HEAD~3")
    assert _statuses(report) == _only("lint", "format-check", "tests", "lock")
    assert _check(report, "lock")["reason"] == "check_inputs_changed"


def test_nested_gitignore_is_a_ruff_discovery_input(tmp_path):
    root = _repo(tmp_path / "repo")
    _commit(root, "src/.gitignore", "generated.py\n")
    report = _report(root)
    assert _statuses(report) == _only("lint", "format-check", "tests")


def test_python_change_runs_ruff_and_tests_only(tmp_path):
    root = _repo(tmp_path / "repo")
    _commit(root, "src/engineering_ci/selection.py", "VALUE = 1\n")
    report = _report(root)
    assert _statuses(report) == _only("lint", "format-check", "tests")


def test_version_check_implementation_is_a_version_input(tmp_path):
    root = _repo(tmp_path / "repo")
    _commit(root, "src/engineering_ci/release/version.py", "VALUE = 1\n")
    report = _report(root)
    assert _statuses(report) == _only("lint", "format-check", "tests", "version")


def test_workspace_member_pyproject_is_a_lock_input(tmp_path):
    root = _repo(tmp_path / "repo")
    _commit(root, "tests/fixtures/python-app/pyproject.toml", "[project]\n")
    report = _report(root)
    assert _statuses(report) == _only("lint", "format-check", "tests", "lock")


@pytest.mark.parametrize(
    "relative",
    [
        ".dagger/src/engineering_ci_module/repository.py",
        ".dagger/src/engineering_ci_module/main.py",
        ".dagger/src/engineering_ci_module/tooling.py",
        ".dagger/sdk/src/dagger/client/gen.py",
        "pyproject.toml",
        ".github/workflows/ci.yml",
    ],
)
def test_shared_toolchain_change_escalates_to_every_check(tmp_path, relative):
    root = _repo(tmp_path / "repo")
    _commit(root, relative, "changed: true\n")
    report = _report(root)
    assert set(_statuses(report).values()) == {"applicable"}
    assert report["layer_2_candidate"]["reason"] == "toolchain_inputs"


@pytest.mark.parametrize(
    "relative",
    [
        "src/engineering_ci/ci_check_inputs.py",
        "src/engineering_ci/ci_applicability.py",
        "tests/test_ci_applicability.py",
    ],
)
def test_self_verification_inputs_cannot_cancel_their_own_checks(tmp_path, relative):
    root = _repo(tmp_path / "repo")
    _commit(root, relative, "# changed\n")
    report = _report(root)
    assert set(_statuses(report).values()) == {"applicable"}
    assert report["layer_2_candidate"]["reason"] == "self_verification_inputs"
    for check in report["layer_3_planned_scope"]["checks"]:
        assert check["reason"] == "self_verification_inputs"


def test_mixed_change_takes_the_union_of_applicable_checks(tmp_path):
    root = _repo(tmp_path / "repo")
    _commit(root, "guides/common-tasks.md")
    _commit(root, "src/engineering_ci/hygiene.py", "VALUE = 1\n")
    _commit(root, "docs/architecture/release/manifest.schema.json", "{}\n")
    report = _report(root, base="HEAD~3")
    assert _statuses(report) == _only("lint", "format-check", "tests")


def test_incomplete_change_set_escalates_and_never_skips(tmp_path):
    root = _repo(tmp_path / "repo")
    changes = _changes(root, base="missing-ref")
    assert changes.complete is False
    report = ci_applicability.build_report(changes, event="pull_request")
    assert set(_statuses(report).values()) == {"applicable"}
    assert report["layer_2_candidate"]["status"] == "escalated"
    assert report["layer_2_candidate"]["reason"] == "changeset_incomplete"
    assert _check(report, "lint")["reason"] == "changeset_incomplete"


def test_empty_change_set_is_explicitly_not_applicable(tmp_path):
    root = _repo(tmp_path / "repo")
    _git(root, "commit", "-q", "--allow-empty", "-m", "empty")
    report = _report(root)
    assert set(_statuses(report).values()) == {"not_applicable"}
    assert report["layer_2_candidate"]["reason"] == "no_changed_paths"


def test_explicit_full_verification_overrides_a_documentation_change(tmp_path):
    root = _repo(tmp_path / "repo")
    _commit(root, "guides/common-tasks.md")
    report = _report(root, full=True, full_reason="release_workflow_call")
    assert set(_statuses(report).values()) == {"applicable"}
    assert report["layer_2_candidate"]["status"] == "escalated"
    assert _check(report, "tests")["reason"] == "release_workflow_call"
    assert report["layer_1_trigger"]["full_requested"] is True


def test_release_call_without_a_base_keeps_identity_unbound():
    report = ci_applicability.build_report(
        None,
        event="workflow_call",
        full=True,
        full_reason="explicit_caller_full",
        requested_base=None,
        requested_head="a" * 40,
    )
    assert set(_statuses(report).values()) == {"applicable"}
    candidate = report["layer_2_candidate"]
    assert candidate["snapshot"] is None
    assert candidate["complete"] is None
    assert candidate["head_commit"] is None
    assert candidate["requested_head"] == "a" * 40
    assert candidate["identity_status"] == "not_bound_to_change_set"
    assert report["layer_2_candidate"]["status"] == "escalated"


def test_report_separates_planned_scope_from_observed_execution(tmp_path):
    root = _repo(tmp_path / "repo")
    _commit(root, "src/engineering_ci/selection.py")
    report = _report(root)
    assert "layer_3_execution" not in report
    assert report["layer_3_planned_scope"]["claim"] == "planned_scope_only"
    observation = report["execution_observation"]
    assert observation["status"] == "not_observed_in_this_report"
    assert observation["authority"] == ["#52", "#75", "#86"]


def test_previous_commit_is_recorded_without_claiming_reuse(tmp_path):
    root = _repo(tmp_path / "repo")
    _commit(root, "src/engineering_ci/selection.py", "VALUE = 1\n")
    report = _report(root, previous_commit="a" * 40)
    increment = report["increment"]
    assert increment["status"] == "unavailable"
    assert increment["reason"] == "trusted_evidence_admission_unavailable"
    assert increment["owner"] == "#63"
    assert increment["previous_commit"] == "a" * 40


def test_git_path_identity_is_preserved(tmp_path):
    root = _repo(tmp_path / "repo")
    changes = _changes(root)
    literal = selection.ChangeSet(
        root=changes.root,
        base_ref=changes.base_ref,
        head_ref=changes.head_ref,
        base_commit=changes.base_commit,
        head_commit=changes.head_commit,
        tree=changes.tree,
        paths=("guides\\probe.txt",),
        worktree_dirty=False,
        complete=True,
        snapshot="commit",
    )
    checks, status, _ = ci_applicability.decide(literal)
    assert status == "applicable"
    by_name = {check["name"]: check for check in checks}
    # A literal backslash on POSIX is part of the filename, not a separator.
    assert by_name["tests"]["status"] == "applicable"
    assert by_name["lint"]["status"] == "not_applicable"
    assert by_name["format-check"]["status"] == "not_applicable"


def test_directory_prefix_does_not_match_a_sibling_name(tmp_path):
    root = _repo(tmp_path / "repo")
    changes = _changes(root)
    sibling = selection.ChangeSet(
        root=changes.root,
        base_ref=changes.base_ref,
        head_ref=changes.head_ref,
        base_commit=changes.base_commit,
        head_commit=changes.head_commit,
        tree=changes.tree,
        paths=("guides-other/probe.md",),
        worktree_dirty=False,
        complete=True,
        snapshot="commit",
    )
    checks, status, _ = ci_applicability.decide(sibling)
    assert status == "applicable"
    assert {check["name"] for check in checks if check["status"] == "applicable"} == {
        "format-check",
        "tests",
    }


def test_ci_checks_match_native_definitions_and_workflow_jobs():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    check_names = {
        node.name
        for container in ast.parse(DAGGER_MAIN.read_text(encoding="utf-8")).body
        if isinstance(container, ast.ClassDef)
        for node in container.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        if "check"
        in {
            decorator.id
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Name)
        }
    }
    for check in ci_check_inputs.CI_CHECKS:
        assert check.dagger_check.replace("-", "_") in check_names, check.name
        assert f"  {check.job}:" in workflow, check.job
        assert f"needs.change-applicability.outputs.{check.github_output}" in workflow


def test_workflow_paths_match_the_self_verification_contract():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "self_verification_inputs" in workflow
    for path in ci_check_inputs.SELF_VERIFICATION_FILES:
        assert path in workflow, path
    for prefix in ci_check_inputs.SELF_VERIFICATION_PREFIXES:
        assert prefix in workflow, prefix


def _job_block(workflow, job):
    match = re.search(
        rf"^  {re.escape(job)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:|\Z)",
        workflow,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, job
    return match.group(1)


def test_workflow_jobs_run_after_a_failed_decision_and_require_prior_steps():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    for job in ("python-ci", "sync-tool-tests", "version-metadata"):
        block = _job_block(workflow, job)
        assert "    if: ${{ !cancelled() }}" in block, job
        assert "success() && !cancelled()" in block, job
        assert "!= 'false'" in block, job
    telemetry = _job_block(workflow, "dagger-native-telemetry")
    assert "needs: [change-applicability]" not in telemetry
    assert "change-applicability.outputs.tests" not in telemetry


def _step_conditions(block):
    return re.findall(r"^        if: \$\{\{ (.+) \}\}$", block, re.MULTILINE)


def _evaluate_condition(condition, *, cancelled, setup_ok, decision_ok, outputs=None):
    """Evaluate the workflow's own condition text for the documented truth table.

    GitHub evaluates the expressions natively; this helper only substitutes the
    exact tokens this workflow uses so the declared fallback semantics can be
    tested without starting a failing Workflow.
    """
    outputs = outputs or {}
    expr = condition.replace("${{", "").replace("}}", "").strip()
    expr = expr.replace("!cancelled()", "False" if cancelled else "True")
    expr = expr.replace("success()", "True" if setup_ok else "False")
    expr = expr.replace(
        "needs.change-applicability.result != 'success'",
        "True" if not decision_ok else "False",
    )

    def _replace_output(match):
        value = outputs.get(match.group(1))
        return "True" if value != "false" else "False"

    expr = re.sub(
        r"needs\.change-applicability\.outputs\.(\w+) != 'false'",
        _replace_output,
        expr,
    )
    expr = expr.replace("&&", " and ").replace("||", " or ")
    assert set(re.findall(r"[A-Za-z_]+", expr)) <= {
        "True",
        "False",
        "and",
        "or",
        "not",
    }, expr
    return bool(eval(expr, {"__builtins__": {}}, {}))


def test_job_fallback_truth_table():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    for job in ("python-ci", "sync-tool-tests", "version-metadata"):
        condition = _job_block(workflow, job).split("    if: ", 1)[1].splitlines()[0]
        assert _evaluate_condition(
            condition, cancelled=False, setup_ok=True, decision_ok=False
        )
        assert not _evaluate_condition(
            condition, cancelled=True, setup_ok=True, decision_ok=True
        )


def test_step_fallback_truth_table():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    lint_conditions = [
        condition
        for condition in _step_conditions(_job_block(workflow, "python-ci"))
        if "outputs.lint" in condition and "outputs.format_check" not in condition
    ]
    # checkout, tool installation and the lint step share one fail-closed form.
    assert lint_conditions
    assert len(set(lint_conditions)) == 1
    lint = lint_conditions[0]
    scenarios = (
        # decision failed / skipped or outputs missing: run every native check
        (False, True, False, {}, True),
        (False, True, True, {}, True),
        (False, True, True, {"lint": "true"}, True),
        # explicit false only, and only when this job's setup succeeded
        (False, True, True, {"lint": "false"}, False),
        (False, False, True, {"lint": "true"}, False),
        # cancellation always prevents the step
        (True, True, True, {"lint": "true"}, False),
    )
    for cancelled, setup_ok, decision_ok, outputs, expected in scenarios:
        assert (
            _evaluate_condition(
                lint,
                cancelled=cancelled,
                setup_ok=setup_ok,
                decision_ok=decision_ok,
                outputs=outputs,
            )
            is expected
        ), (cancelled, setup_ok, decision_ok, outputs)


def test_version_metadata_runs_when_either_check_is_applicable():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    version_conditions = [
        condition
        for condition in _step_conditions(_job_block(workflow, "version-metadata"))
        if "outputs.version" in condition
    ]
    assert version_conditions
    assert len(set(version_conditions)) == 1
    condition = version_conditions[0]
    assert _evaluate_condition(
        condition,
        cancelled=False,
        setup_ok=True,
        decision_ok=True,
        outputs={"version": "false", "lock": "true"},
    )
    assert not _evaluate_condition(
        condition,
        cancelled=False,
        setup_ok=True,
        decision_ok=True,
        outputs={"version": "false", "lock": "false"},
    )


def test_reusable_call_requires_explicit_full_intent():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "full_verification:" in workflow
    assert "default: true" in workflow
    assert "FULL_INPUT: ${{ inputs.full_verification }}" in workflow
    assert "COVERAGE_INPUT: ${{ inputs.coverage }}" in workflow
    assert "--config" not in workflow
    release = RELEASE.read_text(encoding="utf-8")
    assert "full_verification: true" in release


def _dagger_source_excludes():
    tree = ast.parse(CONTRACT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "SOURCE_EXCLUDES"
            for target in node.targets
        ):
            values = set()
            for element in node.value.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    values.add(element.value)
                elif isinstance(element, ast.Starred) and isinstance(
                    element.value, ast.Name
                ):
                    values.add(element.value.id)
            return values
    raise AssertionError("SOURCE_EXCLUDES not found")


def test_documentation_contract_stays_within_the_dagger_source_excludes():
    # The contract module is the single authority now; this guards an edit that
    # moves a documentation pattern without updating the source excludes.
    excludes = _dagger_source_excludes()
    assert "docs/**/*.md" in excludes
    assert "scripts/*.md" in excludes
    for prefix in ci_check_inputs.DOCUMENTATION_PREFIXES:
        assert prefix.rstrip("/") in excludes, prefix
    for path in ci_check_inputs.DOCUMENTATION_FILES:
        assert any(fnmatch.fnmatchcase(path, pattern) for pattern in excludes), path
    for prefix in ci_check_inputs.DOCUMENTATION_MARKDOWN_PREFIXES:
        assert any(
            pattern.startswith(prefix) and pattern.endswith("/**/*.md")
            for pattern in excludes
        ), prefix


def test_dagger_module_consumes_the_shared_source_contract():
    repository = DAGGER_REPOSITORY.read_text(encoding="utf-8")
    tooling = DAGGER_TOOLING.read_text(encoding="utf-8")
    assert "from engineering_ci.ci_check_inputs import" in repository
    assert "from engineering_ci.ci_check_inputs import SOURCE_NOISE" in tooling
    assert "WORKSPACE_SCRATCH = [" not in repository
    assert "SOURCE_NOISE = [" not in tooling


def test_documentation_predicate_keeps_contract_inputs_applicable():
    for path in (
        "guides/common-tasks.md",
        "design/execution-graph/README.md",
        "docs/architecture/local-checks.md",
        "docs/verification/plan-result-sharing/README.md",
        ".agent/upstream-observations.md",
        "scripts/README.md",
        "AGENTS.md",
        ".dagger/AGENTS.md",
    ):
        assert ci_check_inputs.is_source_excluded_path(path), path
    for path in (
        "docs/architecture/release/manifest.schema.json",
        "docs/architecture/eci-review-result.schema.json",
        "docs/verification/review-experiment/validate.py",
        "README.md",
        "src/engineering_ci/selection.py",
        "scripts/check.sh",
        ".github/workflows/ci.yml",
        "guides\\probe.txt",
    ):
        assert not ci_check_inputs.is_source_excluded_path(path), path


def test_cli_writes_planned_scope_report_and_outputs(tmp_path, capsys):
    root = _repo(tmp_path / "repo")
    _commit(root, "guides/common-tasks.md")
    output = tmp_path / "report.json"
    github_output = tmp_path / "github-output.txt"
    summary = tmp_path / "summary.md"
    summary.write_text("existing\n", encoding="utf-8")
    code = ci_applicability.main(
        [
            "--root",
            str(root),
            "--event",
            "pull_request",
            "--base",
            "HEAD^",
            "--head",
            "HEAD",
            "--output",
            str(output),
            "--github-output",
            str(github_output),
            "--summary",
            str(summary),
            "--json",
        ]
    )
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["kind"] == ci_applicability.KIND
    assert "layer_3_planned_scope" in report
    assert json.loads(output.read_text(encoding="utf-8")) == report
    lines = github_output.read_text(encoding="utf-8").splitlines()
    for name in ("lint", "format_check", "tests", "version", "lock"):
        assert f"{name}=false" in lines
    assert "candidate_status=not_applicable" in lines
    assert "applicability=recorded" in lines
    assert summary.read_text(encoding="utf-8").startswith("existing\n")
    assert "### CI applicability" in summary.read_text(encoding="utf-8")


def test_cli_requires_a_base_commit(tmp_path):
    root = _repo(tmp_path / "repo")
    code = ci_applicability.main(["--root", str(root), "--event", "pull_request"])
    assert code == 2


def test_cli_full_without_base_records_requested_head(tmp_path):
    root = _repo(tmp_path / "repo")
    output = tmp_path / "report.json"
    code = ci_applicability.main(
        [
            "--root",
            str(root),
            "--event",
            "workflow_call",
            "--full",
            "--full-reason",
            "explicit_caller_full",
            "--head",
            "a" * 40,
            "--output",
            str(output),
            "--json",
        ]
    )
    assert code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    candidate = report["layer_2_candidate"]
    assert candidate["complete"] is None
    assert candidate["requested_head"] == "a" * 40
    assert candidate["identity_status"] == "not_bound_to_change_set"
    assert {check["status"] for check in report["layer_3_planned_scope"]["checks"]} == {
        "applicable"
    }
