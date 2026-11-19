"""Generic repository hygiene checks with explicit native outcomes."""

import dataclasses
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from engineering_ci import hygiene, selection

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
    fake = FakeRunner(stderr="native successful-tool diagnostic")
    result = report(path, base, checks=("docs",), runner=fake)
    assert result["status"] == "passed"
    assert by_name(result)["docs"]["exit_code"] == 0
    assert by_name(result)["docs"]["stderr"] == "native successful-tool diagnostic"
    tools = [call for call in fake.calls if call[0][0] != "git"]
    assert len(tools) == 1
    argv, data = tools[0]
    assert Path(argv[0]).stem == "lychee"
    # Lychee takes explicit inputs; stdin is not an accepted invocation.
    assert data is None
    assert "docs/guide.md" in argv


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
    assert by_name(result)["forwarding"]["reason"] is None


def test_forwarding_without_base_commit_is_blocked(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("from .other import VALUE\n", encoding="utf-8")
    value = changes(path, base)
    value = dataclasses.replace(value, base_commit=None)
    result = hygiene.run(
        value, config(path, ("forwarding",)), root=path, runner=FakeRunner()
    )
    assert result["status"] == "blocked"
    check = by_name(result)["forwarding"]
    assert check["reason"] == "base_commit_missing"
    assert check["status"] == "not_executed"
    assert check["execution_started"] is False
    assert check["observation"]["probes"] == []
    assert result["git"]["base_commit"] is None


def test_forwarding_unavailable_base_odb_fails(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("from .other import VALUE\n", encoding="utf-8")
    value = changes(path, base)
    missing = "0123456789abcdef0123456789abcdef01234567"
    value = dataclasses.replace(value, base_commit=missing)
    result = hygiene.run(
        value, config(path, ("forwarding",)), root=path, runner=FakeRunner()
    )
    check = by_name(result)["forwarding"]
    assert result["status"] == "failed"
    assert check["reason"] == "base_odb_unavailable"
    assert check["exit_code"] is None
    assert check["observation"]["probes"][-1]["exit_code"] != 0
    assert check["executable"] == "git"
    assert result["git"]["base_commit"] == missing


def test_forwarding_unreadable_base_tree_fails(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("from .other import VALUE\n", encoding="utf-8")

    def runner(argv, root, data=None):
        if argv[:3] == ["git", "rev-parse", "--verify"] and argv[-1].endswith(
            "^{tree}"
        ):
            return subprocess.CompletedProcess(
                argv, 128, "", "fatal: not a valid object name"
            )
        if argv[0] == "git":
            return hygiene._run(argv, root, data)
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = hygiene.run(
        changes(path, base), config(path, ("forwarding",)), root=path, runner=runner
    )
    check = by_name(result)["forwarding"]
    assert result["status"] == "failed"
    assert check["reason"] == "base_tree_unavailable"
    assert check["exit_code"] is None
    assert check["observation"]["probes"][-1]["exit_code"] == 128
    assert check["executable"] == "git"
    assert result["git"]["base_commit"] == base


def test_forwarding_base_content_unavailable_propagates_native_failure(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("from .other import VALUE\n", encoding="utf-8")
    blob = run(path, "rev-parse", f"{base}:src/pkg/sub.py").strip()

    def runner(argv, root, data=None):
        if argv == ["git", "cat-file", "-p", blob]:
            return subprocess.CompletedProcess(
                argv, 128, "", "fatal: blob exists but content unreadable"
            )
        if argv[0] == "git":
            return hygiene._run(argv, root, data)
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = hygiene.run(
        changes(path, base), config(path, ("forwarding",)), root=path, runner=runner
    )
    check = by_name(result)["forwarding"]
    assert result["status"] == "failed"
    assert check["reason"] == "base_content_unavailable"
    assert check["exit_code"] is None
    probe = check["observation"]["probes"][-1]
    assert probe["exit_code"] == 128
    assert "unreadable" in probe["stderr"]
    assert check["executable"] == "git"


def _ls_tree_blob(repo, tree, path):
    listing = subprocess.run(
        ["git", "ls-tree", "-r", "-z", tree],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    for record in listing.split("\0"):
        if not record:
            continue
        meta, _separator, name = record.partition("\t")
        if name != path:
            continue
        _mode, kind, oid = meta.split(" ")
        assert kind == "blob"
        return oid
    raise AssertionError(path)


def _drop_loose_blob(repo, oid):
    loose = Path(repo) / ".git" / "objects" / oid[:2] / oid[2:]
    assert loose.is_file()
    os.chmod(loose, stat.S_IWRITE)
    loose.unlink()
    missing = subprocess.run(
        ["git", "cat-file", "-e", oid],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert missing.returncode != 0


def test_forwarding_tree_entry_with_missing_blob_is_not_a_new_path(repo):
    """Commit, tree and ls-tree entry stay; deleting the blob must not pass.

    ``git cat-file -e <tree>:<path>`` exits 1 and ``git show`` exits 128 in this
    state. Those exits do not mean the path is absent.
    """
    path, base = repo
    relative = "src/pkg/sub.py"
    (path / relative).write_text("from .other import VALUE\n", encoding="utf-8")
    tree = run(path, "rev-parse", "--verify", f"{base}^{{commit}}^{{tree}}").strip()
    blob = _ls_tree_blob(path, tree, relative)
    _drop_loose_blob(path, blob)
    listed = subprocess.run(
        ["git", "ls-tree", "-r", "-z", tree, "--", relative],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    assert listed.split("\t", 1)[1].split("\0", 1)[0] == relative
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{tree}:{relative}"],
        cwd=path,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    shown = subprocess.run(
        ["git", "show", f"{tree}:{relative}"],
        cwd=path,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert exists.returncode == 1
    assert shown.returncode == 128
    result = hygiene.run(
        changes(path, base),
        config(path, ("forwarding",)),
        root=path,
        runner=FakeRunner(),
    )
    check = by_name(result)["forwarding"]
    assert result["status"] == "failed"
    assert check["status"] == "failed"
    assert check["reason"] == "base_content_unavailable"
    assert check["exit_code"] is None
    assert check["observation"]["probes"][-1]["exit_code"] == exists.returncode
    assert check["executable"] == "git"
    assert result["git"]["base_commit"] == base


def test_forwarding_tree_oid_base_is_not_a_commit(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("from .other import VALUE\n", encoding="utf-8")
    tree = run(path, "rev-parse", "--verify", f"{base}^{{tree}}").strip()
    peeled = subprocess.run(
        ["git", "rev-parse", "--verify", f"{tree}^{{commit}}"],
        cwd=path,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert peeled.returncode != 0
    value = dataclasses.replace(changes(path, base), base_commit=tree)
    result = hygiene.run(
        value, config(path, ("forwarding",)), root=path, runner=FakeRunner()
    )
    check = by_name(result)["forwarding"]
    assert result["status"] == "failed"
    assert check["reason"] == "base_not_commit"
    assert check["exit_code"] is None
    assert check["observation"]["probes"][-2]["exit_code"] == peeled.returncode
    assert check["executable"] == "git"
    assert result["git"]["base_commit"] == tree


def test_forwarding_tree_query_failure_is_explicit(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text("from .other import VALUE\n", encoding="utf-8")

    def runner(argv, root, data=None):
        if argv[:4] == ["git", "ls-tree", "-r", "-z"]:
            return subprocess.CompletedProcess(
                argv, 128, "", "fatal: not a tree object"
            )
        if argv[0] == "git":
            return hygiene._run(argv, root, data)
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = hygiene.run(
        changes(path, base), config(path, ("forwarding",)), root=path, runner=runner
    )
    check = by_name(result)["forwarding"]
    assert result["status"] == "failed"
    assert check["reason"] == "base_tree_unavailable"
    assert check["exit_code"] is None
    probe = check["observation"]["probes"][-1]
    assert probe["exit_code"] == 128
    assert "not a tree object" in probe["stderr"]
    assert check["executable"] == "git"


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


def test_omitted_checks_respects_tach_external_flag_without_silent_expansion(tmp_path):
    default_cfg = tmp_path / "default.toml"
    default_cfg.write_text("schema_version = 1\n", encoding="utf-8")
    loaded_default = hygiene.load_config(default_cfg)
    assert hygiene.effective_checks(loaded_default) == hygiene._DEFAULT_CHECKS
    assert "tach_external" not in hygiene.effective_checks(loaded_default)

    ext_cfg = tmp_path / "external.toml"
    ext_cfg.write_text(
        "schema_version = 1\n[tach]\nexternal = true\n", encoding="utf-8"
    )
    loaded_ext = hygiene.load_config(ext_cfg)
    assert loaded_ext.checks.count("tach_external") == 1
    assert hygiene.effective_checks(loaded_ext).count("tach_external") == 1


def test_shared_applicability_and_config_identity(repo):
    path, base = repo
    cfg_path = path / "ci-hygiene.toml"
    cfg = hygiene.load_config(cfg_path)
    identity = hygiene.config_identity(cfg_path, cfg, root=path)
    assert identity["path"] == "ci-hygiene.toml"
    assert identity["schema_version"] == 1
    assert identity["effective_check_config_ref"]["name"] == "engineering-ci.hygiene"

    (path / "docs/archive").mkdir(parents=True, exist_ok=True)
    (path / "docs/archive/old.md").write_text("# ignored\n", encoding="utf-8")
    (path / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    app_cfg = dataclasses.replace(cfg, checks=(*cfg.checks, "tach_external"))
    app = hygiene.check_applicability(
        changes(path, base),
        app_cfg,
        root=path,
        code_prefixes=("src/", "scripts/", "tests/"),
    )
    assert app["docs"]["status"] == "not_applicable"
    assert app["tach"]["status"] == "not_applicable"
    assert app["tach_external"]["status"] == "selected"
    assert app["tach_external"]["inputs"] == ["requirements-dev.txt"]
