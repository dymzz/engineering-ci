"""Use installed vesryn-log writers at the existing task observation boundary."""

import builtins
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from subprocess import Popen as ExistingPopen

import pytest

from engineering_ci import task_observation as observation

DELIVER = observation.deliver
PERSIST = observation.persist_delivery


def command(code=0):
    return [sys.executable, "-c", f"print('PRIVATE-OUTPUT'); exit({code})"]


def test_existing_commands_threads_results_and_exceptions_are_preserved(
    tmp_path, monkeypatch
):
    original = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == "vesryn" or name.startswith("vesryn."):
            pytest.fail("host imported Vesryn")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    method = subprocess.Popen.__init__
    with observation.Capture(tmp_path, "Inspect project tools") as task:
        result = subprocess.run(command(7), capture_output=True, check=False)
        assert result.returncode == 7 and result.stdout.strip() == b"PRIVATE-OUTPUT"
        with pytest.raises(subprocess.CalledProcessError) as error:
            subprocess.check_output(command(13))
        assert error.value.returncode == 13
        with pytest.raises(FileNotFoundError):
            subprocess.run([str(tmp_path / "missing-program")], check=True)
        with ExistingPopen(command(), stdout=subprocess.PIPE) as process:
            assert process.communicate()[0].strip() == b"PRIVATE-OUTPUT"
            assert process.wait() == process.poll() == 0
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(
                pool.map(
                    lambda _: (
                        subprocess.run(
                            command(), stdout=subprocess.DEVNULL, check=False
                        ).returncode
                    ),
                    range(2),
                )
            ) == [0, 0]
        task.exit_code = 0
    assert subprocess.Popen.__init__ is method
    returned = [event["call"] for event in task.events if event["event"] == "returned"]
    assert [call["exit_code"] for call in returned] == [7, 13, None, 0, 0, 0]
    assert task.envelope()["exit_code"] == 0
    assert "PRIVATE-OUTPUT" not in json.dumps(task.envelope())
    assert "argv" not in json.dumps(task.envelope())


def test_default_entry_wraps_once_and_queries_are_read_only(tmp_path, monkeypatch):
    monkeypatch.setattr(observation, "ROOT", tmp_path)
    tasks = []
    monkeypatch.setattr(observation, "deliver", lambda task: tasks.append(task))

    @observation.task_entry("Nested task", read_only=("inspect",))
    def nested(argv=None):
        return 17

    @observation.task_entry("Run checks", read_only=("inspect",))
    def entry(argv=None):
        return nested(argv)

    assert entry(["pr"]) == 17
    assert len(tasks) == 1 and tasks[0].exit_code == 17
    assert entry(["--help"]) == entry(["inspect"]) == 17
    assert len(tasks) == 1


def test_transport_failure_never_masks_task_exception_or_reexecutes(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(observation, "ROOT", tmp_path)

    def unavailable(task):
        calls.append(task)
        raise OSError("PRIVATE-RECORDING-ERROR")

    monkeypatch.setattr(observation, "deliver", unavailable)

    @observation.task_entry("Preserve task failure")
    def entry(argv=None):
        raise RuntimeError("PRIVATE-EXCEPTION")

    with pytest.raises(RuntimeError, match="PRIVATE-EXCEPTION"):
        entry(["run"])
    assert len(calls) == 1 and calls[0].status == "raised"
    assert calls[0].receipt["observation_delivery"] == "unavailable"
    assert observation._ACTIVE is None


@pytest.fixture
def real_recording(monkeypatch):
    monkeypatch.setattr(observation, "deliver", DELIVER)
    monkeypatch.setattr(observation, "persist_delivery", PERSIST)


def test_actual_entry_uses_installed_writers_without_vesryn_or_dagger(
    tmp_path, monkeypatch, capsys, real_recording
):
    from vesryn_log import JsonFileSink, JsonlSink

    from engineering_ci import checks, workflow_actions

    monkeypatch.setattr(observation, "ROOT", tmp_path)
    original_import = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == "vesryn" or name.startswith("vesryn."):
            pytest.fail("logging imported Vesryn")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    writes = []
    for kind in (JsonFileSink, JsonlSink):
        original = kind.emit

        def emit(self, value, original=original):
            writes.append(type(self).__module__)
            return original(self, value)

        monkeypatch.setattr(kind, "emit", emit)
    commands = []
    run = subprocess.run

    def execute(args, **kwargs):
        commands.append(args)
        assert args[0] != "dagger"
        return run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", execute)

    def synthetic(args, **kwargs):
        return subprocess.run(command(7), capture_output=True, check=False).returncode

    monkeypatch.setattr(checks, "main", synthetic)
    assert workflow_actions.main(["checks", "pr"]) == 7
    assert (
        len(commands) == 1
        and writes
        and all(name.startswith("vesryn_log.") for name in writes)
    )
    pointer = json.loads((tmp_path / ".log/latest-task.json").read_bytes())
    folder = tmp_path / ".log" / pointer["run_path"]
    capture = json.loads((folder / "capture.json").read_bytes())
    events = [
        json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines()
    ]
    assert events == capture["events"]
    assert capture["context"]["task_id"] == pointer["task_id"]
    assert capture["exit_code"] == 7
    calls = [event["call"] for event in events if event["event"] == "returned"]
    assert len(calls) == 1 and calls[0]["exit_code"] == 7 and calls[0]["call_id"]
    receipt = json.loads((folder / "delivery.json").read_bytes())
    assert receipt["recorder"] == "vesryn-log" and receipt["task_exit_code"] == 7
    assert "PRIVATE-OUTPUT" not in json.dumps(capture)
    assert "available" in capsys.readouterr().err


@pytest.mark.parametrize("outcome", [0, 7, "raised"])
def test_recording_io_failure_preserves_task_result_exception_and_previous_pointer(
    tmp_path, monkeypatch, capsys, real_recording, outcome
):
    from vesryn_log import EventWriteError, JsonlSink

    monkeypatch.setattr(observation, "ROOT", tmp_path)
    latest = tmp_path / ".log/latest-task.json"
    latest.parent.mkdir()
    latest.write_bytes(b'{"previous":"record"}')

    def failed(self, event):
        raise EventWriteError("PRIVATE-SINK-ERROR")

    monkeypatch.setattr(JsonlSink, "emit", failed)
    original = RuntimeError("PRIVATE-EXCEPTION")

    @observation.task_entry("Synthetic observed task")
    def entry(argv=None):
        if outcome == "raised":
            raise original
        return outcome

    if outcome == "raised":
        with pytest.raises(RuntimeError) as error:
            entry(["run"])
        assert error.value is original
    else:
        assert entry(["run"]) == outcome
    assert latest.read_bytes() == b'{"previous":"record"}'
    receipt = json.loads(
        next((tmp_path / ".log/tasks").glob("*/*/delivery.json")).read_bytes()
    )
    assert receipt["observation_delivery"] == "unavailable"
    assert receipt["task_exit_code"] == (None if outcome == "raised" else outcome)
    assert "PRIVATE" not in json.dumps(receipt) + capsys.readouterr().err


def test_unwritable_log_path_has_independent_diagnostic(
    tmp_path, monkeypatch, capsys, real_recording
):
    monkeypatch.setattr(observation, "ROOT", tmp_path)
    (tmp_path / ".log").write_text("not a directory")

    @observation.task_entry("Preserve original failure")
    def entry(argv=None):
        return 19

    assert entry(["run"]) == 19
    assert "unavailable" in capsys.readouterr().err


def test_missing_optional_logger_preserves_actual_dispatcher_result(
    tmp_path, monkeypatch, capsys, real_recording
):
    from engineering_ci import checks, workflow_actions

    monkeypatch.setattr(observation, "ROOT", tmp_path)
    original = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == "vesryn_log" or name.startswith("vesryn_log."):
            raise ModuleNotFoundError("Logger not installed", name="vesryn_log")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    monkeypatch.setattr(checks, "main", lambda *args, **kwargs: 7)
    assert workflow_actions.main(["checks", "pr"]) == 7
    assert "unavailable" in capsys.readouterr().err
    assert not (tmp_path / ".log").exists()


def test_scope_and_capture_budget(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="inside"):
        observation._inside(tmp_path, tmp_path.parent)
    monkeypatch.setattr(observation, "_MAX_BYTES", 1)
    with observation.Capture(tmp_path, "Bounded capture") as task:
        assert task.begin("git") is None
    assert task.errors > 0 and len(task.events) == 0


def test_recording_and_diagnostic_failures_preserve_task_result(tmp_path, monkeypatch):
    monkeypatch.setattr(observation, "ROOT", tmp_path)

    def failed(task):
        raise OSError("PRIVATE-ERROR")

    class BrokenOutput:
        def write(self, value):
            raise OSError("PRIVATE-ERROR")

    monkeypatch.setattr(observation, "persist_delivery", failed)
    monkeypatch.setattr(observation.sys, "stderr", BrokenOutput())

    @observation.task_entry("Preserve the original result")
    def entry(argv=None):
        return 17

    assert entry(["run"]) == 17
    assert observation._ACTIVE is None
