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


def test_whitespace_git_failure_preserves_native_diagnostic(repo):
    path, _ = repo
    tree = run(path, "write-tree").strip()

    result = hygiene.check_git_whitespace(path, base="missing-base-tree", tree=tree)

    assert result["status"] == "failed"
    assert result["reason"] == "git_diff_failed"
    assert result["exit_code"] == 128
    assert "missing-base-tree" in result["stderr"]


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


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("from .other import VALUE\n__all__ = ['VALUE']\n", True),
        ("from .other import VALUE\n__all__: list[str] = ['VALUE']\n", True),
        ("from .other import VALUE\n__all__ = __all__ = ['VALUE']\n", True),
        ("from .other import VALUE\n__all__ = ALIAS = ['VALUE']\n", False),
        ("from .other import VALUE\n__all__ += ['VALUE']\n", False),
        ("import other\n__all__ = ['other']\n", False),
        ("from .other import VALUE\nVALUE = 1\n", False),
    ],
)
def test_pure_reexport_retains_original_export_list_semantics(content, expected):
    assert hygiene._pure_reexport(content) is expected


@pytest.mark.parametrize(
    "export_list", ["__all__ = ['VALUE']", "__all__: list[str] = ['VALUE']"]
)
def test_forwarding_with_export_list_reports_original_advisory(repo, export_list):
    path, base = repo
    (path / "src/pkg/sub.py").write_text(
        f"from .other import VALUE\n{export_list}\n", encoding="utf-8"
    )
    result = report(path, base, checks=("forwarding",))
    assert by_name(result)["forwarding"]["status"] == "advisory"
    assert "src/pkg/sub.py" in by_name(result)["forwarding"]["findings"][0]


@pytest.mark.parametrize(
    ("previous", "expected"),
    [
        ("", "passed"),
        ("# no statements\n", "passed"),
        ('"""A module docstring."""\n', "advisory"),
        ("from .other import VALUE\n__all__ = ['VALUE']\n", "passed"),
        ("VALUE = 1\n", "advisory"),
    ],
)
def test_forwarding_compares_original_base_substance(repo, previous, expected):
    path, _ = repo
    source = path / "src/pkg/sub.py"
    source.write_text(previous, encoding="utf-8")
    run(path, "add", "--", "src/pkg/sub.py")
    run(path, "commit", "--allow-empty", "-qm", "set comparison base")
    base = run(path, "rev-parse", "HEAD").strip()
    source.write_text("from .other import VALUE\n", encoding="utf-8")
    result = report(path, base, checks=("forwarding",))
    assert by_name(result)["forwarding"]["status"] == expected


def test_forwarding_excludes_package_initializers_from_scope_and_execution(repo):
    path, _ = repo
    initializer = path / "src/pkg/__init__.py"
    initializer.write_text("VALUE = 1\n", encoding="utf-8")
    run(path, "add", "--", "src/pkg/__init__.py")
    run(path, "commit", "-qm", "set package initializer base")
    base = run(path, "rev-parse", "HEAD").strip()
    initializer.write_text("from .other import VALUE\n", encoding="utf-8")
    change = changes(path, base)
    value = config(path, ("forwarding",))
    scope = hygiene.check_applicability(change, value, root=path)["forwarding"]
    assert scope["status"] == "not_applicable"
    assert scope["inputs"] == []
    result = hygiene.run(change, value, root=path, runner=FakeRunner())
    assert by_name(result)["forwarding"]["status"] == "not_applicable"


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


def test_configured_dagger_scan_roots_select_code_checks(repo):
    path, _base = repo
    (path / "tach.toml").write_text(
        (path / "tach.toml")
        .read_text(encoding="utf-8")
        .replace('source_roots = ["src"]', 'source_roots = ["src", ".dagger/src"]'),
        encoding="utf-8",
    )
    (path / "ci-hygiene.toml").write_text(
        (path / "ci-hygiene.toml")
        .read_text(encoding="utf-8")
        .replace('paths = ["src"]', 'paths = ["src", ".dagger/src"]'),
        encoding="utf-8",
    )
    run(path, "add", "tach.toml", "ci-hygiene.toml")
    run(path, "commit", "-qm", "configure dagger scan roots")
    mid = run(path, "rev-parse", "HEAD").strip()

    module = path / ".dagger/src/tool.py"
    module.parent.mkdir(parents=True)
    module.write_text("VALUE = 1\n", encoding="utf-8")
    app = hygiene.check_applicability(
        changes(path, mid), config(path, ("vulture", "tach")), root=path
    )
    assert app["tach"]["status"] == "selected"
    assert ".dagger/src/tool.py" in app["tach"]["inputs"]
    assert app["vulture"]["status"] == "selected"
    assert ".dagger/src/tool.py" in app["vulture"]["inputs"]


def test_vulture_config_change_does_not_select_docs_or_tach(repo):
    path, base = repo
    cfg_path = path / "ci-hygiene.toml"
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8").replace(
            "min_confidence = 60", "min_confidence = 80"
        ),
        encoding="utf-8",
    )
    loaded = hygiene.load_config(cfg_path)
    loaded = dataclasses.replace(loaded, checks=("vulture", "docs", "tach"))
    app = hygiene.check_applicability(changes(path, base), loaded, root=path)
    assert app["vulture"]["reason"] == "hygiene_vulture_config_changed"
    assert app["docs"]["status"] == "not_applicable"
    assert app["tach"]["status"] == "not_applicable"


def test_offline_lychee_does_not_claim_external_coverage(repo):
    path, base = repo
    (path / "docs/guide.md").write_text("# changed\n", encoding="utf-8")
    loaded = config(path, ("docs",))
    loaded = dataclasses.replace(
        loaded, lychee=dataclasses.replace(loaded.lychee, arguments=("--offline",))
    )
    result = hygiene.run(changes(path, base), loaded, root=path, runner=FakeRunner())
    docs = by_name(result)["docs"]
    assert docs["status"] == "passed"
    assert docs["link_scope"] == "offline"
    assert "external URLs were not requested" in docs["link_scope_note"]


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


def test_docs_config_trigger_rechecks_all_without_contaminating_inputs(repo):
    path, _base = repo
    hyg = path / "ci-hygiene.toml"
    hyg.write_text(
        "schema_version = 1\n"
        "[docs]\n"
        'suffixes = [".md"]\n'
        'excluded_prefixes = ["docs/archive/"]\n\n'
        "[lychee]\n"
        'executable = "lychee"\n'
        'arguments = ["--no-progress"]\n',
        encoding="utf-8",
    )
    run(path, "add", "ci-hygiene.toml")
    run(path, "commit", "-qm", "baseline docs config")
    cfg_base = run(path, "rev-parse", "HEAD").strip()

    hyg.write_text(
        "schema_version = 1\n"
        "[docs]\n"
        'suffixes = [".md", ".html"]\n'
        'excluded_prefixes = ["docs/archive/"]\n\n'
        "[lychee]\n"
        'executable = "lychee"\n'
        'arguments = ["--offline", "--no-progress"]\n',
        encoding="utf-8",
    )
    cfg = hygiene.load_config(hyg)
    value = changes(path, cfg_base)
    app = hygiene.check_applicability(value, cfg, root=path, hygiene_config_path=hyg)
    assert app["docs"]["status"] == "selected"
    assert app["docs"]["recheck_all"] is True
    assert "ci-hygiene.toml" not in app["docs"]["inputs"]
    assert app["forwarding"]["status"] == "not_applicable"

    fake = FakeRunner()
    result = hygiene.run(value, cfg, root=path, runner=fake, hygiene_config_path=hyg)
    docs_check = by_name(result)["docs"]
    assert result["status"] == "passed"
    assert docs_check["status"] == "passed"
    lychee_calls = [argv for argv, _ in fake.calls if argv and argv[0] == "lychee"]
    assert len(lychee_calls) == 1
    assert any(arg.endswith("docs/guide.md") for arg in lychee_calls[0])
    assert any(arg.endswith("README.md") for arg in lychee_calls[0])
    assert "ci-hygiene.toml" not in lychee_calls[0]


def test_forwarding_config_trigger_keeps_python_inputs_and_normalizes_equivalence(
    repo,
):
    path, _base = repo
    hyg = path / "ci-hygiene.toml"
    hyg.write_text(
        'schema_version = 1\n[forwarding]\nsource_prefix = "src/"\n',
        encoding="utf-8",
    )
    run(path, "add", "ci-hygiene.toml")
    run(path, "commit", "-qm", "explicit forwarding defaults")
    cfg_base = run(path, "rev-parse", "HEAD").strip()

    # Rewriting an equivalent config (comment + omitted default section) must not false-trigger.
    hyg.write_text("# comment only\nschema_version = 1\n", encoding="utf-8")
    run(path, "add", "ci-hygiene.toml")
    run(path, "commit", "-qm", "equivalent normalized hygiene config")
    equiv_cfg = hygiene.load_config(hyg)
    equiv_changes = changes(path, cfg_base)
    equiv_app = hygiene.check_applicability(
        equiv_changes, equiv_cfg, root=path, hygiene_config_path=hyg
    )
    assert equiv_app["forwarding"]["status"] == "not_applicable"
    assert equiv_app["forwarding"]["reason"] == "no_python_changes"
    assert equiv_app["docs"]["status"] == "not_applicable"
    assert equiv_app["tach"]["status"] == "not_applicable"

    # Config-only [forwarding] prefix change without Python changes stays not_applicable — never empty PASS or whole-tree scan.
    head_base = run(path, "rev-parse", "HEAD").strip()
    hyg.write_text(
        "schema_version = 1\n"
        'checks = ["forwarding"]\n'
        "[forwarding]\n"
        'source_prefix = "src/pkg/"\n',
        encoding="utf-8",
    )
    cfg_only_cfg = hygiene.load_config(hyg)
    cfg_only_changes = changes(path, head_base)
    cfg_only_app = hygiene.check_applicability(
        cfg_only_changes, cfg_only_cfg, root=path, hygiene_config_path=hyg
    )
    assert cfg_only_app["forwarding"]["status"] == "not_applicable"
    assert cfg_only_app["forwarding"]["inputs"] == []
    assert cfg_only_app["forwarding"]["reason"] == (
        "hygiene_forwarding_config_changed_no_targets"
    )
    cfg_only_runner = FakeRunner()
    cfg_only_res = hygiene.run(
        cfg_only_changes,
        cfg_only_cfg,
        root=path,
        runner=cfg_only_runner,
        hygiene_config_path=hyg,
    )
    assert by_name(cfg_only_res)["forwarding"]["status"] == "not_applicable"
    assert [argv for argv, _ in cfg_only_runner.calls] == [("git", "ls-files", "-z")]

    # Incomplete source stays unknown instead of not_applicable.
    incomplete_app = hygiene.check_applicability(
        dataclasses.replace(cfg_only_changes, complete=False),
        cfg_only_cfg,
        root=path,
        hygiene_config_path=hyg,
    )
    assert incomplete_app["forwarding"]["status"] == "unknown"

    # Real [forwarding] prefix change together with a changed Python module analyzes that real candidate.
    (path / "src/pkg/sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    changed_changes = changes(path, head_base)
    changed_app = hygiene.check_applicability(
        changed_changes, cfg_only_cfg, root=path, hygiene_config_path=hyg
    )
    assert changed_app["forwarding"]["status"] == "selected"
    assert changed_app["forwarding"]["reason"] == "hygiene_forwarding_config_changed"
    assert changed_app["forwarding"]["inputs"] == ["src/pkg/sub.py"]

    res = hygiene.run(
        changed_changes,
        cfg_only_cfg,
        root=path,
        runner=FakeRunner(),
        hygiene_config_path=hyg,
    )
    assert res["status"] == "passed"
    assert by_name(res)["forwarding"]["status"] == "passed"

    (path / "src/pkg/sub.py").write_text(
        "from pkg.other import VALUE\n\n__all__ = ['VALUE']\n", encoding="utf-8"
    )
    advisory_res = hygiene.run(
        changes(path, head_base),
        cfg_only_cfg,
        root=path,
        runner=FakeRunner(),
        hygiene_config_path=hyg,
    )
    assert by_name(advisory_res)["forwarding"]["status"] == "advisory"


def test_tach_config_trigger_and_snapshot_isolation_and_fail_closed_fallback(repo):
    from engineering_ci import selection

    path, _base = repo
    hyg = path / "ci-hygiene.toml"
    hyg.write_text(
        "schema_version = 1\n"
        'checks = ["vulture", "tach", "tach_external"]\n'
        "[vulture]\n"
        'paths = ["src"]\n'
        "min_confidence = 60\n\n"
        "[tach]\n"
        'path = "tach.toml"\n'
        "external = true\n",
        encoding="utf-8",
    )
    run(path, "add", "ci-hygiene.toml")
    run(path, "commit", "-qm", "tach and vulture baseline")
    cfg_base = run(path, "rev-parse", "HEAD").strip()

    # Commit a [tach] rule change, then dirty worktree differently and evaluate commit snapshot.
    hyg.write_text(
        "schema_version = 1\n"
        'checks = ["vulture", "tach", "tach_external"]\n'
        "[vulture]\n"
        'paths = ["src"]\n'
        "min_confidence = 60\n\n"
        "[tach]\n"
        'executable = "custom-tach"\n'
        'path = "tach.toml"\n'
        "external = true\n",
        encoding="utf-8",
    )
    run(path, "add", "ci-hygiene.toml")
    run(path, "commit", "-qm", "change tach executable")
    commit_cfg = hygiene.load_config(hyg)

    # Dirty worktree alters [vulture] instead of [tach]; commit snapshot must read execution_tree!
    hyg.write_text(
        "schema_version = 1\n"
        'checks = ["vulture", "tach", "tach_external"]\n'
        "[vulture]\n"
        'paths = ["src"]\n'
        "min_confidence = 80\n\n"
        "[tach]\n"
        'path = "tach.toml"\n'
        "external = true\n",
        encoding="utf-8",
    )
    commit_changes = selection.changeset(path, base=cfg_base, mode="commit")
    app = hygiene.check_applicability(
        commit_changes, commit_cfg, root=path, hygiene_config_path=hyg
    )
    assert app["tach"]["status"] == "selected"
    assert app["tach_external"]["status"] == "selected"
    assert "ci-hygiene.toml" not in app["tach"]["inputs"]
    assert app["vulture"]["status"] == "not_applicable"

    # Unreadable/corrupt base config or missing base_commit falls back conservatively.
    corrupt_base = dataclasses.replace(
        changes(path, cfg_base),
        base_commit="0" * 40,
        paths=("ci-hygiene.toml",),
    )
    conservative = hygiene.check_applicability(
        corrupt_base, commit_cfg, root=path, hygiene_config_path=hyg
    )
    assert conservative["tach"]["status"] == "selected"
    assert conservative["tach_external"]["status"] == "selected"
    assert conservative["vulture"]["status"] == "selected"


def test_dagger_source_change_does_not_claim_src_only_tach_or_vulture(repo):
    """Selection may map `.dagger/src/` tests; Tach/Vulture follow tool roots only."""
    path, _base = repo
    hyg = path / "ci-hygiene.toml"
    hyg.write_text(
        "schema_version = 1\n"
        'checks = ["vulture", "tach", "tach_external", "whitespace"]\n'
        "[vulture]\n"
        'paths = ["src"]\n'
        "min_confidence = 60\n\n"
        "[tach]\n"
        'path = "tach.toml"\n'
        "external = true\n",
        encoding="utf-8",
    )
    dagger_file = path / ".dagger/src/engineering_ci_module/sample.py"
    dagger_file.parent.mkdir(parents=True, exist_ok=True)
    dagger_file.write_text("VALUE = 1\n", encoding="utf-8")
    run(path, "add", "ci-hygiene.toml", ".dagger/src/engineering_ci_module/sample.py")
    run(path, "commit", "-qm", "add dagger module sample")
    mid = run(path, "rev-parse", "HEAD").strip()

    dagger_file.write_text("VALUE = 2\n", encoding="utf-8")
    cfg = hygiene.load_config(hyg)
    value = changes(path, mid)
    assert ".dagger/src/engineering_ci_module/sample.py" in value.paths
    app = hygiene.check_applicability(
        value,
        cfg,
        root=path,
        code_prefixes=("src/", "scripts/", "tests/", ".dagger/src/"),
        hygiene_config_path=hyg,
    )
    assert app["whitespace"]["status"] == "selected"
    assert app["vulture"]["status"] == "not_applicable"
    assert app["tach"]["status"] == "not_applicable"
    assert app["tach_external"]["status"] == "not_applicable"

    run(path, "checkout", "--", ".dagger/src/engineering_ci_module/sample.py")
    (path / "src/pkg/sub.py").write_text("VALUE = 9\n", encoding="utf-8")
    src_value = changes(path, mid)
    src_app = hygiene.check_applicability(
        src_value,
        cfg,
        root=path,
        code_prefixes=("src/", "scripts/", "tests/", ".dagger/src/"),
        hygiene_config_path=hyg,
    )
    assert src_app["vulture"]["status"] == "selected"
    assert src_app["tach"]["status"] == "selected"
    assert "src/pkg/sub.py" in src_app["tach"]["inputs"]


def test_is_offline_lychee_arguments_respects_overrides_and_separator():
    assert hygiene.is_offline_lychee_arguments(["--offline", "--no-progress"]) is True
    assert hygiene.is_offline_lychee_arguments(["--offline=true"]) is True
    assert hygiene.is_offline_lychee_arguments(["--offline=false"]) is False
    assert (
        hygiene.is_offline_lychee_arguments(["--offline", "--offline=false"]) is False
    )
    assert (
        hygiene.is_offline_lychee_arguments(["--offline=false", "--offline"]) is False
    )
    assert hygiene.is_offline_lychee_arguments(["--", "--offline"]) is False
    assert hygiene.is_offline_lychee_arguments(["--no-progress"]) is False


def test_tool_scan_roots_normalization_lazy_tach_and_fail_closed_unknown(repo):
    path, base = repo

    # 1. Normalization of ".", "./src", and Windows separators; rejection of invalid/escaping roots.
    assert hygiene._normalize_scan_root(".") == ""
    assert hygiene._normalize_scan_root("./") == ""
    assert hygiene._normalize_scan_root("./src") == "src"
    assert hygiene._normalize_scan_root(".\\src\\") == "src"
    assert hygiene._path_under_roots("src/pkg.py", ("",)) is True
    assert hygiene._path_under_roots("src/pkg.py", ("src",)) is True
    assert hygiene._path_under_roots("src/pkg/sub.py", ("src",)) is True
    for bad in (
        "",
        "   ",
        "../outside",
        "/etc",
        "C:/src",
        "./C:/outside",
        "./C:outside",
        ".\\C:\\outside",
        ".\\C:outside",
        "./C:\\outside",
        ".\\C:/outside",
        "./c:outside",
        "//server/share",
        "\\\\server\\share",
        " /etc",
        " ./C:/outside",
        "--min-confidence",
        "./--help",
        "./--version",
        ".\\.\\--version",
        "./-h",
        ".git",
    ):
        assert hygiene._normalize_scan_root(bad) is None

    # 2. Vulture with "." and "./src" selects src/pkg/sub.py and isolates normalized roots behind "--".
    (path / "src/pkg/sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    src_changes = changes(path, base)
    for configured_paths, expected_arg in (((".",), "."), (("./src",), "src")):
        v_cfg = dataclasses.replace(
            config(path, ("vulture",)),
            vulture=dataclasses.replace(config(path).vulture, paths=configured_paths),
        )
        app = hygiene.check_applicability(src_changes, v_cfg, root=path)
        assert app["vulture"]["status"] == "selected"
        assert "src/pkg/sub.py" in app["vulture"]["inputs"]
        fake = FakeRunner()
        res = hygiene.run(src_changes, v_cfg, root=path, runner=fake)
        assert res["status"] == "passed"
        v_calls = [call[0] for call in fake.calls if call[0][0] == "vulture"]
        assert v_calls == [("vulture", "--min-confidence", "60", "--", expected_arg)]

    # Invalid/escaping/option-like Vulture paths fail closed (unknown -> failed/not_started), never false N/A.
    for invalid_v_paths in (
        ("../outside",),
        ("./--help",),
        ("./--version",),
        ("./C:/outside",),
        ("./C:outside",),
        (".\\C:\\outside",),
    ):
        bad_v_cfg = dataclasses.replace(
            config(path, ("vulture",)),
            vulture=dataclasses.replace(config(path).vulture, paths=invalid_v_paths),
        )
        bad_v_app = hygiene.check_applicability(src_changes, bad_v_cfg, root=path)
        assert bad_v_app["vulture"]["status"] == "unknown"
        assert bad_v_app["vulture"]["reason"] == "vulture_paths_invalid"
        bad_v_runner = FakeRunner()
        bad_v_res = hygiene.run(src_changes, bad_v_cfg, root=path, runner=bad_v_runner)
        assert bad_v_res["status"] == "failed"
        assert by_name(bad_v_res)["vulture"]["reason"] == "vulture_paths_invalid"
        assert by_name(bad_v_res)["vulture"]["execution_started"] is False
        assert not any(call[0][0] == "vulture" for call in bad_v_runner.calls)

    # 3. Lazy Tach config reading: corrupt UTF-8 tach.toml does not affect runs with Tach disabled.
    original_tach = (path / "tach.toml").read_text(encoding="utf-8")
    (path / "tach.toml").write_bytes(b"\xff\xfe\x00not-utf8")
    no_tach_app = hygiene.check_applicability(
        src_changes, config(path, ("whitespace", "forwarding")), root=path
    )
    assert no_tach_app["whitespace"]["status"] == "selected"
    assert no_tach_app["forwarding"]["status"] == "selected"

    # When Tach is enabled, corrupt UTF-8 fails closed as unknown -> failed/not_started (no crash, no N/A).
    tach_cfg = dataclasses.replace(
        config(path, ("tach", "tach_external")),
        tach=dataclasses.replace(config(path).tach, external=True),
    )
    bad_tach_app = hygiene.check_applicability(src_changes, tach_cfg, root=path)
    assert bad_tach_app["tach"]["status"] == "unknown"
    assert bad_tach_app["tach"]["reason"] == "tach_config_invalid"
    assert bad_tach_app["tach_external"]["status"] == "unknown"
    bad_tach_res = hygiene.run(src_changes, tach_cfg, root=path, runner=FakeRunner())
    assert bad_tach_res["status"] == "failed"
    assert by_name(bad_tach_res)["tach"]["reason"] == "tach_config_invalid"
    assert by_name(bad_tach_res)["tach"]["execution_started"] is False

    # Invalid source_roots in tach.toml fails closed as unknown -> failed/not_started.
    for bad_roots_toml in (
        'source_roots = ["../escape"]\n',
        'source_roots = ["./C:outside"]\n',
    ):
        (path / "tach.toml").write_text(bad_roots_toml, encoding="utf-8")
        bad_roots_app = hygiene.check_applicability(src_changes, tach_cfg, root=path)
        assert bad_roots_app["tach"]["status"] == "unknown"
        assert bad_roots_app["tach"]["reason"] == "tach_source_roots_invalid"
        bad_roots_res = hygiene.run(
            src_changes, tach_cfg, root=path, runner=FakeRunner()
        )
        assert bad_roots_res["status"] == "failed"
        assert by_name(bad_roots_res)["tach"]["reason"] == "tach_source_roots_invalid"

    # Missing tach.toml with only non-src doc change still fails closed (never false N/A).
    run(path, "checkout", "--", "src/pkg/sub.py")
    (path / "docs/guide.md").write_text("# changed doc\n", encoding="utf-8")
    (path / "tach.toml").unlink()
    doc_only_changes = changes(path, base)
    missing_app = hygiene.check_applicability(doc_only_changes, tach_cfg, root=path)
    assert missing_app["tach"]["status"] == "unknown"
    assert missing_app["tach"]["reason"] == "tach_config_missing"
    missing_res = hygiene.run(
        doc_only_changes, tach_cfg, root=path, runner=FakeRunner()
    )
    assert missing_res["status"] == "failed"
    assert by_name(missing_res)["tach"]["reason"] == "tach_config_missing"

    # 4. Explicit multi-root vs single-root Dagger positive/negative applicability + omitted default (".").
    run(path, "checkout", "--", "docs/guide.md")
    dagger_file = path / ".dagger/src/engineering_ci_module/sample.py"
    dagger_file.parent.mkdir(parents=True, exist_ok=True)
    dagger_file.write_text("VALUE = 1\n", encoding="utf-8")
    (path / "tach.toml").write_text(original_tach, encoding="utf-8")
    run(path, "add", ".dagger/src/engineering_ci_module/sample.py", "tach.toml")
    run(path, "commit", "-qm", "add dagger sample")
    dagger_base = run(path, "rev-parse", "HEAD").strip()

    dagger_file.write_text("VALUE = 2\n", encoding="utf-8")
    dagger_changes = changes(path, dagger_base)
    single_app = hygiene.check_applicability(
        dagger_changes, config(path, ("tach",)), root=path
    )
    assert single_app["tach"]["status"] == "not_applicable"

    # Multi-root config explicitly covering ./src and .dagger/src selects tach on Dagger edit.
    (path / "tach.toml").write_text(
        original_tach.replace(
            'source_roots = ["src"]', 'source_roots = ["./src", ".dagger/src"]'
        ),
        encoding="utf-8",
    )
    multi_app = hygiene.check_applicability(
        dagger_changes, config(path, ("tach",)), root=path
    )
    assert multi_app["tach"]["status"] == "selected"
    assert ".dagger/src/engineering_ci_module/sample.py" in multi_app["tach"]["inputs"]

    # Valid tach.toml omitting source_roots uses Tach's native repo-root default (".").
    (path / "tach.toml").write_text(
        original_tach.replace('source_roots = ["src"]\n', ""), encoding="utf-8"
    )
    omitted_app = hygiene.check_applicability(
        dagger_changes, config(path, ("tach",)), root=path
    )
    assert omitted_app["tach"]["status"] == "selected"

    # 5. Multi-ref push resolves per-ref tach.toml union cleanly and fails closed if any active ref is corrupt.
    from engineering_ci import selection

    (path / "tach.toml").write_text(original_tach, encoding="utf-8")
    run(path, "checkout", "--", ".dagger/src/engineering_ci_module/sample.py")
    run(path, "switch", "-qc", "multi-ok", dagger_base)
    (path / "docs/guide.md").write_text("# ref a doc\n", encoding="utf-8")
    run(path, "add", "docs/guide.md")
    run(path, "commit", "-qm", "ref a doc")
    ok_commit = run(path, "rev-parse", "HEAD").strip()

    run(path, "switch", "-qc", "multi-corrupt", dagger_base)
    (path / "tach.toml").write_bytes(b"\xff\xfe\x00bad")
    run(path, "add", "tach.toml")
    run(path, "commit", "-qm", "ref b corrupt tach")
    bad_commit = run(path, "rev-parse", "HEAD").strip()

    run(path, "switch", "-q", "main")
    ok_multi = selection.changeset_multi(
        path, [("refs/heads/multi-ok", ok_commit, "refs/heads/multi-ok", dagger_base)]
    )
    ok_multi_app = hygiene.check_applicability(
        ok_multi, config(path, ("tach",)), root=path
    )
    assert ok_multi_app["tach"]["status"] == "not_applicable"

    bad_multi = selection.changeset_multi(
        path,
        [
            ("refs/heads/multi-ok", ok_commit, "refs/heads/multi-ok", dagger_base),
            (
                "refs/heads/multi-corrupt",
                bad_commit,
                "refs/heads/multi-corrupt",
                dagger_base,
            ),
        ],
    )
    bad_multi_app = hygiene.check_applicability(
        bad_multi, config(path, ("tach",)), root=path
    )
    assert bad_multi_app["tach"]["status"] == "unknown"
    assert bad_multi_app["tach"]["reason"] == "tach_config_invalid"


def test_tach_custom_config_path_is_refused_fail_closed_and_argparse_delimiter(repo):
    import sys

    path, base = repo
    (path / "src/pkg/sub.py").write_text("VALUE = 2\n", encoding="utf-8")
    # Default clean tach.toml exists alongside custom failing tach-alt.toml with different roots.
    (path / "tach-alt.toml").write_text(
        'source_roots = [".dagger/src"]\n'
        "exact = true\n"
        '[[modules]]\npath = "sample"\ndepends_on = []\n',
        encoding="utf-8",
    )
    norm_hyg = path / "ci-hygiene-norm.toml"
    norm_hyg.write_text(
        'schema_version = 1\nchecks = ["tach"]\n[tach]\npath = "./tach.toml"\n',
        encoding="utf-8",
    )
    assert hygiene.load_config(norm_hyg).tach.path == "tach.toml"

    for custom in ("tach-alt.toml", "sub/tach.toml", "pyproject.toml"):
        bad_hyg = path / "ci-hygiene-custom.toml"
        bad_hyg.write_text(
            f'schema_version = 1\nchecks = ["tach", "tach_external"]\n[tach]\npath = "{custom}"\nexternal = true\n',
            encoding="utf-8",
        )
        with pytest.raises(hygiene.HygieneError, match="tach_config_path_unsupported"):
            hygiene.load_config(bad_hyg)

        # Even if constructed directly in Python, neither applicability nor run falls back to tach.toml.
        direct_cfg = dataclasses.replace(
            config(path, ("tach", "tach_external")),
            tach=hygiene.TachConfig(executable="tach", path=custom, external=True),
        )
        app = hygiene.check_applicability(changes(path, base), direct_cfg, root=path)
        assert app["tach"]["status"] == "unknown"
        assert app["tach"]["reason"] == "tach_config_path_unsupported"
        assert app["tach_external"]["status"] == "unknown"
        assert app["tach_external"]["reason"] == "tach_config_path_unsupported"
        runner = FakeRunner()
        res = hygiene.run(changes(path, base), direct_cfg, root=path, runner=runner)
        assert res["status"] == "failed"
        assert by_name(res)["tach"]["reason"] == "tach_config_path_unsupported"
        assert by_name(res)["tach"]["execution_started"] is False
        assert by_name(res)["tach_external"]["reason"] == "tach_config_path_unsupported"
        assert not any(call[0][0] == "tach" for call in runner.calls)

    # Real subprocess check verifying argparse option delimiter "--" before positional scan roots.
    arg_script = path / "vulture_probe.py"
    arg_script.write_text(
        "import argparse, json, sys\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--min-confidence', type=int, required=True)\n"
        "p.add_argument('paths', nargs='+')\n"
        "ns = p.parse_args()\n"
        "print(json.dumps({'min_confidence': ns.min_confidence, 'paths': ns.paths}))\n",
        encoding="utf-8",
    )

    recorded_probe_argv = []

    def real_subprocess_wrapper(argv, cwd, data=None):
        recorded_probe_argv.append(list(argv))
        if argv[0] == "git":
            return hygiene._run(argv, cwd, data)
        return hygiene._run([sys.executable, str(arg_script), *argv[1:]], cwd, data)

    v_cfg = dataclasses.replace(
        config(path, ("vulture",)),
        vulture=dataclasses.replace(config(path).vulture, paths=("./src", ".")),
    )
    probe_res = hygiene.run(
        changes(path, base), v_cfg, root=path, runner=real_subprocess_wrapper
    )
    assert probe_res["status"] == "passed"
    v_entry = by_name(probe_res)["vulture"]
    assert [
        "vulture",
        "--min-confidence",
        "60",
        "--",
        "src",
        ".",
    ] in recorded_probe_argv
    assert json.loads(v_entry["stdout"]) == {
        "min_confidence": 60,
        "paths": ["src", "."],
    }


@pytest.mark.skipif(
    shutil.which("vulture") is None,
    reason="vulture binary unavailable on PATH",
)
def test_real_vulture_cli_scans_normalized_roots_with_delimiter(repo):
    path, base = repo
    (path / "src/pkg/sub.py").write_text(
        "def _unused_helper():\n    unused_var = 1\n    return unused_var\n",
        encoding="utf-8",
    )
    v_cfg = dataclasses.replace(
        config(path, ("vulture",)),
        vulture=dataclasses.replace(
            config(path).vulture, paths=("./src", "."), min_confidence=60
        ),
    )
    recorded_argv = []

    def real_runner(argv, cwd, data=None):
        recorded_argv.append(list(argv))
        return hygiene._run(argv, cwd, data)

    res = hygiene.run(changes(path, base), v_cfg, root=path, runner=real_runner)
    assert res["status"] == "passed"
    v_entry = by_name(res)["vulture"]
    assert v_entry["status"] == "advisory"
    assert v_entry["exit_code"] == 3
    assert ["vulture", "--min-confidence", "60", "--", "src", "."] in recorded_argv
    assert "_unused_helper" in v_entry["stdout"]


@pytest.mark.skipif(
    shutil.which("tach") is None,
    reason="tach binary unavailable on PATH",
)
def test_real_tach_cli_checks_default_tach_toml_and_external_independently(repo):
    path, base = repo
    (path / "tach.toml").write_text(
        'source_roots = ["src"]\n'
        "exact = true\n"
        "forbid_circular_dependencies = true\n"
        'root_module = "forbid"\n\n'
        '[[modules]]\npath = "pkg"\ndepends_on = []\n\n'
        '[[modules]]\npath = "pkg.sub"\ndepends_on = []\n\n'
        '[[modules]]\npath = "pkg.other"\ndepends_on = []\n',
        encoding="utf-8",
    )
    (path / "src/pkg/sub.py").write_text("import pkg.other\n", encoding="utf-8")
    t_cfg = dataclasses.replace(
        config(path, ("tach", "tach_external")),
        tach=dataclasses.replace(config(path).tach, external=True),
    )
    res = hygiene.run(changes(path, base), t_cfg, root=path, runner=hygiene._run)
    assert res["status"] == "failed"
    assert by_name(res)["tach"]["status"] == "failed"
    assert by_name(res)["tach"]["reason"] == "tach_check_failed"
    assert by_name(res)["tach_external"]["status"] == "passed"
