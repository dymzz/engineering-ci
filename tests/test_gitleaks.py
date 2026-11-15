import json
import os
import shutil
import subprocess

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
    assert result["coverage"]["revision_range"] == f"{base}..HEAD"
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["RuleID"] == "gitlab-pat"
    assert finding["File"] == "a.txt"
    assert finding["Commit"] == git(history_repo, "rev-parse", "HEAD")
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


def test_gitleaks_rejects_non_range_scope(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    with pytest.raises(gitleaks.GitleaksError) as raised:
        gitleaks.scan_git_history(root, revision_range="HEAD")
    assert raised.value.code == "gitleaks_revision_invalid"


def test_sanitizer_removes_secret_from_allowed_fields_and_diagnostics():
    findings, secrets = gitleaks._sanitize(
        [
            {
                "RuleID": "test-rule",
                "File": "glpat-abcdefghijklmnopqrst/path.txt",
                "Description": "secret glpat-abcdefghijklmnopqrst",
                "Secret": "glpat-abcdefghijklmnopqrst",
                "Match": "glpat-abcdefghijklmnopqrst",
                "StartLine": 1,
            }
        ]
    )
    assert findings == [
        {
            "RuleID": "test-rule",
            "File": "[REDACTED]/path.txt",
            "StartLine": 1,
            "redacted": True,
        }
    ]
    assert secrets == {"glpat-abcdefghijklmnopqrst"}
    assert "glpat-" not in json.dumps(findings)


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
    assert tool.access_id == "gitleaks.git"
    assert tool.tool_id == "gitleaks"
    assert tool.tool_id_source == "eci_pinned_asset"
    assert tool.version == "8.30.1"
