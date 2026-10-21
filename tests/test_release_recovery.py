"""Hard-stop acceptance uses fresh processes, real Git/files and fake GitHub I/O."""

from __future__ import annotations

import json
import multiprocessing
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from test_execution_integration import ActionsFake, ArchiveFake
from test_release_delivery import REPOSITORY, VERSION, Repository

from engineering_ci.execution.collector import ArtifactCollector
from engineering_ci.execution.coordinator import ExecutionCoordinator
from engineering_ci.execution.models import (
    CollectionIdentity,
    DispatchIdentity,
    DispatchUnknown,
)
from engineering_ci.execution.store import StateStore
from engineering_ci.release import delivery


def increment(counters, index):
    with counters.get_lock():
        counters[index] += 1


def stop_at(ready, hold):
    """Let the parent kill the child without exception cleanup."""
    ready.set()
    assert hold.wait(30), "Parent did not stop the test process"


def stop_process(process):
    """Release only the process owned by this test, including on assertion failure."""
    if process.is_alive():
        process.kill()
    process.join(10)
    if process.is_alive():
        process.kill()
        process.join(5)
    assert not process.is_alive()


def execution_worker(
    directory, point, payloads, counters, ready, hold, outcomes, resume
):
    """Exercise the actual FSM/store/collector through a deterministic transport."""
    directory = Path(directory)
    store = StateStore(directory / "state.json")

    class Client(ActionsFake):
        def dispatch(self, identity, inputs):
            increment(counters, 0)
            if point == "DISPATCHING":
                assert store.load().phase == "DISPATCHING"
                stop_at(ready, hold)
            return super().dispatch(identity, inputs)

        def wait(self, *args):
            assert args[1] == 123  # repeated polling must retain the same Run
            increment(counters, 1)
            if point == "RUNNING":
                assert store.load().run_id == 123
                stop_at(ready, hold)
            return super().wait(*args)

    client = Client()
    client.log, client.body = payloads
    # ZIP timestamps must stay identical across processes/resource identities.
    import hashlib
    from dataclasses import replace

    client.info = replace(
        client.info,
        size=len(client.body),
        digest="sha256:" + hashlib.sha256(client.body).hexdigest(),
    )

    class Adapter(ArchiveFake):
        def download(self, resolve_url, identity, target):
            artifact = not identity.key.startswith("logs:")
            increment(counters, 3 if artifact else 2)
            if point == "COLLECTING" and artifact:
                assert store.load().phase == "COLLECTING"
                assert (Path(collection.out) / "123/run.log").is_file()
                stop_at(ready, hold)
            return super().download(resolve_url, identity, target)

    identity = DispatchIdentity.create("owner/repo", "ci.yml", "main", {})
    collection = CollectionIdentity.create(directory / "out", "build-{run_id}", True)
    coordinator = ExecutionCoordinator(
        store,
        client,
        ArtifactCollector(client, Adapter(client)),
        identity,
        collection,
        {},
    )
    from engineering_ci.execution import store as state_module

    original_replace = state_module.os.replace

    def replace_file(source, destination):
        if (
            point == "BEFORE_DISPATCH_REPLACE"
            and Path(destination) == store.path
            and json.loads(Path(source).read_text(encoding="utf-8"))["phase"]
            == "DISPATCHING"
        ):
            assert store.load().phase == "VALIDATED"
            stop_at(ready, hold)
        return original_replace(source, destination)

    try:
        with patch.object(state_module.os, "replace", replace_file):
            result = coordinator.execute(resume=resume)
        outcomes.put(
            {
                "status": "finished",
                "run_id": result.run_id,
                "exit_code": result.exit_code,
            }
        )
    except DispatchUnknown:
        outcomes.put({"status": "unknown", "phase": store.load().phase})
    except (
        OSError,
        ValueError,
        RuntimeError,
        AssertionError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as error:
        outcomes.put(
            {"status": "error", "type": type(error).__name__, "reason": str(error)}
        )


@pytest.mark.parametrize(
    "point,phase",
    [
        ("BEFORE_DISPATCH_REPLACE", "VALIDATED"),
        ("DISPATCHING", "DISPATCHING"),
        ("RUNNING", "RUNNING"),
        ("COLLECTING", "COLLECTING"),
    ],
)
def test_hard_kill_recovers_durable_execution_in_a_fresh_process(
    tmp_path, point, phase
):
    context = multiprocessing.get_context("spawn")
    counters = context.Array("i", [0, 0, 0, 0])  # POST, wait, logs, artifact attempts
    ready, hold, outcomes = context.Event(), context.Event(), context.Queue()
    payload = ActionsFake()
    args = (
        str(tmp_path),
        point,
        (payload.log, payload.body),
        counters,
        ready,
        hold,
        outcomes,
        None,
    )
    interrupted = context.Process(target=execution_worker, args=args)
    interrupted.start()
    try:
        assert ready.wait(20), "Worker did not reach the intended durable checkpoint"
        assert StateStore(tmp_path / "state.json").load().phase == phase
        stop_process(interrupted)
        assert interrupted.exitcode != 0

        def restart(resume=None):
            worker = context.Process(
                target=execution_worker,
                args=(
                    str(tmp_path),
                    None,
                    (payload.log, payload.body),
                    counters,
                    ready,
                    hold,
                    outcomes,
                    resume,
                ),
            )
            worker.start()
            try:
                value = outcomes.get(timeout=20)
                worker.join(10)
                assert not worker.is_alive() and worker.exitcode == 0
                return value
            finally:
                stop_process(worker)

        if point == "DISPATCHING":
            assert restart() == {"status": "unknown", "phase": "UNKNOWN"}
            assert counters[0] == 1
            result = restart("123")  # explicit confirmation of the simulated Run
        else:
            result = restart()
        assert result == {"status": "finished", "run_id": 123, "exit_code": 0}
        record = StateStore(tmp_path / "state.json").load()
        assert record.phase == "FINISHED" and record.collection_complete
        assert counters[0] == 1
        assert counters[1] == (2 if point == "RUNNING" else 1)
        assert counters[2] == 1  # completed logs are not downloaded again
        assert counters[3] == (2 if point == "COLLECTING" else 1)
        assert "test error details" in (tmp_path / "out/123/run.log").read_text()
        assert (tmp_path / "out/123/result.txt").read_text() == "build output"
        assert "SIGNED" not in (tmp_path / "state.json").read_text()
    finally:
        stop_process(interrupted)
        outcomes.close()
        outcomes.join_thread()


def delivery_worker(root, point, counters, ready, hold, outcomes):
    """Recover native commit/push facts and a persisted fake PR in a fresh process."""
    root = Path(root)
    repo = object.__new__(Repository)
    repo.root, repo.remote = root, root.parent / "remote.git"
    repo.permission, repo.failure, repo.compare, repo.calls = (
        "WRITE",
        None,
        "identical",
        [],
    )
    remote_record = root / ".test-tools/fake-gh.json"
    repo.pr = json.loads(remote_record.read_text()) if remote_record.is_file() else None

    def run(args, **kwargs):
        if args[0] == "dagger":
            increment(counters, 3)
        result = repo.run(args, **kwargs)
        operation = (
            ("PR", 2)
            if args[:3] == ["gh", "pr", "create"]
            else {
                ("git", "commit"): ("COMMIT", 0),
                ("git", "push"): ("PUSH", 1),
            }.get(tuple(args[:2]))
        )
        if args[:3] == ["gh", "pr", "create"]:
            remote_record.parent.mkdir(parents=True, exist_ok=True)
            remote_record.write_text(json.dumps(repo.pr), encoding="utf-8")
        if operation and result.returncode == 0:
            increment(counters, operation[1])
            if point == operation[0]:
                stop_at(ready, hold)
        return result

    try:
        with (
            patch.object(delivery, "checked_version", return_value=VERSION),
        ):
            code = delivery.prepare(
                REPOSITORY,
                VERSION,
                message="Recovery fixture commit\n\nRetain the original message.",
                include_all=True,
                checkout=delivery.Checkout(REPOSITORY, root=root, run=run),
            )
        outcomes.put({"exit_code": code})
    except (
        OSError,
        ValueError,
        RuntimeError,
        AssertionError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as error:
        outcomes.put({"type": type(error).__name__, "reason": str(error)})


@pytest.mark.parametrize("point", ["COMMIT", "PUSH", "PR"])
def test_hard_kill_recovers_delivery_from_native_facts(tmp_path, point):
    repo = Repository(tmp_path)
    context = multiprocessing.get_context("spawn")
    counters = context.Array("i", [0, 0, 0, 0])  # commit, push, PR, simulated checks
    ready, hold, outcomes = context.Event(), context.Event(), context.Queue()
    args = (str(repo.root), point, counters, ready, hold, outcomes)
    first = context.Process(target=delivery_worker, args=args)
    first.start()
    try:
        assert ready.wait(20), "Delivery did not reach its interruption point"
        stop_process(first)
        assert first.exitcode != 0
        committed = repo.git("rev-parse", "HEAD")
        second = context.Process(
            target=delivery_worker,
            args=(str(repo.root), None, counters, ready, hold, outcomes),
        )
        second.start()
        try:
            assert outcomes.get(timeout=20) == {"exit_code": 3}
            second.join(10)
            assert second.exitcode == 0 and not second.is_alive()
        finally:
            stop_process(second)
        assert repo.git("rev-parse", "HEAD") == committed
        assert repo.git("rev-list", "--count", "HEAD") == "2"
        assert (
            repo.git("log", "-1", "--format=%B")
            == "Recovery fixture commit\n\nRetain the original message."
        )
        pr = json.loads((repo.root / ".test-tools/fake-gh.json").read_text())
        assert pr["headRefOid"] == committed and pr["state"] == "OPEN"
        assert counters[0] == 1 and counters[2] == 1
        assert (
            counters[3] >= 2
        )  # each invocation uses native Dagger, never a pass receipt
        # A native push can repeat as up-to-date; it must keep the exact same SHA.
        assert (
            repo.git("ls-remote", "origin", "refs/heads/codex/delivery").split()[0]
            == committed
        )
        if point == "PR":
            assert counters[1] == 1  # an existing current PR restores REVIEW.waiting
    finally:
        stop_process(first)
        outcomes.close()
        outcomes.join_thread()
