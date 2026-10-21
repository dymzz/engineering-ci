"""Native gh contracts and concrete dispatch safety; no GitHub Workflow is created."""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_release_verification import API, HEAD, REPOSITORY, TAG, identity

from engineering_ci.cli import download, release
from engineering_ci.github_cli import ApiError, GitHub
from engineering_ci.release import request


def response(status=200, body=None, code=0):
    if body is None:
        body = {"workflow_run_id": 123}
    return subprocess.CompletedProcess(
        [],
        code,
        f"HTTP/2.0 {status} Result\r\ncontent-type: application/json\r\n\r\n"
        + json.dumps(body),
        "SENSITIVE_AUTH",
    )


@pytest.mark.parametrize(
    "status,body,code,reason",
    [
        (204, {}, 0, "accepted_without_run_id"),
        (500, {}, 1, "dispatch_response_unavailable"),
        (200, {}, 0, "dispatch_response_invalid"),
        (200, [], 0, "dispatch_response_invalid"),
        (200, {"workflow_run_id": True}, 0, "dispatch_response_invalid"),
    ],
)
def test_uncertain_post_is_never_repeated(
    tmp_path, monkeypatch, status, body, code, reason
):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return response(status, body, code)

    monkeypatch.setattr(request.subprocess, "run", run)
    client = GitHub(REPOSITORY)
    path = tmp_path / "intent.json"
    with pytest.raises(request.UncertainDispatch, match=reason):
        request.dispatch(client, identity(), "publish", path=path)
    with pytest.raises(request.UncertainDispatch):
        request.dispatch(client, identity(), "publish", path=path)
    assert len(calls) == 1 and "SENSITIVE_AUTH" not in path.read_text()


def test_200_run_is_saved_immediately_and_reused(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        request.subprocess, "run", lambda args, **kw: calls.append(args) or response()
    )
    path = tmp_path / "intent.json"
    client = GitHub(REPOSITORY)
    assert request.dispatch(client, identity(), "publish", path=path) == 123
    assert json.loads(path.read_text())["run_id"] == 123
    assert request.dispatch(client, identity(), "publish", path=path) == 123
    assert len(calls) == 1 and "--include" in calls[0]
    with pytest.raises(request.UncertainDispatch, match="identity_mismatch"):
        request.dispatch(
            client, {**identity(), "commit_sha": "b" * 40}, "publish", path=path
        )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "failure",
    [ConnectionError("TOKEN"), subprocess.TimeoutExpired("gh", 1), KeyboardInterrupt()],
)
def test_post_interruption_keeps_intent_before_any_retry(
    tmp_path, monkeypatch, failure
):
    path = tmp_path / "intent.json"
    calls = []

    def run(*a, **kw):
        assert json.loads(path.read_text())["commit_sha"] == HEAD
        calls.append(a)
        raise failure

    monkeypatch.setattr(request.subprocess, "run", run)
    with pytest.raises((request.UncertainDispatch, KeyboardInterrupt)):
        request.dispatch(GitHub(REPOSITORY), identity(), "publish", path=path)
    with pytest.raises(request.UncertainDispatch):
        request.dispatch(GitHub(REPOSITORY), identity(), "publish", path=path)
    assert len(calls) == 1 and "TOKEN" not in path.read_text()


def test_422_is_rejected_and_can_be_corrected(tmp_path, monkeypatch):
    results = iter([response(422, {"message": "TOKEN"}, 1), response()])
    monkeypatch.setattr(request.subprocess, "run", lambda *a, **kw: next(results))
    path = tmp_path / "intent.json"
    with pytest.raises(ApiError) as error:
        request.dispatch(GitHub(REPOSITORY), identity(), "publish", path=path)
    assert (
        error.value.status == 422
        and not path.exists()
        and "TOKEN" not in str(error.value)
    )
    assert request.dispatch(GitHub(REPOSITORY), identity(), "publish", path=path) == 123


def test_malformed_body_and_incomplete_intent_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        request.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            [], 0, "HTTP/2.0 200 OK\n\n{broken", ""
        ),
    )
    path = tmp_path / "intent.json"
    with pytest.raises(request.UncertainDispatch):
        request.dispatch(GitHub(REPOSITORY), identity(), "publish", path=path)
    path.write_text("")
    with pytest.raises(request.UncertainDispatch, match="unreadable"):
        request.dispatch(GitHub(REPOSITORY), identity(), "publish", path=path)


def test_two_callers_send_at_most_one_post(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        request.subprocess, "run", lambda args, **kw: calls.append(args) or response()
    )

    def invoke():
        try:
            return request.dispatch(
                GitHub(REPOSITORY), identity(), "publish", path=tmp_path / "intent.json"
            )
        except request.UncertainDispatch:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: invoke(), range(2)))
    assert 123 in values and len(calls) == 1


def test_native_watch_preserves_command_and_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "engineering_ci.github_cli.subprocess.run",
        lambda args, **kw: calls.append(args) or subprocess.CompletedProcess(args, 17),
    )
    assert GitHub(REPOSITORY).watch(123) == 17
    assert calls == [
        ["gh", "run", "watch", "123", "--repo", REPOSITORY, "--exit-status"]
    ]


def test_download_failure_is_nonzero_and_never_posts(monkeypatch, tmp_path, capsys):
    client = GitHub(REPOSITORY)
    calls = []
    monkeypatch.setattr(client, "run_info", lambda _: API().run)
    monkeypatch.setattr(download, "GitHub", lambda _: client)

    def run(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 7, b"", b"SENSITIVE_AUTH (HTTP 403)")

    monkeypatch.setattr("engineering_ci.github_cli.subprocess.run", run)
    assert (
        download.main(
            ["--repo", REPOSITORY, "--run", "123", "--out", str(tmp_path), "--json"]
        )
        == 7
    )
    value = json.loads(capsys.readouterr().out)
    assert value["run_url"].endswith("/123") and "SENSITIVE_AUTH" not in str(value)
    assert calls[0][:3] == ["gh", "run", "download"] and "--method" not in calls[0]


def test_logs_for_failed_run_are_saved_by_native_gh(monkeypatch, tmp_path, capsys):
    client = GitHub(REPOSITORY)
    run = API().run
    run["conclusion"] = "failure"
    calls = []
    monkeypatch.setattr(client, "run_info", lambda _: run)
    monkeypatch.setattr(download, "GitHub", lambda _: client)

    def call(args, **kw):
        calls.append(args)
        kw["stdout"].write(b"failure diagnostics")
        return subprocess.CompletedProcess(args, 0, stderr=b"")

    monkeypatch.setattr("engineering_ci.github_cli.subprocess.run", call)
    args = [
        "--repo",
        REPOSITORY,
        "--run",
        "123",
        "--save-log",
        "--no-artifacts",
        "--out",
        str(tmp_path),
        "--json",
    ]
    assert download.main(args) == 0
    value = json.loads(capsys.readouterr().out)
    assert (
        value["conclusion"] == "failure"
        and (tmp_path / "123/run.log").read_text() == "failure diagnostics"
    )
    assert calls[0][:3] == ["gh", "run", "view"] and "--attempt" in calls[0]


@pytest.mark.parametrize(
    "change",
    [
        {"id": 456},
        {"repository": {"full_name": "other/repo"}},
        {"head_sha": "bad"},
        {"run_attempt": 0},
    ],
)
def test_foreign_or_malformed_run_is_rejected(monkeypatch, change):
    data = {**API().run, **change}
    monkeypatch.setattr(GitHub, "__call__", lambda *a: data)
    with pytest.raises(ValueError):
        GitHub(REPOSITORY).run_info(123)


@pytest.mark.parametrize(
    "args",
    [
        ["publish", "--version", TAG, "--all"],
        ["publish", "--version", TAG, "-m", "commit"],
        ["status", "--version", TAG],
        ["validate", "--version", TAG],
        ["publish", "--version", TAG, "--new-run"],
    ],
)
def test_retired_delivery_parameters_are_not_a_compatibility_protocol(args):
    with pytest.raises(SystemExit):
        release.main(args)


def test_nonzero_dispatch_with_run_id_keeps_identity_and_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(request.subprocess, "run", lambda *a, **k: response(code=7))
    path = tmp_path / "intent.json"
    with pytest.raises(request.UncertainDispatch) as error:
        request.dispatch(GitHub(REPOSITORY), identity(), "publish", path=path)
    assert error.value.run_id == 123 and error.value.exit_code == 7
    assert json.loads(path.read_text())["run_id"] == 123


def test_known_run_is_reported_if_record_update_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(request.subprocess, "run", lambda *a, **k: response())

    def fail(*a):
        raise OSError("SENSITIVE_AUTH")

    monkeypatch.setattr(request, "save_run", fail)
    path = tmp_path / "intent.json"
    with pytest.raises(request.UncertainDispatch) as error:
        request.dispatch(GitHub(REPOSITORY), identity(), "publish", path=path)
    assert error.value.run_id == 123 and error.value.exit_code == 2
    assert "SENSITIVE" not in str(error.value)
    with pytest.raises(request.UncertainDispatch, match="dispatch_result_unknown"):
        request.dispatch(GitHub(REPOSITORY), identity(), "publish", path=path)


def test_publish_is_one_post_and_not_reported_as_publication(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    client = API()
    client.prefix = f"repos/{REPOSITORY}/"
    client.run.update(status="queued", conclusion=None)
    monkeypatch.setattr(release, "GitHub", lambda _: client)
    calls = []
    monkeypatch.setattr(
        request.subprocess, "run", lambda a, **k: calls.append(a) or response()
    )
    args = ["publish", "--repo", REPOSITORY, "--version", TAG, "--json"]
    for _ in range(2):
        assert release.main(args) == 0
        value = json.loads(capsys.readouterr().out)
        assert value["result"] == "requested"
        assert value["publication"]["status"] == "unconfirmed"
        assert "--run 123" in value["next_commands"][0]
    assert len(calls) == 1 and "POST" in calls[0]
    assert not client.writes


def test_publish_existing_validation_is_not_publication(monkeypatch, capsys):
    client = API()
    monkeypatch.setattr(release, "GitHub", lambda _: client)
    assert (
        release.main(
            [
                "publish",
                "--repo",
                REPOSITORY,
                "--version",
                TAG,
                "--run",
                "123",
                "--json",
            ]
        )
        == 1
    )
    value = json.loads(capsys.readouterr().out)
    assert value["publication"]["status"] == "not_published"
    assert value["result"] == "unavailable" and not client.writes


def test_wrong_requested_sha_is_rejected_before_watch(monkeypatch, capsys):
    client = API()
    monkeypatch.setattr(release, "GitHub", lambda _: client)
    monkeypatch.setattr(
        release.verification,
        "target",
        lambda *a: {**identity(), "commit_sha": "b" * 40},
    )
    monkeypatch.setattr(release, "dispatch", lambda *a: 123)
    monkeypatch.setattr(release, "tag_commit", lambda *a: None)
    assert (
        release.main(
            ["publish", "--repo", REPOSITORY, "--version", TAG, "--wait", "--json"]
        )
        == 1
    )
    value = json.loads(capsys.readouterr().out)
    assert "differs from requested commit" in value["reason"]


@pytest.mark.parametrize("module", [release, download])
def test_watch_error_survives_failed_followup_api(module, monkeypatch, capsys):
    client = API()
    monkeypatch.setattr(module, "GitHub", lambda _: client)

    def watch(*a, **k):
        monkeypatch.setattr(
            client, "run_info", lambda _: (_ for _ in ()).throw(ApiError(403))
        )
        return 17

    client.watch = watch
    args = ["--repo", REPOSITORY, "--run", "123", "--json"]
    args = ["wait", "--version", TAG, *args] if module is release else [*args, "--wait"]
    assert module.main(args) == 17
    value = json.loads(capsys.readouterr().out)
    assert "123" in str(value["next_commands"]) and "SENSITIVE" not in str(value)


def test_attempt_change_during_wait_cannot_be_validated(monkeypatch, capsys):
    client = API()
    monkeypatch.setattr(release, "GitHub", lambda _: client)

    def watch(*a, **k):
        client.run = {**client.run, "run_attempt": client.run["run_attempt"] + 1}
        return 0

    client.watch = watch
    assert (
        release.main(
            ["wait", "--repo", REPOSITORY, "--version", TAG, "--run", "123", "--json"]
        )
        == 1
    )
    assert "attempt" in json.loads(capsys.readouterr().out)["reason"]


def test_partial_log_is_removed_and_existing_log_is_not_overwritten(
    monkeypatch, tmp_path, capsys
):
    client = API()
    monkeypatch.setattr(download, "GitHub", lambda _: client)

    def log(*args):
        args[-1].write(b"partial")
        raise ApiError(503, exit_code=7)

    client.run_log = log
    args = [
        "--repo",
        REPOSITORY,
        "--run",
        "123",
        "--save-log",
        "--no-artifacts",
        "--out",
        str(tmp_path),
        "--json",
    ]
    assert download.main(args) == 7
    capsys.readouterr()
    directory = tmp_path / "123"
    assert not list(directory.iterdir())
    (directory / "run.log").write_text("kept")
    assert download.main(args) == 1
    assert (directory / "run.log").read_text() == "kept"


def test_artifact_failure_keeps_saved_log_and_does_not_request_it_again(
    monkeypatch, tmp_path, capsys
):
    client = API()
    monkeypatch.setattr(download, "GitHub", lambda _: client)
    client.run_log = lambda run, attempt, stream: stream.write(b"diagnostics")

    def artifacts(*args):
        raise ApiError(503, exit_code=7)

    client.run_download = artifacts
    assert (
        download.main(
            [
                "--repo",
                REPOSITORY,
                "--run",
                "123",
                "--save-log",
                "--artifact",
                "core-tests",
                "--out",
                str(tmp_path),
                "--json",
            ]
        )
        == 7
    )
    value = json.loads(capsys.readouterr().out)
    assert (tmp_path / "123/run.log").read_text() == "diagnostics"
    assert "--save-log" not in value["next_commands"][0]
    assert "core-tests" in value["next_commands"][0]
