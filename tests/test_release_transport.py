"""Native gh against local HTTP doubles; these tests never publish to GitHub."""

import contextlib
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
from engineering_ci.release import publishing as release


@pytest.mark.parametrize("operation", ["upload", "download", "metadata"])
def test_permission_errors_keep_status_and_redact_responses(operation, tmp_path):
    result = subprocess.CompletedProcess(
        [],
        1,
        stdout=b'{"token":"SENSITIVE_RESPONSE"}',
        stderr=b"gh: SENSITIVE_RESPONSE (HTTP 403)",
    )
    client = release.GitHub("example/ci")
    with (
        patch.object(release.subprocess, "run", return_value=result) as process,
        pytest.raises(release.ApiError) as error,
    ):
        if operation == "upload":
            client.upload("v0.4.9", tmp_path / "release-manifest.json")
        elif operation == "download":
            client.download(20)
        else:
            client("releases")
    if operation == "upload":
        assert "--clobber" not in process.call_args.args[0]
    assert error.value.status == 403
    assert "SENSITIVE_RESPONSE" not in str(error.value)


def test_empty_delete_is_supported_but_empty_or_null_get_is_not_missing():
    client = release.GitHub("example/ci")
    result = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    with patch.object(release.subprocess, "run", return_value=result):
        assert client("releases/assets/20", "DELETE") is None
        with pytest.raises(ValueError):
            client("releases/tags/v0.4.9")
    result.stdout = "null"
    with (
        patch.object(release.subprocess, "run", return_value=result),
        pytest.raises(TypeError, match="object or array"),
    ):
        client("releases/tags/v0.4.9")


@contextlib.contextmanager
def server(response, observations):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observations.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "accept": self.headers.get("Accept"),
                }
            )
            status, headers, body = response()
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=instance.serve_forever, daemon=True)
    worker.start()
    try:
        yield instance.server_address[1]
    finally:
        instance.shutdown()
        instance.server_close()
        worker.join(timeout=5)


@pytest.mark.skipif(shutil.which("gh") is None, reason="native gh unavailable")
@pytest.mark.parametrize("redirect", [False, True])
def test_native_gh_download_handles_200_and_302_without_forwarding_auth(
    tmp_path,
    monkeypatch,
    redirect,
):
    # All possible native gh token sources are replaced; no real credential is used.
    for name in (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
    ):
        monkeypatch.setenv(name, "fixture-token-only")
    config = tmp_path / "gh-config"
    config.mkdir()
    monkeypatch.setenv("GH_CONFIG_DIR", str(config))
    monkeypatch.setenv("GH_HOST", "github.com")
    monkeypatch.setenv("GH_PROMPT_DISABLED", "1")
    monkeypatch.delenv("GH_DEBUG", raising=False)
    payload = json.dumps({"schema_version": 1}).encode()
    destination, origin = [], []
    with server(
        lambda: (200, {"Content-Type": "application/octet-stream"}, payload),
        destination,
    ) as target:

        def response():
            if redirect:
                return 302, {"Location": f"http://localhost:{target}/download"}, b""
            return 200, {"Content-Type": "application/octet-stream"}, payload

        with server(response, origin) as port:
            client = release.GitHub("example/ci")
            client.prefix = f"http://127.0.0.1:{port}/repos/example/ci/"
            assert client.download(20) == {"schema_version": 1}
    assert len(origin) == 1
    assert origin[0]["accept"] == "application/octet-stream"
    if redirect:
        assert len(destination) == 1
        assert destination[0]["authorization"] is None
    else:
        assert not destination
    assert os.environ["GH_TOKEN"] == "fixture-token-only"
