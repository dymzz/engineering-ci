"""The timing receipt covers one native verify invocation."""

import hashlib
import json
import os
import subprocess
import sys

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
    monkeypatch.setattr(verify, "_lefthook_binary", lambda: "lefthook")
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


def test_interrupted_verify_has_no_completed_receipt(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(verify, "_lefthook_binary", lambda: "lefthook")
    outputs = {
        ("lefthook", "version"): b"2.2.2\n",
        ("lefthook", "dump", "--format", "json"): b'{"verify":{}}\n',
    }
    monkeypatch.setattr(verify.subprocess, "check_output", outputs.__getitem__)

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
    err = capsys.readouterr().err
    assert str(stdout.parent) in err


def test_verify_prefers_sibling_lefthook_over_global_path(tmp_path, monkeypatch):
    fake_bin = tmp_path / "venv"
    fake_bin.mkdir()
    fake_python = fake_bin / ("python.exe" if os.name == "nt" else "python")
    fake_python.write_bytes(b"")
    sibling = fake_bin / ("lefthook.exe" if os.name == "nt" else "lefthook")
    sibling.write_bytes(b"")
    global_bin = tmp_path / "global"
    global_bin.mkdir()
    global_lh = global_bin / ("lefthook.exe" if os.name == "nt" else "lefthook")
    global_lh.write_bytes(b"")
    monkeypatch.setattr(verify.sys, "executable", str(fake_python))
    monkeypatch.setenv("PATH", str(global_bin))
    assert verify._lefthook_binary() == str(sibling)


def test_verify_rejects_config_without_verify_hook(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(verify, "_lefthook_binary", lambda: "lefthook")
    outputs = {
        ("lefthook", "version"): b"2.2.2\n",
        ("lefthook", "dump", "--format", "json"): b'{"pre-commit":{}}\n',
    }
    monkeypatch.setattr(verify.subprocess, "check_output", outputs.__getitem__)
    monkeypatch.setattr(
        verify.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, b"a" * 40 + b"\n"),
    )
    assert main(["verify", "--out", str(tmp_path)]) == 1
    assert not list(tmp_path.iterdir())
    assert "no 'verify' hook" in capsys.readouterr().err


@pytest.mark.parametrize("job_exit", [0, 2])
def test_verify_runs_real_lefthook_in_isolated_repo(tmp_path, monkeypatch, job_exit):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "verify@example.invalid"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Verify Test"], cwd=repo, check=True)
    python = sys.executable.replace("\\", "/")
    (repo / "job.py").write_text(
        f"import sys\nsys.stdout.write('sample-out\\n')\nsys.stderr.write('sample-err\\n')\nsys.exit({job_exit})\n",
        encoding="utf-8",
        newline="\n",
    )
    (repo / "lefthook.yml").write_text(
        f'min_version: "2.2.2"\nverify:\n  jobs:\n    - name: sample\n      run: >-\n        "{python}" job.py\n',
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(["git", "add", "lefthook.yml", "job.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    dumped = subprocess.check_output(
        [verify._lefthook_binary(), "dump", "--format", "json"], cwd=repo
    )
    out = tmp_path / "artifacts"
    monkeypatch.chdir(repo)

    rc = main(["verify", "--out", str(out)])
    if job_exit == 0:
        assert rc == 0
    else:
        assert rc != 0

    (receipt_path,) = out.glob("*/verify-timing.json")
    receipt = json.loads(receipt_path.read_bytes())
    assert receipt["observed_git_head"] == head
    assert (
        receipt["observed_lefthook_config_sha256"] == hashlib.sha256(dumped).hexdigest()
    )
    assert receipt["lefthook_version"] == "2.2.2"
    assert receipt["completed_at_ns"] >= receipt["started_at_ns"]
    assert receipt["time_source_verified"] is False
    assert receipt["native_exit_code"] == rc
    for stream in ("stdout", "stderr"):
        ref = receipt["native_outputs"][stream]
        raw = (receipt_path.parent / ref["path"]).read_bytes()
        assert ref["sha256"] == hashlib.sha256(raw).hexdigest()
    combined = (receipt_path.parent / "lefthook.stdout").read_bytes() + (
        receipt_path.parent / "lefthook.stderr"
    ).read_bytes()
    assert b"sample-out" in combined
    assert b"sample-err" in combined
