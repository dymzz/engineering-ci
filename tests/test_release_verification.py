"""Release CLI semantics use fake GitHub facts; no real Workflow or publication."""

import base64
import hashlib
import io
import json
import subprocess
import zipfile

import pytest
from test_manual_release import FakeGitHub
from test_release_manifest import HEAD, REPOSITORY, TAG, make_manifest

from engineering_ci.cli import release as cli
from engineering_ci.github_cli import ApiError, GitHub
from engineering_ci.release import publishing, verification


class API(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.run.update(status="completed", conclusion="success")
        self.versions = {
            name: "0.4.11"
            for name in (
                "pyproject.toml",
                "uv.lock",
                ".dagger/pyproject.toml",
                ".dagger/uv.lock",
            )
        }
        self.value = make_manifest(self, mode="validation")
        self.refresh()

    def refresh(self, name="release-manifest.json"):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(name, json.dumps(self.value))
        self.archive = buffer.getvalue()
        self.item = {
            "id": 99,
            "name": "release-manifest",
            "expired": False,
            "size_in_bytes": len(self.archive),
            "digest": "sha256:" + hashlib.sha256(self.archive).hexdigest(),
            "workflow_run": {"id": 123, "head_sha": HEAD},
            "url": f"https://api.github.com/repos/{REPOSITORY}/actions/artifacts/99",
        }
        self.manifests = [self.item]

    def __call__(self, path, method="GET", data=None):
        if path.startswith("contents/"):
            name = path[len("contents/") :].split("?", 1)[0]
            version = self.versions[name]
            text = (
                f'[project]\nversion="{version}"\n'
                if name.endswith("pyproject.toml")
                else f'[[package]]\nname="engineering-ci"\nversion="{version}"\n'
            )
            result = {
                "path": name,
                "encoding": "base64",
                "content": base64.b64encode(text.encode()).decode(),
            }
        elif path in ("actions/runs/123", "actions/runs/123/attempts/2"):
            result = self.run
        elif path.startswith("actions/runs/123/artifacts?"):
            result = {"artifacts": self.manifests}
        elif path == "actions/artifacts/99":
            result = self.item
        else:
            return super().__call__(path, method, data)
        self.calls.append((path, method, data))
        assert method == "GET"
        return result

    def artifact_archive(self, asset_id):
        self.calls.append((f"actions/artifacts/{asset_id}/zip", "GET", None))
        assert asset_id == 99
        return self.archive


def identity():
    return {"version": "0.4.11", "tag": TAG, "commit_sha": HEAD, "ref": "main"}


def verify_args():
    return [
        "verify",
        "--version",
        "0.4.11",
        "--run",
        "123",
        "--repo",
        REPOSITORY,
        "--json",
    ]


def test_requested_version_is_checked_against_all_four_remote_fields():
    api = API()
    assert verification.target(api, "main", "0.4.11") == identity()
    assert not api.writes
    api.versions[".dagger/uv.lock"] = "0.4.9"
    with pytest.raises(ValueError, match="four metadata"):
        verification.target(api, "main", "0.4.11")


def test_verify_checks_explicit_version_and_run_without_dispatching(
    monkeypatch, capsys
):
    api = API()
    monkeypatch.setattr(cli, "GitHub", lambda repo: api)
    monkeypatch.setattr(
        cli, "entrypoint", lambda args: pytest.fail("verify cannot create a Run")
    )
    assert cli.main(verify_args()) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["release"] == identity()
    assert result["validation"]["run_id"] == 123
    assert result["validation"]["status"] == "passed"
    assert result["publication"]["status"] == "not_published"
    assert "DO_NOT_RECORD" not in json.dumps(result)
    assert not api.writes


@pytest.mark.parametrize(
    "state,expected",
    [
        ("queued", "running"),
        ("in_progress", "running"),
        ("failure", "failed"),
        ("cancelled", "failed"),
    ],
)
def test_verify_reports_the_actual_existing_run_state(
    state, expected, monkeypatch, capsys
):
    api = API()
    if state in {"queued", "in_progress"}:
        api.run.update(status=state, conclusion=None)
    else:
        api.run["conclusion"] = state
    monkeypatch.setattr(cli, "GitHub", lambda repo: api)
    monkeypatch.setattr(
        cli,
        "entrypoint",
        lambda args: pytest.fail("Read-only inspection cannot Dispatch"),
    )
    assert cli.main(verify_args()) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["validation"]["status"] == expected and not api.writes
    if state in {"failure", "cancelled"}:
        assert result["validation"]["conclusion"] == state


@pytest.mark.parametrize(
    "field,value",
    [("path", ".github/workflows/ci.yml"), ("event", "pull_request"), ("id", 456)],
)
def test_lightweight_pr_or_foreign_run_cannot_be_release_evidence(
    field, value, monkeypatch, capsys
):
    api = API()
    api.run[field] = value
    monkeypatch.setattr(cli, "GitHub", lambda repo: api)
    assert cli.main(verify_args()) == 1
    assert json.loads(capsys.readouterr().out)["result"] == "unavailable"
    assert not api.writes


def test_invalid_remote_version_never_echoes_sensitive_content(monkeypatch, capsys):
    api = API()
    api.versions["uv.lock"] = "DO_NOT_RECORD"
    monkeypatch.setattr(cli, "GitHub", lambda repo: api)
    assert cli.main(verify_args()) == 1
    output = capsys.readouterr().out
    assert "DO_NOT_RECORD" not in output
    assert json.loads(output)["result"] == "unavailable"


def test_read_only_interruption_returns_130(monkeypatch):
    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "GitHub", interrupt)
    assert cli.main(verify_args()) == 130


@pytest.mark.parametrize("mode,publish", [("validate", "false"), ("publish", "true")])
def test_mutating_commands_request_one_existing_workflow_without_prior_validation(
    mode, publish, monkeypatch
):
    api, calls = API(), []
    api.run["conclusion"] = (
        "failure"  # no earlier passed Run is required to request one full release
    )
    monkeypatch.setattr(cli, "GitHub", lambda repo: api)
    monkeypatch.setattr(cli, "prepare_publication", lambda options: 0)
    monkeypatch.setattr(cli, "execution_checkpoint", lambda *args: None)
    monkeypatch.setattr(cli, "entrypoint", lambda args: calls.append(args) or 1)
    assert cli.main([mode, "--version", "0.4.11", "--repo", REPOSITORY]) == 1
    from engineering_ci.execution.cli import parser

    options = parser().parse_args(calls[0])
    assert options.input == [
        "version=" + TAG,
        "publish=" + publish,
        "pytest_scenario=pass",
    ]
    assert not options.new_run and len(calls) == 1


def test_publish_and_post_verify_are_bound_to_the_same_run(
    monkeypatch, capsys, tmp_path
):
    api, calls = API(), []
    monkeypatch.setattr(cli, "GitHub", lambda repo: api)
    monkeypatch.setattr(cli, "prepare_publication", lambda options: 0)

    def invoke(args):
        calls.append(args)
        value = make_manifest(api, mode="publication")
        publishing.publish_release(
            api, TAG, HEAD, value, tmp_path / "release-manifest.json"
        )
        return 0

    monkeypatch.setattr(cli, "entrypoint", invoke)
    args = ["publish", "--version", "0.4.11", "--repo", REPOSITORY]
    assert cli.main(args) == 0
    assert api.saved["workflow"]["run_id"] == 123
    writes = list(api.writes)
    assert cli.main(args) == 0
    assert len(calls) == 1 and api.writes == writes
    capsys.readouterr()
    api.item["expired"] = True  # the official Release asset remains readable
    assert cli.main(verify_args()) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["validation"]["run_id"] == api.saved["workflow"]["run_id"]
    assert result["publication"]["status"] == "published" and api.writes == writes


def test_success_without_final_release_is_not_reported_as_publication(monkeypatch):
    api = API()
    monkeypatch.setattr(cli, "GitHub", lambda repo: api)
    monkeypatch.setattr(cli, "prepare_publication", lambda options: 0)
    monkeypatch.setattr(cli, "entrypoint", lambda args: 0)
    assert cli.main(["publish", "--version", "0.4.11", "--repo", REPOSITORY]) == 1
    assert not api.writes


@pytest.mark.parametrize("latest_conclusion", ["success", "failure"])
def test_published_evidence_keeps_original_attempt_after_run_retry(
    latest_conclusion, monkeypatch, capsys, tmp_path
):
    class History(API):
        def __call__(self, path, method="GET", data=None):
            if path == "actions/runs/123/attempts/1" and hasattr(self, "original"):
                self.calls.append((path, method, data))
                assert method == "GET"
                return self.original
            return super().__call__(path, method, data)

    api = History()
    value = make_manifest(api, mode="publication")
    publishing.publish_release(
        api, TAG, HEAD, value, tmp_path / "release-manifest.json"
    )
    api.original = dict(api.run)
    api.run.update(run_attempt=2, conclusion=latest_conclusion)
    api.calls.clear()
    monkeypatch.setattr(cli, "GitHub", lambda repo: api)
    assert cli.main(verify_args()) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["validation"]["run_attempt"] == 1
    assert result["publication"]["status"] == "published"
    assert not api.writes

    api.jobs[0]["conclusion"] = "failure"
    assert cli.main(verify_args()) == 1
    assert json.loads(capsys.readouterr().out)["result"] == "unavailable"
    assert not api.writes


def test_version_conflict_does_not_dispatch(monkeypatch):
    api = API()
    monkeypatch.setattr(cli, "GitHub", lambda repo: api)
    monkeypatch.setattr(cli, "entrypoint", lambda args: pytest.fail("Version mismatch"))
    assert cli.main(["validate", "--repo", REPOSITORY, "--version", "0.4.9"]) == 1
    assert not api.writes


@pytest.mark.parametrize(
    "args",
    [
        ["validate"],
        ["publish"],
        ["verify", "--version", "0.4.11"],
        ["publish", "--version", "0.4.11", "--new-run"],
    ],
)
def test_selection_must_be_explicit(args):
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2


@pytest.mark.parametrize(
    "change",
    [
        "attempt",
        "expired",
        "digest",
        "wrong_run",
        "wrong_sha",
        "duplicate",
        "missing",
        "jobs",
    ],
)
def test_verification_rejects_changed_or_ambiguous_evidence(change):
    api = API()
    if change == "attempt":
        api.run["run_attempt"] = 2
    elif change == "expired":
        api.item["expired"] = True
    elif change == "digest":
        api.item["digest"] = "sha256:" + "0" * 64
    elif change == "wrong_run":
        api.item["workflow_run"]["id"] = 321
    elif change == "wrong_sha":
        api.item["workflow_run"]["head_sha"] = "b" * 40
    elif change == "duplicate":
        api.manifests.append({**api.item, "id": 100})
    elif change == "jobs":
        api.jobs[0]["conclusion"] = "failure"
    else:
        api.manifests = []
    with pytest.raises(ValueError):
        verification.validation(api, REPOSITORY, identity(), 123)
    assert not api.writes


def test_archive_paths_and_oversize_never_write_files(tmp_path):
    api = API()
    api.refresh("../release-manifest.json")
    with pytest.raises(ValueError, match="layout"):
        verification.validation(api, REPOSITORY, identity(), 123)
    assert not list(tmp_path.iterdir())
    api.refresh()
    api.item["size_in_bytes"] = 3 * 1024 * 1024
    with pytest.raises(ValueError):
        verification.validation(api, REPOSITORY, identity(), 123)


@pytest.mark.parametrize("status", [403, 503, None])
def test_native_manifest_archive_error_is_redacted(status, monkeypatch):
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0], 1, b"DO_NOT_RECORD", f"Token DO_NOT_RECORD HTTP {status}".encode()
        )

    monkeypatch.setattr("engineering_ci.github_cli.subprocess.run", run)
    with pytest.raises(ApiError) as error:
        GitHub(REPOSITORY).artifact_archive(99)
    assert error.value.status == status and "DO_NOT_RECORD" not in str(error.value)


def test_native_manifest_archive_uses_binary_gh_api(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, b"PK\x00\xff", b"")

    monkeypatch.setattr("engineering_ci.github_cli.subprocess.run", run)
    assert GitHub(REPOSITORY).artifact_archive(99) == b"PK\x00\xff"
    args, kwargs = calls[0]
    assert args == ["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/99/zip"]
    assert kwargs["timeout"] == 60 and not kwargs.get("text")
