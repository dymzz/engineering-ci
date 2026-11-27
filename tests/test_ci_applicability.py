"""CI applicability policy: cumulative change classes and fail-closed escalation."""

import ast
import fnmatch
import json
import subprocess
from pathlib import Path

import pytest

from engineering_ci import ci_applicability, selection

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "ci-applicability.toml"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
DAGGER_MAIN = ROOT / ".dagger/src/engineering_ci_module/main.py"
DAGGER_REPOSITORY = ROOT / ".dagger/src/engineering_ci_module/repository.py"


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


def _policy():
    return ci_applicability.load_config(POLICY)


def _changes(root, base="HEAD^"):
    return selection.changeset(root, base=base, head="HEAD", mode="commit")


def _report(root, *, full=False, full_reason=None, previous_commit=None, base="HEAD^"):
    return ci_applicability.build_report(
        _changes(root, base=base) if root is not None else None,
        _policy(),
        config_path=POLICY,
        event="pull_request",
        full=full,
        full_reason=full_reason,
        previous_commit=previous_commit,
    )


def _statuses(report):
    return {
        check["name"]: check["status"]
        for check in report["layer_3_execution"]["checks"]
    }


def _check(report, name):
    return next(
        check
        for check in report["layer_3_execution"]["checks"]
        if check["name"] == name
    )


def test_guide_only_change_skips_every_existing_check(tmp_path):
    root = _repo(tmp_path / "repo")
    _commit(root, "guides/common-tasks.md")
    report = _report(root)
    assert _statuses(report) == {
        "lint": "not_applicable",
        "format-check": "not_applicable",
        "tests": "not_applicable",
        "version": "not_applicable",
        "lock": "not_applicable",
    }
    assert report["layer_2_candidate"]["status"] == "not_applicable"
    assert report["layer_2_candidate"]["reason"] == "no_applicable_checks"
    assert _check(report, "tests")["reason"] == "documentation_only_changes"
    assert report["increment"]["status"] == "unavailable"


def test_contract_schema_under_docs_keeps_the_pytest_check(tmp_path):
    root = _repo(tmp_path / "repo")
    _commit(root, "docs/architecture/release/manifest.schema.json", "{}\n")
    report = _report(root)
    assert _statuses(report) == {
        "lint": "not_applicable",
        "format-check": "not_applicable",
        "tests": "applicable",
        "version": "not_applicable",
        "lock": "not_applicable",
    }
    assert _check(report, "tests")["reason"] == "repository_source_inputs"
    assert _check(report, "tests")["inputs"] == [
        "docs/architecture/release/manifest.schema.json"
    ]


def test_python_change_runs_ruff_and_tests_only(tmp_path):
    root = _repo(tmp_path / "repo")
    _commit(root, "src/engineering_ci/selection.py", "VALUE = 1\n")
    report = _report(root)
    assert _statuses(report) == {
        "lint": "applicable",
        "format-check": "applicable",
        "tests": "applicable",
        "version": "not_applicable",
        "lock": "not_applicable",
    }
    assert _check(report, "lint")["reason"] == "check_inputs_changed"


def test_mixed_change_takes_the_union_of_applicable_checks(tmp_path):
    root = _repo(tmp_path / "repo")
    _commit(root, "guides/common-tasks.md")
    _commit(root, "src/engineering_ci/hygiene.py", "VALUE = 1\n")
    _commit(root, "docs/architecture/release/manifest.schema.json", "{}\n")
    report = _report(root, base="HEAD~3")
    assert _statuses(report) == {
        "lint": "applicable",
        "format-check": "applicable",
        "tests": "applicable",
        "version": "not_applicable",
        "lock": "not_applicable",
    }


@pytest.mark.parametrize(
    "relative",
    [
        "pyproject.toml",
        ".github/workflows/ci.yml",
        "ci-applicability.toml",
        ".dagger/uv.lock",
    ],
)
def test_toolchain_change_escalates_to_every_check(tmp_path, relative):
    root = _repo(tmp_path / "repo")
    _commit(root, relative, "changed: true\n")
    report = _report(root)
    assert set(_statuses(report).values()) == {"applicable"}
    assert report["layer_2_candidate"]["reason"] == "toolchain_inputs"
    assert _check(report, "lock")["reason"] == "toolchain_inputs"


def test_incomplete_change_set_escalates_and_never_skips(tmp_path):
    root = _repo(tmp_path / "repo")
    changes = _changes(root, base="missing-ref")
    assert changes.complete is False
    report = ci_applicability.build_report(
        changes,
        _policy(),
        config_path=POLICY,
        event="pull_request",
    )
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


def test_release_call_without_a_base_declares_the_full_scope():
    report = ci_applicability.build_report(
        None,
        _policy(),
        config_path=POLICY,
        event="workflow_call",
        full=True,
        full_reason="release_workflow_call",
    )
    assert set(_statuses(report).values()) == {"applicable"}
    assert report["layer_2_candidate"]["snapshot"] is None
    assert report["layer_2_candidate"]["complete"] is None
    assert report["layer_2_candidate"]["status"] == "escalated"


def test_previous_commit_is_recorded_without_claiming_reuse(tmp_path):
    root = _repo(tmp_path / "repo")
    _commit(root, "src/engineering_ci/selection.py", "VALUE = 1\n")
    report = _report(root, previous_commit="a" * 40)
    increment = report["increment"]
    assert increment["status"] == "unavailable"
    assert increment["reason"] == "trusted_evidence_admission_unavailable"
    assert increment["owner"] == "#63"
    assert increment["previous_commit"] == "a" * 40


def _write_policy(tmp_path, text):
    path = tmp_path / "policy.toml"
    path.write_text(text, encoding="utf-8")
    return path


GOOD_CHECK = """
[[check]]
name = "lint"
job = "python-ci"
dagger_check = "lint"
suffixes = [".py"]
"""


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (
            "schema_version = 2\n[toolchain]\nfiles = []\nprefixes = []\n"
            "[documentation]\nfiles = []\nprefixes = []\nmarkdown_prefixes = []\n"
            + GOOD_CHECK,
            "config_schema_version_unsupported",
        ),
        (
            "schema_version = 1\nextra = true\n[toolchain]\nfiles = []\nprefixes = []\n"
            "[documentation]\nfiles = []\nprefixes = []\nmarkdown_prefixes = []\n"
            + GOOD_CHECK,
            "config_keys_invalid",
        ),
        (
            (
                "schema_version = 1\n[toolchain]\nfiles = []\nprefixes = []\n"
                "[documentation]\nfiles = []\nprefixes = []\nmarkdown_prefixes = []\n"
                '[[check]]\nname = "lint"\njob = "python-ci"\ndagger_check = "lint"\n'
            ),
            "check[0]_inputs_missing",
        ),
        (
            (
                "schema_version = 1\n[toolchain]\nfiles = []\nprefixes = []\n"
                "[documentation]\nfiles = []\nprefixes = []\nmarkdown_prefixes = []\n"
                '[[check]]\nname = "tests"\njob = "sync-tool-tests"\n'
                'dagger_check = "tests"\nsource_scope = true\nsuffixes = [".py"]\n'
            ),
            "check[0]_scope_ambiguous",
        ),
        (
            "schema_version = 1\n[toolchain]\nfiles = []\nprefixes = []\n"
            "[documentation]\nfiles = []\nprefixes = []\nmarkdown_prefixes = []\n"
            + GOOD_CHECK
            * 2,
            "config_check_duplicate",
        ),
        (
            'schema_version = 1\n[toolchain]\nfiles = []\nprefixes = ["/abs/"]\n'
            "[documentation]\nfiles = []\nprefixes = []\nmarkdown_prefixes = []\n"
            + GOOD_CHECK,
            "toolchain.prefixes_invalid",
        ),
    ],
)
def test_invalid_policy_is_refused(tmp_path, text, reason):
    path = _write_policy(tmp_path, text)
    with pytest.raises(ci_applicability.ApplicabilityError) as error:
        ci_applicability.load_config(path)
    assert str(error.value) == reason


def test_cli_writes_report_github_output_and_summary(tmp_path, capsys):
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
            "--config",
            str(POLICY),
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
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["kind"] == ci_applicability.KIND
    assert json.loads(output.read_text(encoding="utf-8")) == report
    lines = github_output.read_text(encoding="utf-8").splitlines()
    assert "lint=false" in lines
    assert "format_check=false" in lines
    assert "tests=false" in lines
    assert "version=false" in lines
    assert "lock=false" in lines
    assert "candidate_status=not_applicable" in lines
    assert "applicability=recorded" in lines
    assert summary.read_text(encoding="utf-8").startswith("existing\n")
    assert "### CI applicability" in summary.read_text(encoding="utf-8")


def test_cli_requires_a_base_commit(tmp_path):
    root = _repo(tmp_path / "repo")
    code = ci_applicability.main(
        ["--root", str(root), "--config", str(POLICY), "--event", "pull_request"]
    )
    assert code == 2


def test_policy_checks_match_the_workflow_outputs_and_conditions():
    config = _policy()
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "  change-applicability:" in workflow
    for check in config.checks:
        assert f"  {check.job}:" in workflow
        assert f"needs.change-applicability.outputs.{check.github_output}" in workflow
    # Every skip condition is fail-closed: a missing or unexpected output runs
    # the native check instead of silently skipping it.
    assert "!= 'false'" in workflow
    assert "needs.change-applicability.outputs.tests != 'false'" in workflow


def _dagger_check_functions():
    tree = ast.parse(DAGGER_MAIN.read_text(encoding="utf-8"))
    names = set()
    for container in tree.body:
        if isinstance(container, ast.ClassDef):
            nodes = container.body
        elif isinstance(container, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nodes = [container]
        else:
            continue
        for node in nodes:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = {
                decorator.id
                for decorator in node.decorator_list
                if isinstance(decorator, ast.Name)
            }
            if "check" in decorators:
                names.add(node.name)
    return names


def test_policy_dagger_checks_exist_in_the_native_module():
    checks = _dagger_check_functions()
    for check in _policy().checks:
        assert check.dagger_check.replace("-", "_") in checks, check.name


DOCUMENTATION_PREFIXES = (".agent", "design", "guides")
DOCUMENTATION_FILES = (".dagger/AGENTS.md", "AGENTS.md", "scripts/README.md")


def _dagger_source_excludes():
    tree = ast.parse(DAGGER_REPOSITORY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "SOURCE_EXCLUDES"
            for target in node.targets
        ):
            return {
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
    raise AssertionError("SOURCE_EXCLUDES not found")


def test_documentation_policy_stays_within_the_dagger_source_excludes():
    config = _policy()
    excludes = _dagger_source_excludes()
    assert "docs/**/*.md" in excludes
    assert "scripts/*.md" in excludes
    for prefix in config.documentation_prefixes:
        assert prefix.rstrip("/") in excludes, prefix
    for prefix in config.documentation_markdown_prefixes:
        assert any(
            pattern.startswith(prefix) and pattern.endswith("/**/*.md")
            for pattern in excludes
        ), prefix
    for path in config.documentation_files:
        assert any(fnmatch.fnmatchcase(path, pattern) for pattern in excludes), path
    for prefix in DOCUMENTATION_PREFIXES:
        assert prefix in excludes
    for path in DOCUMENTATION_FILES:
        assert any(fnmatch.fnmatchcase(path, pattern) for pattern in excludes), path


def test_documentation_predicate_keeps_contract_inputs_applicable():
    config = _policy()
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
        assert ci_applicability.is_documentation_path(path, config), path
    for path in (
        "docs/architecture/release/manifest.schema.json",
        "docs/architecture/eci-review-result.schema.json",
        "docs/verification/review-experiment/validate.py",
        "README.md",
        "src/engineering_ci/selection.py",
        "scripts/check.sh",
        ".github/workflows/ci.yml",
    ):
        assert not ci_applicability.is_documentation_path(path, config), path
