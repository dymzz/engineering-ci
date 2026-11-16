import json
import os
import shutil

import pytest

from engineering_ci import check_contract, trivy

BINARY = os.environ.get("TRIVY_BIN") or shutil.which("trivy")
requires_trivy = pytest.mark.skipif(
    not BINARY, reason="fixed trivy binary is not available"
)


@requires_trivy
def test_trivy_secret_scan_returns_sanitized_findings(tmp_path):
    root = tmp_path / "target"
    root.mkdir()
    (root / "a.txt").write_text(
        'token = "glpat-abcdefghijklmnopqrst"\n', encoding="utf-8"
    )
    result = trivy.scan_filesystem(
        root,
        target=".",
        scanners=("secret",),
        environ={**os.environ, "TRIVY_BIN": BINARY},
    )
    assert result["status"] == "findings"
    assert result["native_exit_code"] == 0
    assert result["scanners"] == ["secret"]
    assert result["target"]["path"] == "."
    assert result["target"]["input_sha256"].startswith("sha256:")
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["kind"] == "secret"
    assert finding["rule_sha256"].startswith("sha256:")
    assert finding["target_sha256"].startswith("sha256:")
    assert finding["start_line"] == 1
    serialized = json.dumps(result)
    assert "glpat-abcdefghijklmnopqrst" not in serialized
    assert "Content" not in serialized


@requires_trivy
def test_trivy_clean_scan_passes(tmp_path):
    root = tmp_path / "target"
    root.mkdir()
    (root / "a.txt").write_text("clean\n", encoding="utf-8")
    result = trivy.scan_filesystem(
        root,
        target=".",
        scanners=("secret",),
        environ={**os.environ, "TRIVY_BIN": BINARY},
    )
    assert result["status"] == "passed"
    assert result["native_exit_code"] == 0
    assert result["findings"] == []


def test_trivy_rejects_target_outside_root(tmp_path):
    root = tmp_path / "target"
    root.mkdir()
    with pytest.raises(trivy.TrivyError) as raised:
        trivy.scan_filesystem(root, target="../outside", scanners=("secret",))
    assert raised.value.code == "trivy_target_outside_root"


def test_trivy_rejects_unknown_scanner(tmp_path):
    root = tmp_path / "target"
    root.mkdir()
    with pytest.raises(trivy.TrivyError) as raised:
        trivy.scan_filesystem(root, target=".", scanners=("unknown",))
    assert raised.value.code == "trivy_scanners_invalid"


def test_trivy_missing_binary_is_not_clean(tmp_path):
    root = tmp_path / "target"
    root.mkdir()
    with pytest.raises(trivy.TrivyError) as raised:
        trivy.scan_filesystem(
            root,
            target=".",
            scanners=("secret",),
            environ={"TRIVY_BIN": str(tmp_path / "missing")},
        )
    assert raised.value.code == "trivy_binary_missing"


def test_trivy_check_definition_is_valid_process():
    definition = check_contract.definition_for("trivy")
    assert definition.check_id == "eci/security/trivy-scan"
    assert definition.definition_version == 1
    assert check_contract._execution_kind(definition.check_id) == "process"
    check_contract.validate_definition(definition)
    resolved = check_contract.resolve_definitions(["trivy"])
    assert resolved["status"] == "complete"
    assert resolved["definitions"][0]["check_id"] == definition.check_id
    tool = definition.tool_requirements[0].alternatives[0]
    assert tool.access_id == "trivy/scan"
    assert tool.tool_id == "trivy"
    assert tool.version == "0.76.1"
