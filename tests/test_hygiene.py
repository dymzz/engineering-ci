"""Generic repository hygiene checks with explicit native outcomes."""

import dataclasses
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from eci_core import hygiene, selection

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


@pytest.fixture
def repo(tmp_path):
    target = tmp_path / "repo"
    shutil.copytree(FIXTURE, target)
    run(target, "init", "-q", "-b", "main")
    run(target, "config", "user.email", "hygiene@example.invalid")
    run(target, "config", "user.name", "Hygiene Test")
    run(target, "add", "-A")
    run(target, "commit", "-q", "-m", "base")
    return target, run(target, "rev-parse", "HEAD").strip()


class FakeRunner:
    def __init__(self, code=0, stdout="", stderr="", missing=()):
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        self.missing = set(missing)
        self.calls = []

    def __call__(self, argv, root, data=None):
        self.calls.append((tuple(argv), data))
        if argv[0] == "git":
            return hygiene._run(argv, root, data)
        name = Path(argv[0]).stem
        if name in self.missing:
            return subprocess.CompletedProcess(argv, 127, "", f"missing {name}")
        return subprocess.CompletedProcess(argv, self.code, self.stdout, self.stderr)


def config(repo, checks=()):
    value = hygiene.load_config(repo / "ci-hygiene.toml")
    if checks:
        value = dataclasses.replace(value, checks=checks)
    return value


def changes(repo, base):
    return selection.changeset(repo, base=base, head="HEAD")


def report(repo, base, *, checks=(), runner=None):
    return hygiene.run(
        changes(repo, base),
        config(repo, checks),
        root=repo,
        runner=runner or FakeRunner(),
    )


def by_name(report_value):
    return {item["name"]: item for item in report_value["checks"]}


def test_whitespace_failure_is_reported_from_git(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 2   \n", encoding="utf-8")
    result = report(path, base, checks=("whitespace",))
    assert result["status"] == "failed"
    assert by_name(result)["whitespace"]["reason"] == "whitespace_errors"


def test_clean_whitespace_passes(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    result = report(path, base, checks=("whitespace",))
    assert result["status"] == "passed"
    assert by_name(result)["whitespace"]["exit_code"] == 0


def test_docs_without_applicable_files_is_not_applicable(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    fake = FakeRunner()
    result = report(path, base, checks=("docs",), runner=fake)
    assert by_name(result)["docs"]["status"] == "not_applicable"
    assert [call for call in fake.calls if call[0][0] != "git"] == []


def test_docs_runs_lychee_once_with_active_files(repo):
    path, base = repo
    (path / "docs/guide.md").write_text("# changed\n", encoding="utf-8")
    fake = FakeRunner()
    result = report(path, base, checks=("docs",), runner=fake)
    assert result["status"] == "passed"
    assert by_name(result)["docs"]["exit_code"] == 0
    tools = [call for call in fake.calls if call[0][0] != "git"]
    assert len(tools) == 1
    argv, data = tools[0]
    assert Path(argv[0]).stem == "lychee"
    assert "docs/guide.md" in data


def test_missing_link_checker_is_a_failure(repo):
    path, base = repo
    (path / "docs/guide.md").write_text("# changed\n", encoding="utf-8")
    fake = FakeRunner(missing=("lychee",))
    result = report(path, base, checks=("docs",), runner=fake)
    assert result["status"] == "failed"
    assert by_name(result)["docs"]["reason"] == "tool_unavailable"


def test_broken_links_fail(repo):
    path, base = repo
    (path / "docs/guide.md").write_text("# changed\n", encoding="utf-8")
    result = report(path, base, checks=("docs",), runner=FakeRunner(code=2))
    assert result["status"] == "failed"
    assert by_name(result)["docs"]["reason"] == "lychee_findings"


def test_vulture_findings_are_advisory_not_failure(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    result = report(
        path,
        base,
        checks=("vulture",),
        runner=FakeRunner(code=3, stdout="dead.py:1: unused variable 'x'"),
    )
    assert result["status"] == "passed"
    assert by_name(result)["vulture"]["status"] == "advisory"
    assert by_name(result)["vulture"]["exit_code"] == 3


def test_vulture_execution_error_fails(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    result = report(path, base, checks=("vulture",), runner=FakeRunner(code=1))
    assert result["status"] == "failed"
    assert by_name(result)["vulture"]["reason"] == "vulture_failed"


def test_tach_requires_its_config(repo):
    path, base = repo
    (path / "tach.toml").unlink()
    (path / "src/pkg/sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    result = report(path, base, checks=("tach",))
    assert result["status"] == "failed"
    assert by_name(result)["tach"]["reason"] == "tach_config_missing"


def test_tach_failure_propagates(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    result = report(path, base, checks=("tach",), runner=FakeRunner(code=1))
    assert result["status"] == "failed"
    assert by_name(result)["tach"]["reason"] == "tach_check_failed"


def test_tach_external_runs_separately_when_enabled(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    value = dataclasses.replace(
        config(path, ("tach",)),
        tach=dataclasses.replace(config(path).tach, external=True),
    )
    fake = FakeRunner()
    result = hygiene.run(changes(path, base), value, root=path, runner=fake)
    assert result["status"] == "passed"
    assert by_name(result)["tach"]["status"] == "passed"
    assert by_name(result)["tach_external"]["status"] == "passed"
    tools = [call[0] for call in fake.calls if call[0][0] != "git"]
    assert tools == [("tach", "check"), ("tach", "check-external")]


def test_tach_external_failure_is_independent_of_tach_check(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    value = dataclasses.replace(
        config(path, ("tach",)),
        tach=dataclasses.replace(config(path).tach, external=True),
    )

    def runner(argv, root, data=None):
        if argv[0] == "git":
            return hygiene._run(argv, root, data)
        if argv[1:] == ["check-external"]:
            return subprocess.CompletedProcess(argv, 1, "", "external failed")
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = hygiene.run(changes(path, base), value, root=path, runner=runner)
    assert result["status"] == "failed"
    assert by_name(result)["tach"]["status"] == "passed"
    assert by_name(result)["tach_external"]["reason"] == "tach_external_failed"


def test_forwarding_module_is_reported_without_failing(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("from .other import VALUE\n", encoding="utf-8")
    result = report(path, base, checks=("forwarding",))
    assert result["status"] == "passed"
    assert by_name(result)["forwarding"]["status"] == "advisory"
    assert any(
        "src/pkg/sub.py" in item for item in by_name(result)["forwarding"]["findings"]
    )


def test_forwarding_new_module_is_not_a_failure(repo):
    path, base = repo
    (path / "src/pkg/new.py").write_text("from .other import VALUE\n", encoding="utf-8")
    result = report(path, base, checks=("forwarding",))
    assert result["status"] == "passed"
    assert by_name(result)["forwarding"]["status"] == "passed"


def test_report_records_git_identity_and_changes(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    result = report(path, base, checks=("whitespace",))
    assert result["git"]["base_commit"]
    assert result["git"]["head_commit"] == base
    assert "src/pkg/sub.py" in result["git"]["changed_paths"]


def test_cli_outputs_json_and_preserves_failure(repo, capsys):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 2   \n", encoding="utf-8")
    assert (
        hygiene.main(
            [
                "--root",
                str(path),
                "--base",
                base,
                "--config",
                str(path / "ci-hygiene.toml"),
                "--json",
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "engineering-ci-hygiene-report"
    assert payload["status"] == "failed"
