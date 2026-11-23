"""The timing receipt covers one native verify invocation."""

import hashlib
import json
import subprocess

import pytest

from engineering_ci.cli import main, verify


@pytest.mark.parametrize(
    ("exit_code", "end", "completed"),
    [(0, 50, 50), (1, 50, 50), (0, 20, None)],
)
def test_verify_preserves_native_output_and_completion(
    tmp_path, monkeypatch, exit_code, end, completed
):
    ticks = iter((30, end))
    monkeypatch.setattr(verify, "time_ns", lambda: next(ticks))
    config = b'{"verify":{}}\n'
    outputs = {
        ("lefthook", "version"): b"2.2.2\n",
        ("lefthook", "dump", "--format", "json"): config,
    }
    monkeypatch.setattr(verify.subprocess, "check_output", outputs.__getitem__)
    native_outputs = {"stdout": b"raw \xff\r\n", "stderr": b"diagnostic\n"}

    def run(command, *, stdout, check, stderr=None):
        if command[0] == "git":
            return subprocess.CompletedProcess(command, 0, b"a" * 40 + b"\n")
        assert command == ("lefthook", "run", "verify", "--no-auto-install")
        assert check is False
        assert not list(tmp_path.glob("*/verify-timing.json"))
        stdout.write(native_outputs["stdout"])
        stderr.write(native_outputs["stderr"])
        return subprocess.CompletedProcess(command, exit_code)

    monkeypatch.setattr(verify.subprocess, "run", run)
    assert main(["verify", "--out", str(tmp_path)]) == exit_code
    (path,) = tmp_path.glob("*/verify-timing.json")
    receipt = json.loads(path.read_bytes())
    assert receipt["invocation_id"] == path.parent.name
    assert receipt["observed_git_head"] == "a" * 40
    assert (
        receipt["observed_lefthook_config_sha256"] == hashlib.sha256(config).hexdigest()
    )
    assert receipt["lefthook_version"] == "2.2.2"
    assert receipt["started_at_ns"] == 30
    assert receipt["completed_at_ns"] == completed
    assert receipt["time_source_verified"] is False
    assert receipt["native_exit_code"] == exit_code
    for stream, data in native_outputs.items():
        ref = receipt["native_outputs"][stream]
        assert (path.parent / ref["path"]).read_bytes() == data
        assert ref["sha256"] == hashlib.sha256(data).hexdigest()


def test_interrupted_verify_has_no_completed_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(verify.subprocess, "check_output", lambda _: b"fixture\n")

    def run(command, **kwargs):
        if command[0] == "git":
            return subprocess.CompletedProcess(command, 1, b"")
        kwargs["stdout"].write(b"partial output\n")
        raise KeyboardInterrupt

    monkeypatch.setattr(verify.subprocess, "run", run)
    assert main(["verify", "--out", str(tmp_path)]) == 130
    (stdout,) = tmp_path.glob("*/lefthook.stdout")
    assert stdout.read_bytes() == b"partial output\n"
    assert not list(tmp_path.glob("*/verify-timing.json"))
