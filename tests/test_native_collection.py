"""Native transport still honors the existing coordinator and ZIP contract."""

import io
import shutil
from pathlib import Path
from urllib.error import URLError

import pytest
from test_execution_integration import (
    workflow as workflow,  # noqa: PLC0414 - imported pytest fixture
)

from engineering_ci.execution.collector import ArtifactCollector
from engineering_ci.execution.coordinator import ExecutionCoordinator
from engineering_ci.execution.download import (
    DownloadAdapter,
    DownloadConfig,
    DownloadInterrupted,
)
from engineering_ci.execution.models import DispatchIdentity, ExecutionError


class Response(io.BytesIO):
    status = 200

    def __init__(self, body, interrupt=False):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body)), "ETag": '"stable"'}
        self.interrupt = interrupt
        self.calls = 0

    def read(self, size=-1):
        self.calls += 1
        if self.interrupt and self.calls > 1:
            raise URLError("Network interrupted with SIGNED URL")
        return super().read(size)


@pytest.mark.parametrize("backend", ["native", "auto"])
def test_native_collection_resume_keeps_run_and_completed_log(
    workflow, monkeypatch, backend
):
    client, _, store, collection, _ = workflow
    monkeypatch.setattr(shutil, "which", lambda _: None)
    calls, interrupt = [], [True]

    def opener(url, config):
        kind = "log" if "/log?" in url else "artifact"
        calls.append(kind)
        return Response(
            client.log if kind == "log" else client.body,
            interrupt=kind == "artifact" and interrupt[0],
        )

    dispatch_identity = DispatchIdentity.create("owner/repo", "ci.yml", "main", {})

    def coordinator():
        adapter = DownloadAdapter(DownloadConfig(backend=backend), opener=opener)
        return ExecutionCoordinator(
            store,
            client,
            ArtifactCollector(client, adapter),
            dispatch_identity,
            collection,
            {},
        )

    with pytest.raises(DownloadInterrupted):
        coordinator().execute()
    assert store.load().phase == "PAUSED" and store.load().run_id == 123
    assert client.posts == 1 and calls == ["log", "artifact"]
    assert (Path(collection.out) / "123/run.log").is_file()
    interrupt[0] = False
    assert coordinator().execute(resume="last").exit_code == 0
    assert client.posts == 1 and calls == ["log", "artifact", "artifact"]
    assert store.load().phase == "FINISHED"
    assert "SIGNED" not in store.path.read_text()
    assert (Path(collection.out) / "123/result.txt").read_text() == "build output"


def test_native_bad_zip_never_finishes(workflow):
    client, _, store, collection, _ = workflow
    invalid = b"x" * len(client.log)  # same length; logs have no server Digest
    adapter = DownloadAdapter(
        DownloadConfig(backend="native"), opener=lambda *a: Response(invalid)
    )
    coordinator = ExecutionCoordinator(
        store,
        client,
        ArtifactCollector(client, adapter),
        DispatchIdentity.create("owner/repo", "ci.yml", "main", {}),
        collection,
        {},
    )
    with pytest.raises(ExecutionError):
        coordinator.execute()
    assert store.load().phase != "FINISHED" and client.posts == 1
