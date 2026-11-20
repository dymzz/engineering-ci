import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from engineering_ci import check_contract, gitleaks

BINARY = os.environ.get("GITLEAKS_BIN") or shutil.which("gitleaks")
requires_gitleaks = pytest.mark.skipif(
    not BINARY, reason="fixed gitleaks binary is not available"
)


def git(root, *args):
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


@pytest.fixture()
def history_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    (root / "a.txt").write_text("hello\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    (root / "a.txt").write_text(
        'token = "glpat-abcdefghijklmnopqrst"\n', encoding="utf-8"
    )
    git(root, "add", "-A")
    git(root, "commit", "-qm", "secret")
    return root


@requires_gitleaks
def test_gitleaks_history_finds_and_redacts_secret(history_repo):
    base = git(history_repo, "rev-parse", "HEAD~1")
    result = gitleaks.scan_git_history(
        history_repo,
        revision_range=f"{base}..HEAD",
        environ={**os.environ, "GITLEAKS_BIN": BINARY},
    )
    assert result["status"] == "failed"
    assert result["native_exit_code"] == 1
    assert result["coverage"]["mode"] == "revision-range"
    assert result["coverage"]["scope"] == "revision_range"
    assert result["coverage"]["old_commit"] == base
    assert result["coverage"]["new_commit"] == git(history_repo, "rev-parse", "HEAD")
    assert result["coverage"]["diff_mode"] == "first-parent-merges"
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["rule_sha256"] == (
        "sha256:" + hashlib.sha256(b"gitlab-pat").hexdigest()
    )
    assert finding["Commit"] == git(history_repo, "rev-parse", "HEAD")
    assert "File" not in finding
    assert finding["redacted"] is True
    serialized = json.dumps(result)
    assert "glpat-abcdefghijklmnopqrst" not in serialized
    assert "Match" not in serialized
    assert "Secret" not in serialized


@requires_gitleaks
def test_gitleaks_clean_range_passes(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    (root / "a.txt").write_text("clean\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    (root / "a.txt").write_text("cleaner\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "next")
    result = gitleaks.scan_git_history(
        root,
        revision_range="HEAD~1..HEAD",
        environ={**os.environ, "GITLEAKS_BIN": BINARY},
    )
    assert result["status"] == "passed"
    assert result["native_exit_code"] == 0
    assert result["findings"] == []
    assert result["coverage"]["complete"] is True
    assert result["coverage"]["old_commit"] == git(root, "rev-parse", "HEAD~1")
    assert result["coverage"]["new_commit"] == git(root, "rev-parse", "HEAD")
    assert result["coverage"]["scope"] == "revision_range"
    assert result["coverage"]["diff_mode"] == "first-parent-merges"


def test_gitleaks_rejects_non_range_scope(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    with pytest.raises(gitleaks.GitleaksError) as raised:
        gitleaks.scan_git_history(root, revision_range="HEAD")
    assert raised.value.code == "gitleaks_revision_invalid"


def test_sanitizer_removes_secret_from_allowed_fields_and_diagnostics():
    findings = gitleaks._sanitize(
        [
            {
                "RuleID": "test-rule",
                "File": "glpat-abcdefghijklmnopqrst/path.txt",
                "Description": "secret glpat-abcdefghijklmnopqrst",
                "Secret": "[REDACTED]",
                "Match": "[REDACTED]",
                "Commit": "a" * 40,
                "StartLine": 1,
            }
        ]
    )
    assert findings == [
        {
            "rule_sha256": "sha256:" + hashlib.sha256(b"test-rule").hexdigest(),
            "Commit": "a" * 40,
            "StartLine": 1,
            "redacted": True,
        }
    ]
    assert "glpat-" not in json.dumps(findings)
    assert "File" not in findings[0]


def test_rule_id_cannot_expose_secret():
    findings = gitleaks._sanitize(
        [
            {
                "RuleID": "glpat-abcdefghijklmnopqrst",
                "Commit": "a" * 40,
                "StartLine": 1,
                "Secret": "[REDACTED]",
                "Match": "[REDACTED]",
            }
        ]
    )
    serialized = json.dumps(findings)
    assert "glpat-abcdefghijklmnopqrst" not in serialized
    assert findings[0]["rule_sha256"] == (
        "sha256:" + hashlib.sha256(b"glpat-abcdefghijklmnopqrst").hexdigest()
    )


def test_sanitizer_refuses_non_list_report():
    with pytest.raises(gitleaks.GitleaksError) as raised:
        gitleaks._sanitize({"Secret": "x"})
    assert raised.value.code == "gitleaks_report_invalid"


def test_gitleaks_rejects_wrong_binary_version(tmp_path):
    if os.name != "nt":
        pytest.skip("cmd shim fixture is Windows-specific")
    fake = tmp_path / "fake-gitleaks.cmd"
    fake.write_text("@echo 9.9.9\n", encoding="utf-8")
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    with pytest.raises(gitleaks.GitleaksError) as raised:
        gitleaks.scan_git_history(
            root,
            all_history=True,
            environ={"GITLEAKS_BIN": str(fake)},
        )
    assert raised.value.code == "gitleaks_binary_version_mismatch"


@requires_gitleaks
def test_gitleaks_non_ancestor_range_is_insufficient(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    (root / "a.txt").write_text("base\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    git(root, "checkout", "-qb", "other")
    (root / "b.txt").write_text("other\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "other")
    other = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "-")
    (root / "c.txt").write_text("main\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "main")
    result = gitleaks.scan_git_history(
        root,
        revision_range=f"{other}..HEAD",
        environ={**os.environ, "GITLEAKS_BIN": BINARY},
    )
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["reason"] == "non_ancestor_range"
    assert result["status"] == "insufficient"
    assert result["native_status"] in {"passed", "failed"}


@requires_gitleaks
def test_gitleaks_custom_rule_id_secret_is_hashed_in_public_result(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    (root / "a.txt").write_text("placeholder\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    (root / "a.txt").write_text("SECRET123\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "secret")
    config = tmp_path / "gitleaks.toml"
    config.write_text(
        """
title = "test"
[[rules]]
id = "glpat-abcdefghijklmnopqrst"
description = "custom"
regex = '''SECRET123'''
""",
        encoding="utf-8",
    )
    base = git(root, "rev-parse", "HEAD~1")
    result = gitleaks.scan_git_history(
        root,
        revision_range=f"{base}..HEAD",
        config_path=config,
        environ={**os.environ, "GITLEAKS_BIN": BINARY},
    )
    serialized = json.dumps(result)
    assert result["status"] == "failed"
    assert "glpat-abcdefghijklmnopqrst" not in serialized
    assert result["findings"][0]["rule_sha256"] == (
        "sha256:" + hashlib.sha256(b"glpat-abcdefghijklmnopqrst").hexdigest()
    )


def test_gitleaks_requires_explicit_scope(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    with pytest.raises(gitleaks.GitleaksError) as raised:
        gitleaks.scan_git_history(root)
    assert raised.value.code == "gitleaks_scope_required"
    with pytest.raises(gitleaks.GitleaksError) as raised:
        gitleaks.scan_git_history(root, revision_range="HEAD", all_history=True)
    assert raised.value.code == "gitleaks_scope_required"


def test_gitleaks_missing_binary_is_not_clean(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    with pytest.raises(gitleaks.GitleaksError) as raised:
        gitleaks.scan_git_history(
            root,
            all_history=True,
            environ={"GITLEAKS_BIN": str(tmp_path / "missing")},
        )
    assert raised.value.code == "gitleaks_binary_missing"


def test_gitleaks_check_definition_is_process_with_controlled_tool():
    definition = check_contract.definition_for("gitleaks")
    assert definition.check_id == "eci/security/gitleaks-history"
    assert check_contract._execution_kind(definition.check_id) == "process"
    assert definition.definition_version == 1
    requirement = definition.tool_requirements[0]
    assert requirement.requirement_id == "history-scan"
    tool = requirement.alternatives[0]
    assert tool.access_id == "gitleaks/git"
    assert tool.tool_id == "gitleaks"
    assert tool.tool_id_source == "eci_pinned_asset"
    assert tool.version == "8.30.1"
    check_contract.validate_definition(definition)
    resolved = check_contract.resolve_definitions(["gitleaks"])
    assert resolved["status"] == "complete"
    assert resolved["definitions"][0]["check_id"] == definition.check_id
    assert resolved["definitions"][0]["definition_version"] == 1


def test_native_err_diagnostics_are_not_clean():
    assert gitleaks._native_outcome(
        0, [], 'time ERR error="stderr is not empty"\n'
    ) == ("error", "gitleaks_native_error")
    assert gitleaks._native_outcome(0, [], "time INF no leaks found\n") == (
        "passed",
        None,
    )
    assert gitleaks._native_outcome(
        0, [{"Commit": "a" * 40}], "time INF no leaks found\n"
    ) == ("error", "gitleaks_exit_inconsistent")


def test_all_history_records_local_ref_scope_without_raw_revision(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    (root / "a.txt").write_text("clean\n", encoding="utf-8")
    git(root, "add", "a.txt")
    git(root, "commit", "-qm", "base")
    coverage = gitleaks._resolve_scope(root, None, True)
    assert coverage["scope"] == "local_refs"
    assert coverage["scope_authority"] == "local_git_refs"
    assert coverage["scope_limit"] == "local_refs_and_odb"
    assert coverage["ref_count"] >= 1
    assert coverage["ref_set_sha256"].startswith("sha256:")
    assert coverage["complete"] is True
    assert coverage["head_commit"] == git(root, "rev-parse", "HEAD")
    assert "revision_range" not in coverage


@requires_gitleaks
def test_gitleaks_git_stderr_failure_is_not_clean(history_repo):
    base = git(history_repo, "rev-parse", "HEAD~1")
    result = gitleaks.scan_git_history(
        history_repo,
        revision_range=f"{base}..HEAD",
        environ={
            **os.environ,
            "GITLEAKS_BIN": BINARY,
            "GIT_TRACE": "1",
        },
    )
    assert result["native_status"] == "error"
    assert result["status"] == "error"
    assert result["coverage"]["complete"] is False


@requires_gitleaks
def test_gitleaks_merge_resolution_secret_is_covered(tmp_path):
    root = tmp_path / "merge"
    root.mkdir()
    git(root, "init", "-q", "-b", "master")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    (root / "a.txt").write_text("base\n", encoding="utf-8")
    git(root, "add", "a.txt")
    git(root, "commit", "-qm", "base")
    git(root, "checkout", "-qb", "feature")
    (root / "a.txt").write_text("feature\n", encoding="utf-8")
    git(root, "commit", "-qam", "feature")
    git(root, "checkout", "-q", "master")
    (root / "a.txt").write_text("main\n", encoding="utf-8")
    git(root, "commit", "-qam", "main")
    subprocess.run(
        ["git", "merge", "feature"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    (root / "a.txt").write_text("MERGE_SECRET\n", encoding="utf-8")
    git(root, "add", "a.txt")
    git(root, "commit", "-qm", "merge resolution")
    merge_commit = git(root, "rev-parse", "HEAD")
    config = tmp_path / "gitleaks.toml"
    config.write_text(
        """
title = "merge-test"
[[rules]]
id = "merge-secret"
description = "merge secret"
regex = '''MERGE_SECRET'''
""",
        encoding="utf-8",
    )
    result = gitleaks.scan_git_history(
        root,
        all_history=True,
        config_path=config,
        environ={**os.environ, "GITLEAKS_BIN": BINARY},
    )
    assert result["status"] == "failed"
    assert result["coverage"]["diff_mode"] == "first-parent-merges"
    assert any(finding["Commit"] == merge_commit for finding in result["findings"])


@requires_gitleaks
def test_gitleaks_public_result_omits_raw_scope_and_binary_path(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    (root / "a.txt").write_text("clean\n", encoding="utf-8")
    git(root, "add", "a.txt")
    git(root, "commit", "-qm", "base")
    token = "glpat-abcdefghijklmnopqrst"
    git(root, "branch", token)
    (root / "a.txt").write_text("next\n", encoding="utf-8")
    git(root, "add", "a.txt")
    git(root, "commit", "-qm", "next")
    binary_dir = tmp_path / token
    binary_dir.mkdir()
    binary = binary_dir / Path(BINARY).name
    shutil.copy2(BINARY, binary)
    result = gitleaks.scan_git_history(
        root,
        revision_range=f"refs/heads/{token}..HEAD",
        environ={**os.environ, "GITLEAKS_BIN": str(binary)},
    )
    serialized = json.dumps(result)
    assert token not in serialized
    assert str(binary) not in serialized
    assert "path" not in result["tool"]
    assert "revision_range" not in result["coverage"]
    assert result["coverage"]["old_commit"]
    assert result["coverage"]["new_commit"]


def test_gitleaks_rejects_conflicting_git_environment(tmp_path):
    root = tmp_path / "a"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "test")
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    git(root, "add", "a.txt")
    git(root, "commit", "-qm", "base")
    other = tmp_path / "b"
    other.mkdir()
    git(other, "init", "-q")
    git(other, "config", "user.email", "test@example.com")
    git(other, "config", "user.name", "test")
    (other / "a.txt").write_text("b\n", encoding="utf-8")
    git(other, "add", "a.txt")
    git(other, "commit", "-qm", "base")
    with pytest.raises(
        gitleaks.GitleaksError, match="gitleaks_git_environment_conflict"
    ):
        gitleaks.scan_git_history(
            root,
            all_history=True,
            environ={**os.environ, "GIT_DIR": str(other / ".git")},
        )


@requires_gitleaks
def test_gitleaks_relative_root_and_binary_are_resolved_once(
    history_repo, tmp_path, monkeypatch
):
    binary = tmp_path / "bin" / Path(BINARY).name
    binary.parent.mkdir()
    shutil.copy2(BINARY, binary)
    monkeypatch.chdir(tmp_path)
    base = git(history_repo, "rev-parse", "HEAD~1")
    result = gitleaks.scan_git_history(
        "repo",
        revision_range=f"{base}..HEAD",
        environ={"GITLEAKS_BIN": str(binary.relative_to(tmp_path))},
    )
    assert result["status"] == "failed"
    assert result["coverage"]["complete"] is True
