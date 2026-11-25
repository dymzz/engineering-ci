"""The timing receipt covers one native verify invocation."""

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

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


@pytest.mark.parametrize("linked_python", [False, True])
def test_verify_prefers_sibling_lefthook_over_global_path(
    tmp_path, monkeypatch, linked_python
):
    fake_bin = tmp_path / "venv"
    fake_bin.mkdir()
    fake_python = fake_bin / ("python.exe" if os.name == "nt" else "python")
    sibling = fake_bin / ("lefthook.exe" if os.name == "nt" else "lefthook")
    sibling.write_bytes(b"")
    global_bin = tmp_path / "global"
    global_bin.mkdir()
    if linked_python:
        base_python = global_bin / fake_python.name
        base_python.touch()
        try:
            fake_python.symlink_to(base_python)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                pytest.skip("Windows requires privilege to create file symlinks")
            raise
    else:
        fake_python.touch()
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


def test_real_subprocess_signal_interrupts_verify_without_receipt(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "verify@example.invalid"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Verify Test"], cwd=repo, check=True)
    python = sys.executable.replace("\\", "/")
    marker = repo / "started.flag"
    (repo / "job.py").write_text(
        f"""\
from pathlib import Path
import sys
import time

sys.stdout.write("partial-real-out\\n")
sys.stdout.flush()
Path({marker.as_posix()!r}).write_text("ready", encoding="utf-8")
time.sleep(30)
""",
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

    out = tmp_path / "artifacts"
    env = dict(os.environ)
    src_root = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = (
        f"{src_root}{os.pathsep}{env['PYTHONPATH']}"
        if env.get("PYTHONPATH")
        else src_root
    )
    popen_kwargs = {}
    sig = signal.SIGINT
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        sig = signal.CTRL_BREAK_EVENT

    proc = subprocess.Popen(
        [sys.executable, "-m", "engineering_ci.cli", "verify", "--out", str(out)],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **popen_kwargs,
    )
    try:
        deadline = time.monotonic() + 15.0
        while not marker.is_file():
            if proc.poll() is not None:
                raise AssertionError(
                    f"verify exited early with {proc.returncode}: "
                    f"{proc.stderr.read().decode('utf-8', 'replace')}"
                )
            if time.monotonic() >= deadline:
                raise TimeoutError("real lefthook verify job did not start in time")
            time.sleep(0.05)
        proc.send_signal(sig)
        stdout_bytes, stderr_bytes = proc.communicate(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    assert proc.returncode == 130, stderr_bytes.decode("utf-8", "replace")
    (stdout_log,) = out.glob("*/lefthook.stdout")
    (stderr_log,) = out.glob("*/lefthook.stderr")
    assert stdout_log.parent == stderr_log.parent
    assert not list(out.glob("*/verify-timing.json"))
    err_text = stderr_bytes.decode("utf-8", "replace")
    assert "verify interrupted; partial outputs in" in err_text
    assert str(stdout_log.parent) in err_text
    assert not stdout_bytes.strip()
    assert marker.is_file()
