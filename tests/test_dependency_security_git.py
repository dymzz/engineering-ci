"""Exact Git identity and native advisory-query failures remain blocking."""

import ast
import json
from pathlib import Path

import pytest
from test_dependency_security import FakeRunner, native, run
from test_dependency_security import project as base_project

project = base_project

REPO = "https://github.com/example/private.git"
SHA = "a" * 40
GIT = (
    '\n[[package]]\nname="private"\nversion="0.1.0"\n'
    f'source={{git="{REPO}?rev={SHA}#{SHA}"}}\n'
)


def test_standalone_security_helper_retains_workflow_python_compatibility():
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "src/engineering_ci/dependency_security.py",
        "src/engineering_ci/tools/security.py",
    ):
        ast.parse(
            (root / relative).read_text(encoding="utf-8"), feature_version=(3, 11)
        )


def test_core_policy_covers_every_immutable_git_source():
    import tomllib

    from engineering_ci import dependency_security as security

    root = Path(__file__).resolve().parents[1]
    approved, _ = security.git_policy(root / "config/dependency-security.toml")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    _, unsupported, _, selected = security.lock_inventory(
        project,
        (root / "uv.lock").read_text(encoding="utf-8"),
        ["https://mirrors.aliyun.com/pypi/simple"],
        approved,
    )
    sources = project.get("tool", {}).get("uv", {}).get("sources", {})
    assert not unsupported
    assert set(sources) == set(approved)
    assert {item["package"] for item in selected} == set(approved)


def policy(project, *, commit=SHA, repository=REPO):
    location = project / "git-policy.toml"
    location.write_text(
        'schema_version=1\n[[git_sources]]\npackage="private"\n'
        f'repository="{repository}"\ncommit="{commit}"\n'
    )
    return location


def git_native(*ids):
    result = native()
    result["results"][0]["packages"].append(
        {
            "package": {"name": REPO, "version": "", "ecosystem": "GIT", "commit": SHA},
            "vulnerabilities": [
                {
                    "id": item,
                    "affected": [{"package": {"ecosystem": "GIT", "name": REPO}}],
                }
                for item in ids
            ],
        }
    )
    return result


class GitRunner(FakeRunner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.git_input, self.requested = None, []

    def __call__(self, args, cwd, timeout):
        self.requested.append(args)
        for arg in args:
            if arg.startswith("--lockfile=osv-scanner:"):
                self.git_input = json.loads(
                    Path(arg.removeprefix("--lockfile=osv-scanner:")).read_bytes()
                )
        return super().__call__(
            [arg.replace("--lockfile=osv-scanner:", "--lockfile=") for arg in args],
            cwd,
            timeout,
        )


def prepare(project):
    lock = project / "uv.lock"
    lock.write_text(lock.read_text() + GIT)
    return policy(project)


def test_approved_git_uses_native_commit_query_and_keeps_original_lock(project):
    path = prepare(project)
    original = (project / "uv.lock").read_bytes()
    fake = GitRunner(git_native())
    report, raw, _ = run(project, fake, git_policy_path=path)
    assert report["gate"]["passed"] and report["dependencies_scanned"] == 2
    assert report["git_sources"][0]["source_integrity"] == "passed"
    assert report["git_sources"][0]["advisory_query"] == "complete"
    assert fake.git_input["results"][0]["packages"][0]["package"] == {
        "name": REPO,
        "commit": SHA,
    }
    assert "private" not in fake.lock and "idna" in fake.lock
    assert (project / "uv.lock").read_bytes() == original
    assert raw["results"][0]["source"]["path"] == "uv.lock"


def test_git_advisory_is_blocking_and_is_not_a_pypi_name_match(project):
    path = prepare(project)
    report, _, _ = run(
        project,
        GitRunner(git_native("GHSA-abcd-1234-abcd"), code=1),
        git_policy_path=path,
    )
    assert report["status"] == "failed" and report["gate"]["exit_code"] == 1
    finding = report["vulnerabilities"][0]
    assert finding["ecosystem"] == "GIT" and finding["commit"] == SHA
    assert finding["fixed_versions"] == []


@pytest.mark.parametrize(
    "change",
    [
        "no_policy",
        "wrong_commit",
        "wrong_repository",
        "floating_ref",
        "credential",
        "declaration",
    ],
)
def test_unapproved_or_inconsistent_git_never_queries(project, change):
    path = prepare(project)
    lock = project / "uv.lock"
    if change == "no_policy":
        path = None
    elif change == "wrong_commit":
        path = policy(project, commit="b" * 40)
    elif change == "wrong_repository":
        path = policy(project, repository="https://github.com/example/other.git")
    elif change == "floating_ref":
        lock.write_text(lock.read_text().replace("?rev=" + SHA, "?branch=main"))
    elif change == "credential":
        lock.write_text(
            lock.read_text().replace("https://github.com", "https://secret@github.com")
        )
    else:
        (project / "pyproject.toml").write_text(
            (project / "pyproject.toml").read_text()
            + f'\n[tool.uv.sources]\nprivate={{git="{REPO}", rev="main"}}\n'
        )
    report, raw, fake = run(project, GitRunner(), git_policy_path=path)
    assert report["gate"]["exit_code"] == 1 and report["status"] == "error"
    assert raw is None and not any("scan" in args for args in fake.calls)
    assert "secret@" not in json.dumps(report)


@pytest.mark.parametrize(
    "kind",
    ["missing_git", "wrong_commit", "pypi", "extra_git", "api_failure", "bad_json"],
)
def test_native_git_coverage_and_api_failures_are_not_a_pass(project, kind):
    path = prepare(project)
    result, code = git_native(), 0
    package = result["results"][0]["packages"][-1]["package"]
    if kind == "missing_git":
        result = native()
    elif kind == "wrong_commit":
        package["commit"] = "b" * 40
    elif kind == "pypi":
        package.update(ecosystem="PyPI", name="private", version="0.1.0")
    elif kind == "extra_git":
        package["name"] = "https://github.com/example/other.git"
    elif kind == "api_failure":
        code = 129
    else:
        result = "not json"
    report, _, _ = run(project, GitRunner(result, code=code), git_policy_path=path)
    assert report["status"] == "error" and not report["gate"]["passed"]
    assert report["git_sources"][0]["advisory_query"] == "unavailable"


@pytest.mark.parametrize(
    "content",
    [
        "schema_version=1\ngit_sources=[]",
        "schema_version=2",
        'schema_version=1\n[[git_sources]]\npackage="private"\nrepository="https://token@github.com/x/y.git"\ncommit="'
        + SHA
        + '"',
    ],
)
def test_invalid_git_policy_stops_before_tools(project, content):
    path = prepare(project)
    path.write_text(content)
    report, _, fake = run(project, GitRunner(), git_policy_path=path)
    assert report["gate"]["exit_code"] == 1 and not fake.calls


def test_policy_outside_workflow_workspace_is_not_read(project, tmp_path):
    report, _, fake = run(
        project,
        GitRunner(),
        workspace_root=project,
        git_policy_path=project.parent / "external.toml",
    )
    assert report["diagnostics"] == ["git_policy_outside_workspace"] and not fake.calls


def test_policy_change_during_query_invalidates_result(project):
    path = prepare(project)

    class Changing(GitRunner):
        def __call__(self, args, cwd, timeout):
            value = super().__call__(args, cwd, timeout)
            if "scan" in args:
                path.write_text(path.read_text() + "\n# changed\n")
            return value

    report, _, _ = run(project, Changing(git_native()), git_policy_path=path)
    assert report["diagnostics"] == ["git_policy_changed_during_scan"]
    assert not report["gate"]["passed"]


def test_git_only_inventory_is_actually_scanned(project):
    path = prepare(project)
    lock = project / "uv.lock"
    root = lock.read_text().split('[[package]]\nname = "idna"')[0]
    lock.write_text(root + GIT)
    value = git_native()
    value["results"][0]["packages"] = value["results"][0]["packages"][-1:]
    report, _, fake = run(project, GitRunner(value), git_policy_path=path)
    assert report["status"] == "passed" and report["dependencies_scanned"] == 1
    flags = next(args for args in fake.requested if "scan" in args)
    assert len([arg for arg in flags if arg.startswith("--lockfile=")]) == 1
    assert any(arg.startswith("--lockfile=osv-scanner:") for arg in flags)
