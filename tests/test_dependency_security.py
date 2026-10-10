"""Deterministic security tests need neither network nor an installed scanner."""

import datetime as dt
import json
from pathlib import Path
import subprocess
import tomllib
from unittest.mock import patch

import pytest

from engineering_ci import dependency_security as security

NOW = dt.datetime(2026, 10, 10, tzinfo=dt.timezone.utc)
LOCK = """version = 1
[[package]]
name = "sample"
version = "0.1.0"
source = { virtual = "." }
[[package]]
name = "idna"
version = "3.11"
source = { registry = "https://pypi.org/simple" }
"""


def native(*ids, package="idna", version="3.11"):
    vulnerabilities = [
        {
            "id": identifier,
            "affected": [
                {
                    "package": {"name": package, "ecosystem": "PyPI"},
                    "ranges": [
                        {
                            "type": "ECOSYSTEM",
                            "events": [{"introduced": "0"}, {"fixed": "3.12"}],
                        }
                    ],
                }
            ],
        }
        for identifier in ids
    ]
    return {
        "results": [
            {
                "source": {"path": "/private/temp/uv.lock", "type": "lockfile"},
                "packages": [
                    {
                        "package": {
                            "name": package,
                            "version": version,
                            "ecosystem": "PyPI",
                        },
                        "vulnerabilities": vulnerabilities,
                    }
                ],
            }
        ]
    }


class FakeRunner:
    def __init__(self, result=None, code=0, integrity_code=0, integrity_message=""):
        self.result = native() if result is None else result
        self.code = code
        self.integrity_code = integrity_code
        self.integrity_message = integrity_message
        self.calls = []
        self.config = None
        self.lock = None

    def __call__(self, args, cwd, timeout):
        self.calls.append(args)
        if "--check" in args:
            return subprocess.CompletedProcess(
                args, self.integrity_code, "", self.integrity_message
            )
        if "--version" in args:
            return subprocess.CompletedProcess(
                args, 0, "osv-scanner version: 2.6.0\n", ""
            )
        self.config = Path(
            next(x.split("=", 1)[1] for x in args if x.startswith("--config="))
        ).read_text(encoding="utf-8")
        self.lock = Path(
            next(x.split("=", 1)[1] for x in args if x.startswith("--lockfile="))
        ).read_text()
        self.lock_bytes = Path(
            next(x.split("=", 1)[1] for x in args if x.startswith("--lockfile="))
        ).read_bytes()
        if self.code == "timeout":
            raise subprocess.TimeoutExpired(args, timeout)
        output = Path(
            next(x.split("=", 1)[1] for x in args if x.startswith("--output-file="))
        )
        output.write_text(
            self.result if isinstance(self.result, str) else json.dumps(self.result)
        )
        return subprocess.CompletedProcess(
            args, self.code, "", "token=DO_NOT_PERSIST secret-index-password"
        )


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="sample"\nversion="0.1.0"\n'
    )
    (tmp_path / "uv.lock").write_text(LOCK)
    return tmp_path


def run(project, fake=None, **kwargs):
    fake = fake or FakeRunner()
    with patch.object(security, "install_scanner", return_value=("test-scanner", True)):
        report, raw = security.scan(
            project, runner=fake, now=NOW, mode=kwargs.pop("mode", "enforce"), **kwargs
        )
    return report, raw, fake


def test_off_does_not_read_project_install_tool_or_query(tmp_path):
    report, raw, fake = run(tmp_path / "absent", mode="off")
    assert report["status"] == "not_applicable" and report["gate"]["exit_code"] == 0
    assert fake.calls == [] and raw is None


def test_real_contract_clean_scan_and_integrity_are_separate(project):
    report, raw, fake = run(project)
    assert report["integrity"]["status"] == "passed"
    assert report["status"] == "passed" and report["scanner"]["status"] == "complete"
    assert report["dependencies_scanned"] == 1 and report["gate"]["exit_code"] == 0
    assert len(fake.calls) == 3
    assert "--all-packages" in fake.calls[-1]
    assert raw["results"][0]["source"]["path"] == "uv.lock"


@pytest.mark.parametrize("mode,exit_code", [("report", 0), ("enforce", 1)])
def test_vulnerability_is_not_lock_failure_and_modes_are_explicit(
    project, mode, exit_code
):
    report, _, _ = run(
        project, FakeRunner(native("GHSA-aaaa-bbbb-cccc"), code=1), mode=mode
    )
    assert report["integrity"]["status"] == "passed" and report["status"] == "failed"
    assert (
        report["scanner"]["exit_code"] == 1 and report["gate"]["exit_code"] == exit_code
    )
    assert report["vulnerabilities"][0]["fixed_versions"] == ["3.12"]


def test_multiple_packages_and_truncation_keep_full_native_evidence(project):
    with (project / "uv.lock").open("a") as stream:
        stream.write(
            '[[package]]\nname="other"\nversion="1.0"\nsource={registry="https://pypi.org/simple"}\n'
        )
    result = native("GHSA-aaaa-bbbb-cccc", "CVE-2026-12345")
    result["results"][0]["packages"] += native(
        "PYSEC-2026-12", package="other", version="1.0"
    )["results"][0]["packages"]
    report, raw, _ = run(project, FakeRunner(result, code=1), max_findings=1)
    assert report["dependencies_scanned"] == 2 and report["vulnerability_records"] == 3
    assert len(report["vulnerabilities"]) == 1 and report["omitted_findings"] == 2
    assert len(raw["results"][0]["packages"][0]["vulnerabilities"]) == 2


@pytest.mark.parametrize(
    "code,category",
    [
        (129, "vulnerability_database_unavailable"),
        (127, "scanner_execution_error"),
        (128, "scanner_no_packages"),
        (130, "scanner_config_error"),
        ("timeout", "execution_timeout"),
    ],
)
def test_scan_failures_never_mean_no_vulnerabilities(project, code, category):
    report, _, _ = run(project, FakeRunner(code=code))
    assert report["status"] == "error" and report["integrity"]["status"] == "passed"
    assert report["gate"]["exit_code"] == 1 and category in report["diagnostics"]
    assert "DO_NOT_PERSIST" not in json.dumps(report)


def test_report_mode_records_scanner_errors_without_blocking(project):
    report, _, _ = run(project, FakeRunner(code=129), mode="report")
    assert report["status"] == "error" and report["gate"]["passed"] is False
    assert report["gate"]["blocking"] is False and report["gate"]["exit_code"] == 0


@pytest.mark.parametrize(
    "result", ["not-json", '{"results":[],"results":[]}', {}, {"results": None}]
)
def test_bad_json_is_error(project, result):
    report, _, _ = run(project, FakeRunner(result))
    assert report["status"] == "error" and report["gate"]["exit_code"] == 1
    assert "scanner_response_invalid" in report["diagnostics"]


def test_lock_missing_and_format_errors_are_distinct(project):
    (project / "uv.lock").unlink()
    report, _, fake = run(project)
    assert report["diagnostics"] == ["lock_missing"] and fake.calls == []
    (project / "uv.lock").write_text("not TOML")
    report, _, fake = run(project)
    assert report["diagnostics"] == ["lock_format_invalid"] and fake.calls == []


def test_outdated_lock_prevents_scan(project):
    report, _, fake = run(
        project,
        FakeRunner(
            integrity_code=1, integrity_message="The lockfile needs to be updated"
        ),
    )
    assert report["integrity"]["status"] == "failed" and report["diagnostics"] == [
        "lock_outdated"
    ]
    assert len(fake.calls) == 1


def test_integrity_network_error_is_not_stale_lock_or_security_finding(project):
    report, _, _ = run(
        project, FakeRunner(integrity_code=2, integrity_message="connection failed")
    )
    assert report["integrity"]["status"] == "error" and report["diagnostics"] == [
        "integrity_check_error"
    ]


@pytest.mark.parametrize("mode", ["unknown", "", None, {}, ["enforce"], "token=SECRET"])
def test_invalid_mode_fails_without_network(project, mode):
    report, _, fake = run(project, mode=mode)
    assert report["status"] == "error" and report["gate"]["exit_code"] == 1
    assert report["gate"]["mode"] == "invalid" and "SECRET" not in json.dumps(report)
    assert fake.calls == []


def exception_file(
    project, expiry="2027-01-01", reason="Temporary compatibility exception"
):
    path = project / "exceptions.toml"
    path.write_text(
        f'[[IgnoredVulns]]\nid="GHSA-aaaa-bbbb-cccc"\nreason="{reason}"\nignoreUntil={expiry}\n',
        encoding="utf-8",
    )
    return path


def test_finite_exception_is_forwarded_to_native_scanner(project):
    report, _, fake = run(project, exceptions=exception_file(project))
    assert report["exceptions"][0]["active"] is True and report["status"] == "passed"
    assert (
        "GHSA-aaaa-bbbb-cccc" in fake.config
        and "2027-01-01T00:00:00+00:00" in fake.config
    )


def test_native_exception_supports_unicode_reason(project):
    reason = "临时兼容措施 🔒"
    report, _, fake = run(project, exceptions=exception_file(project, reason=reason))
    assert report["status"] == "passed"
    assert tomllib.loads(fake.config)["IgnoredVulns"][0]["reason"] == reason


def test_expired_exception_is_not_forwarded_and_vulnerability_blocks(project):
    report, _, fake = run(
        project,
        FakeRunner(native("GHSA-aaaa-bbbb-cccc"), code=1),
        exceptions=exception_file(project, "2026-01-01"),
    )
    assert (
        report["exceptions"][0]["active"] is False and report["gate"]["exit_code"] == 1
    )
    assert "GHSA-aaaa-bbbb-cccc" not in fake.config


@pytest.mark.parametrize(
    "content",
    [
        "[[PackageOverrides]]\nignore=true\n",
        '[[IgnoredVulns]]\nid="*"\nreason="all"\nignoreUntil=2027-01-01\n',
        '[[IgnoredVulns]]\nid="CVE-2026-12345"\nreason="temporary"\n',
    ],
)
def test_unbounded_or_broad_exceptions_are_rejected(project, content):
    path = project / "exceptions.toml"
    path.write_text(content)
    report, _, fake = run(project, exceptions=path)
    assert report["status"] == "error" and report["diagnostics"] == [
        "exceptions_invalid"
    ]
    assert len(fake.calls) == 1


def test_private_dependency_blocks_queries_and_records_coverage_gap(project):
    with (project / "uv.lock").open("a") as stream:
        stream.write(
            '[[package]]\nname="private"\nversion="1.0"\nsource={registry="https://token:secret@private.example/simple"}\n'
        )
    report, _, fake = run(project)
    assert report["status"] == "error" and report["diagnostics"] == [
        "scan_coverage_gap"
    ]
    assert report["unsupported_dependencies"][0]["package"] == "private"
    assert len(fake.calls) == 1 and "secret" not in json.dumps(report)


def test_partial_native_results_are_not_complete(project):
    report, _, _ = run(project, FakeRunner({"results": []}))
    assert report["status"] == "error" and report["diagnostics"] == [
        "scan_coverage_gap"
    ]


@pytest.mark.parametrize("result,code", [(native("CVE-2026-12345"), 0), (native(), 1)])
def test_exit_code_and_data_disagreement_is_error(project, result, code):
    report, _, _ = run(project, FakeRunner(result, code=code))
    assert report["diagnostics"] == ["scanner_exit_result_disagreement"]


def test_own_editable_node_excluded_without_changing_original_lock(project):
    before = LOCK.replace('virtual = "."', 'editable = "."')
    (project / "uv.lock").write_text(before)
    report, _, fake = run(project)
    assert report["status"] == "passed"
    assert 'source = { virtual = "." }' in fake.lock
    assert (project / "uv.lock").read_text() == before


def test_bundle_keeps_full_result_and_sanitized_diagnostics(project, tmp_path):
    report, raw, _ = run(project, FakeRunner(native("CVE-2026-12345"), code=1))
    output = tmp_path / "bundle"
    security.write_bundle(output, report, raw)
    assert set(p.name for p in output.iterdir()) == {
        "report.json",
        "summary.md",
        "scanner.json",
        "diagnostics.json",
    }
    assert "CVE-2026-12345" in (output / "summary.md").read_text()
    assert "DO_NOT_PERSIST" not in "".join(p.read_text() for p in output.iterdir())


def test_wrong_scanner_digest_prevents_execution(project):
    path = project / "scanner"
    path.write_bytes(b"untrusted")
    report, _, fake = run(project, scanner=path)
    assert report["diagnostics"] == ["scanner_integrity_error"]
    assert len(fake.calls) == 1


def test_public_mirror_requires_explicit_disclosure_policy(project):
    mirror = "https://mirrors.aliyun.com/pypi/simple"
    (project / "uv.lock").write_text(LOCK.replace("https://pypi.org/simple", mirror))
    report, _, fake = run(project)
    assert report["diagnostics"] == ["scan_coverage_gap"] and len(fake.calls) == 1
    report, _, _ = run(project, public_indexes=[mirror])
    assert report["status"] == "passed" and mirror in report["public_registry_policy"]


@pytest.mark.parametrize(
    "index",
    [
        "*",
        "http://index.example/simple",
        "https://token:secret@index.example/simple",
        "https://index.example/simple?token=secret",
    ],
)
def test_public_index_policy_rejects_credentials_wildcards_and_insecure_urls(
    project, index
):
    report, _, fake = run(project, public_indexes=[index])
    assert report["diagnostics"] == ["public_index_config_invalid"] and fake.calls == []
    assert "secret" not in json.dumps(report)


def test_scanner_copy_preserves_windows_crlf(project):
    original = LOCK.replace("\n", "\r\n").encode("utf-8")
    (project / "uv.lock").write_bytes(original)
    report, _, fake = run(project)
    assert report["status"] == "passed"
    assert fake.lock_bytes == original
    assert (project / "uv.lock").read_bytes() == original


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonstandard_json_numbers_are_rejected(project, constant):
    data = json.dumps(native()).replace(
        '"results":', '"untrusted": ' + constant + ', "results":'
    )
    report, _, _ = run(project, FakeRunner(data))
    assert report["diagnostics"] == ["scanner_response_invalid"]


@pytest.mark.parametrize("aliases", ["not-a-list", [123]])
def test_invalid_scanner_aliases_are_rejected(project, aliases):
    data = native("CVE-2026-12345")
    data["results"][0]["packages"][0]["vulnerabilities"][0]["aliases"] = aliases
    report, _, _ = run(project, FakeRunner(data, code=1))
    assert report["diagnostics"] == ["scanner_response_invalid"]


def test_project_outside_workflow_workspace_is_not_read(project):
    report, _, fake = run(project, workspace_root=project / "child")
    assert report["diagnostics"] == ["project_outside_workspace"] and fake.calls == []


def test_exception_outside_workflow_workspace_is_not_read(project):
    report, _, fake = run(
        project, workspace_root=project, exceptions=project.parent / "exceptions.toml"
    )
    assert (
        report["diagnostics"] == ["exceptions_outside_workspace"] and fake.calls == []
    )
