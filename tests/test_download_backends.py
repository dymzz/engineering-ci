"""Backend selection and recovery use local HTTP or injected transports only."""

import hashlib
import io
import json
import shutil
from dataclasses import asdict
from urllib.error import HTTPError, URLError

import pytest
from test_http_download import http_server

from engineering_ci.execution.download import (
    DownloadAdapter,
    DownloadAddressExpired,
    DownloadConfig,
    DownloadConfigurationError,
    DownloadIntegrityError,
    DownloadInterrupted,
    HTTPMetadata,
    ResourceChanged,
)
from engineering_ci.execution.github import CommandResult
from engineering_ci.execution.models import RecoverableError
from engineering_ci.execution.store import atomic_json
from engineering_ci.execution.transport import ResourceIdentity


class Response(io.BytesIO):
    status = 200

    def __init__(self, body=b"OK", *, size=None, etag='"v1"'):
        super().__init__(body)
        self.headers = {
            "Content-Length": str(len(body) if size is None else size),
            "ETag": etag,
        }


def identity(body=b"OK"):
    return ResourceIdentity(
        "artifact:1", len(body), "sha256:" + hashlib.sha256(body).hexdigest(), True
    )


@pytest.mark.parametrize("mode", ["native", "auto"])
def test_native_and_auto_fallback(tmp_path, monkeypatch, capsys, mode):
    monkeypatch.setattr(shutil, "which", lambda _: None)

    def runner(*a, **kw):
        pytest.fail("No external process is permitted for native download")

    adapter = DownloadAdapter(
        DownloadConfig(backend=mode), runner=runner, opener=lambda *a: Response()
    )
    target = tmp_path / "download.zip"
    result = adapter.download(
        lambda: "https://example.test/file?secret=SIGNED", identity(), target
    )
    assert target.read_bytes() == b"OK" and not result.progress.resumed
    assert not target.with_name(target.name + ".native.part").exists()
    marker = target.with_name(target.name + ".resource.json").read_text()
    assert "SIGNED" not in marker and json.loads(marker)["backend"] == "native"
    assert "byte resume unsupported" in capsys.readouterr().err
    adapter.download(
        lambda: pytest.fail("Completed files need no new URL"), identity(), target
    )


def test_forced_missing_aria2_is_configuration_error(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    adapter = DownloadAdapter(DownloadConfig(backend="aria2"))
    with pytest.raises(DownloadConfigurationError, match="aria2c not found"):
        adapter.download(
            lambda: pytest.fail("Do not resolve a URL"), identity(), tmp_path / "file"
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("mode", ["aria2", "auto"])
def test_available_aria2_selected(tmp_path, monkeypatch, mode, capsys):
    monkeypatch.setattr(shutil, "which", lambda exe: exe)
    target = tmp_path / "file.zip"
    commands = []

    def runner(args, **kwargs):
        commands.append(args)
        target.write_bytes(b"OK")
        return CommandResult(0)

    adapter = DownloadAdapter(
        DownloadConfig(backend=mode),
        runner=runner,
        probe=lambda *a: HTTPMetadata(True, 2, '"v1"'),
        opener=lambda *a: pytest.fail("Do not switch to native"),
    )
    assert (
        adapter.download(lambda: "https://example.test/file", identity(), target).path
        == target
    )
    assert len(commands) == 1 and "--max-tries=1" in commands[0]
    assert "--max-connection-per-server=4" in commands[0]
    assert "Download backend: aria2" in capsys.readouterr().err


def test_native_local_http_restart_and_verified_cache(tmp_path):
    with http_server(ranged=True, slow=False) as (url, body, requests, interrupt):
        target = tmp_path / "directory with spaces" / "file with spaces.zip"
        adapter = DownloadAdapter(DownloadConfig(backend="native"))
        with pytest.raises(DownloadInterrupted) as failure:
            adapter.download(lambda: url, identity(body), target)
        assert failure.value.progress.completed_bytes > 0
        assert (
            target.with_name(target.name + ".native.part").is_file()
            and not target.exists()
        )
        interrupt[0] = False
        result = DownloadAdapter(DownloadConfig(backend="native")).download(
            lambda: url, identity(body), target
        )
        assert target.read_bytes() == body and not result.progress.resumed
        assert "byte resume" in result.progress.restart_reason
        assert all(start is None for start in requests)
        before = len(requests)
        adapter.download(
            lambda: pytest.fail("No network for verified files"), identity(body), target
        )
        assert len(requests) == before


@pytest.mark.parametrize(
    "error", [URLError("TLS interrupted at SECRET"), TimeoutError("SECRET")]
)
def test_native_network_failure_is_sanitized(tmp_path, error):
    def opener(*a):
        raise error

    with pytest.raises(DownloadInterrupted) as failure:
        DownloadAdapter(DownloadConfig(backend="native"), opener=opener).download(
            lambda: "https://example.test/?token=SECRET", identity(), tmp_path / "file"
        )
    assert "SECRET" not in str(failure.value)


def test_native_expired_url_refresh_and_identity_check(tmp_path):
    target = tmp_path / "file"
    resource = identity()
    marker = target.with_name(target.name + ".resource.json")
    atomic_json(
        marker,
        {
            "backend": "native",
            "resource": asdict(resource),
            "validator": '"v1"',
            "size": 2,
            "complete": False,
        },
    )
    target.with_name(target.name + ".native.part").write_bytes(b"O")
    addresses = iter(["https://example.test/expired", "https://example.test/current"])

    def opener(url, config):
        if url.endswith("expired"):
            raise HTTPError(url, 403, "SIGNED", {}, None)
        return Response(etag='"changed"')

    with pytest.raises(ResourceChanged, match="validator changed"):
        DownloadAdapter(DownloadConfig(backend="native"), opener=opener).download(
            lambda: next(addresses), resource, target
        )
    assert target.with_name(target.name + ".native.part").read_bytes() == b"O"
    calls = []

    def success(url, config):
        calls.append(url)
        if len(calls) == 1:
            raise HTTPError(url, 410, "SIGNED", {}, None)
        return Response()

    result = DownloadAdapter(DownloadConfig(backend="native"), opener=success).download(
        lambda: "https://example.test/current", resource, target
    )
    assert len(calls) == 2 and result.path.read_bytes() == b"OK"


def test_native_expired_url_refresh_is_bounded(tmp_path):
    calls = []

    def opener(url, config):
        calls.append(url)
        raise HTTPError(url, 403, "SECRET", {}, None)

    with pytest.raises(DownloadAddressExpired) as failure:
        DownloadAdapter(DownloadConfig(backend="native"), opener=opener).download(
            lambda: "https://example.test/?token=SECRET", identity(), tmp_path / "file"
        )
    assert len(calls) == 2 and "SECRET" not in str(failure.value)


def test_native_digest_corruption_not_marked_complete(tmp_path):
    target = tmp_path / "file"
    with pytest.raises(DownloadIntegrityError, match="digest"):
        DownloadAdapter(
            DownloadConfig(backend="native"), opener=lambda *a: Response(b"NO")
        ).download(lambda: "https://example.test/file", identity(), target)
    assert (
        not target.exists()
        and not target.with_name(target.name + ".native.part").exists()
    )
    assert not json.loads(target.with_name(target.name + ".resource.json").read_text())[
        "complete"
    ]


def test_corrupt_complete_file_redownloaded(tmp_path):
    target = tmp_path / "file"
    adapter = DownloadAdapter(
        DownloadConfig(backend="native"), opener=lambda *a: Response()
    )
    adapter.download(lambda: "https://example.test/file", identity(), target)
    target.write_bytes(b"NO")
    adapter.download(lambda: "https://example.test/file", identity(), target)
    assert target.read_bytes() == b"OK"


@pytest.mark.parametrize("new_backend", ["native", "aria2"])
@pytest.mark.parametrize("recovery", ["restart", "pause"])
def test_backend_switch_never_reuses_unfinished_bytes(
    tmp_path, monkeypatch, new_backend, recovery
):
    monkeypatch.setattr(shutil, "which", lambda exe: exe)
    target = tmp_path / "file"
    old_backend = "aria2" if new_backend == "native" else "native"
    if old_backend == "aria2":
        target.write_bytes(b"O")
        target.with_name(target.name + ".aria2").write_bytes(b"native aria checkpoint")
    else:
        target.with_name(target.name + ".native.part").write_bytes(b"O")
    atomic_json(
        target.with_name(target.name + ".resource.json"),
        {
            "backend": old_backend,
            "resource": asdict(identity()),
            "validator": '"v1"',
            "size": 2,
            "complete": False,
        },
    )

    def runner(args, **kw):
        assert "--continue=false" in args
        target.write_bytes(b"OK")
        return CommandResult(0)

    adapter = DownloadAdapter(
        DownloadConfig(backend=new_backend, recovery=recovery),
        runner=runner,
        probe=lambda *a: HTTPMetadata(True, 2, '"v1"'),
        opener=lambda *a: Response(),
    )
    if recovery == "pause":
        with pytest.raises(RecoverableError, match="backend changed"):
            adapter.download(lambda: "https://example.test/file", identity(), target)
    else:
        result = adapter.download(
            lambda: "https://example.test/file", identity(), target
        )
        assert (
            not result.progress.resumed
            and "backend changed" in result.progress.restart_reason
        )
        assert target.read_bytes() == b"OK"


@pytest.mark.parametrize("new_backend", ["aria2", "native"])
def test_verified_complete_file_can_switch_backends(tmp_path, monkeypatch, new_backend):
    monkeypatch.setattr(shutil, "which", lambda exe: exe)
    target = tmp_path / "file"
    old = DownloadAdapter(
        DownloadConfig(backend="native"), opener=lambda *a: Response()
    )
    old.download(lambda: "https://example.test/file", identity(), target)
    DownloadAdapter(DownloadConfig(backend=new_backend)).download(
        lambda: pytest.fail("Verified file must not be downloaded again"),
        identity(),
        target,
    )


@pytest.mark.parametrize(
    "executable", [r"D:\tools with spaces\aria2c.exe", "/opt/tools with spaces/aria2c"]
)
def test_aria2_platform_paths_are_process_arguments_not_shell_commands(
    tmp_path, monkeypatch, executable
):
    monkeypatch.setattr(shutil, "which", lambda exe: exe)
    target = tmp_path / "directory with spaces" / "archive.zip"

    def runner(args, **kwargs):
        assert args[0] == executable
        assert str(target.parent) in kwargs["stdin"]
        assert all("SIGNED" not in arg for arg in args)
        target.write_bytes(b"OK")
        return CommandResult(0)

    DownloadAdapter(
        DownloadConfig(backend="aria2", executable=executable),
        runner=runner,
        probe=lambda *a: HTTPMetadata(True, 2, '"v1"'),
    ).download(lambda: "https://example.test/file?token=SIGNED", identity(), target)
