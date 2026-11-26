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
    assert not list(tmp_path.glob("*/verify-timing.json"))
    (status_path,) = tmp_path.glob("*/verify-status.json")
    status_data = json.loads(status_path.read_text(encoding="utf-8"))
    assert status_data["status"] == "preflight_failed"
    assert status_data["native_started"] is False
    assert status_data["native_exit_code"] is None
    assert status_data["time_source_verified"] is False
    assert status_data["stages"]["cli_preflight"]["elapsed_seconds"] >= 0
    assert "no 'verify' hook" in status_data["error"]["message"]
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
    (status_path,) = out.glob("*/verify-status.json")
    status_data = json.loads(status_path.read_text(encoding="utf-8"))
    assert status_data["status"] == "interrupted"
    assert status_data["native_started"] is True
    assert status_data["native_exit_code"] is None
    assert status_data["time_source_verified"] is False
    assert status_data["elapsed_seconds"] >= 0
    err_text = stderr_bytes.decode("utf-8", "replace")
    assert "verify interrupted; partial outputs in" in err_text
    assert str(stdout_log.parent) in err_text
    assert not stdout_bytes.strip()
    assert marker.is_file()


def test_verify_monotonic_stages_boundaries_and_clock_backward_jump(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(verify, "_lefthook_binary", lambda: "lefthook")
    monkeypatch.setattr(
        verify.subprocess,
        "check_output",
        lambda cmd: (
            b"2.2.2\n"
            if cmd[1] == "version"
            else b'{"verify":{"parallel":false,"jobs":[{"name":"lint","run":"echo ok"}]}}\n'
        ),
    )

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 0, b"b" * 40 + b"\n")
        kwargs["stdout"].write(
            b"summary: (done in 0.12 seconds)\n\xe2\x9c\x93 lint (0.12 seconds)\n"
        )
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(verify.subprocess, "run", fake_run)
    timestamps = iter([500, 100])
    monkeypatch.setattr(verify, "time_ns", lambda: next(timestamps))

    assert main(["verify", "--out", str(tmp_path)]) == 0
    (timing_path,) = tmp_path.glob("*/verify-timing.json")
    data = json.loads(timing_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 2
    assert data["timing_clock"] == "monotonic_perf_counter"
    assert data["time_source_verified"] is False
    assert data["started_at_ns"] == 500
    assert data["completed_at_ns"] is None
    assert data["clock_diagnostics"]["wall_clock_regression_observed"] is True
    assert data["clock_diagnostics"]["utc_elapsed_ns"] is None
    assert data["elapsed_seconds"] >= 0
    assert set(data["stages"]) == {
        "cli_preflight",
        "lefthook_native",
        "report_preparation",
    }
    for stage in data["stages"].values():
        assert stage["elapsed_seconds"] >= 0
    assert data["timing_boundaries"]["post_seal_io_excluded"] is True
    assert data["timing_boundaries"]["outer_launcher_startup_excluded"] is True
    assert not list(tmp_path.glob("*/verify-status.json"))


def test_verify_seal_failure_records_status_without_timing_receipt(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(verify, "_lefthook_binary", lambda: "lefthook")
    monkeypatch.setattr(
        verify.subprocess,
        "check_output",
        lambda cmd: (
            b"2.2.2\n"
            if cmd[1] == "version"
            else b'{"verify":{"jobs":[{"name":"lint","run":"echo ok"}]}}\n'
        ),
    )

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 0, b"c" * 40 + b"\n")
        kwargs["stdout"].write(b"summary: (done in 0.05 seconds)\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(verify.subprocess, "run", fake_run)
    orig_write = verify._write_json_atomic

    def fail_on_receipt(path, payload):
        if path.name == "verify-timing.json":
            raise OSError("disk full")
        return orig_write(path, payload)

    monkeypatch.setattr(verify, "_write_json_atomic", fail_on_receipt)

    assert main(["verify", "--out", str(tmp_path)]) == 1
    assert not list(tmp_path.glob("*/verify-timing.json"))
    (status_path,) = tmp_path.glob("*/verify-status.json")
    status_data = json.loads(status_path.read_text(encoding="utf-8"))
    assert status_data["status"] == "seal_failed"
    assert status_data["native_started"] is True
    assert status_data["native_exit_code"] == 0
    assert status_data["time_source_verified"] is False
    assert status_data["elapsed_seconds"] >= 0
    assert "disk full" in status_data["error"]["message"]
    assert "timing receipt unavailable: disk full" in capsys.readouterr().err

    # Unlink failure after atomic replace error must not mask observed native exit
    orig_replace = verify.Path.replace

    def fail_timing_replace(self, target):
        if Path(target).name == "verify-timing.json":
            raise OSError("replace failed")
        return orig_replace(self, target)

    monkeypatch.setattr(verify.Path, "replace", fail_timing_replace)
    monkeypatch.setattr(
        verify.Path,
        "unlink",
        lambda *a, **k: (_ for _ in ()).throw(OSError("unlink failed")),
    )
    monkeypatch.setattr(verify, "_write_json_atomic", orig_write)
    timestamps_2 = iter([600, 700])
    monkeypatch.setattr(verify, "time_ns", lambda: next(timestamps_2))
    out2 = tmp_path / "out2"
    assert main(["verify", "--out", str(out2)]) == 1
    assert not list(out2.glob("*/verify-timing.json"))
    (status2,) = out2.glob("*/verify-status.json")
    data2 = json.loads(status2.read_text(encoding="utf-8"))
    assert data2["status"] == "seal_failed"
    assert data2["native_exit_code"] == 0


def test_verify_real_lefthook_parses_parallel_nested_groups_skips_and_failures(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "verify@example.invalid"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Verify Test"], cwd=repo, check=True)
    (repo / "lefthook.yml").write_text(
        """verify:
  parallel: true
  jobs:
    - name: pass-job
      run: echo pass
    - name: fail-job
      run: exit 2
    - name: skip-job
      skip: true
      run: echo never
    - name: outer-grp
      group:
        parallel: false
        jobs:
          - name: inner-ok
            run: echo inner
          - name: subgrp
            group:
              parallel: true
              jobs:
                - name: deep-ok
                  run: echo deep
                - name: deep-skip
                  skip: true
                  run: echo deep-skip
    - group:
        parallel: true
        jobs:
          - name: unnamed-inner
            run: echo unnamed
""",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "lefthook.yml"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

    out = tmp_path / "out"
    monkeypatch.chdir(repo)
    code = main(["verify", "--out", str(out)])
    assert code == 1
    (receipt_path,) = out.glob("*/verify-timing.json")
    data = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert data["native_exit_code"] == 1
    summary = data["lefthook_summary"]
    assert summary["summary_present"] is True
    assert summary["root_parallel"] is True
    assert isinstance(summary["total_duration_seconds"], float)
    assert summary["total_duration_precision_seconds"] == 0.01

    by_name = {node["name"]: node for node in summary["items"]}
    assert by_name["pass-job"]["status"] == "passed"
    assert isinstance(by_name["pass-job"]["duration_seconds"], float)
    assert by_name["fail-job"]["status"] == "failed"
    assert isinstance(by_name["fail-job"]["duration_seconds"], float)
    assert by_name["skip-job"]["status"] == "skipped"
    assert by_name["skip-job"]["duration_seconds"] == "unknown"
    assert by_name["skip-job"]["skip_reason"] == "by condition"

    outer = by_name["outer-grp"]
    assert outer["kind"] == "group"
    assert outer["parallel"] is False
    outer_children = {c["name"]: c for c in outer["children"]}
    assert outer_children["inner-ok"]["status"] == "passed"
    subgrp = outer_children["subgrp"]
    assert subgrp["kind"] == "group"
    assert subgrp["parallel"] is True
    sub_children = {c["name"]: c for c in subgrp["children"]}
    assert sub_children["deep-ok"]["path"] == ["outer-grp", "subgrp", "deep-ok"]
    assert sub_children["deep-ok"]["status"] == "passed"
    assert sub_children["deep-skip"]["status"] == "skipped"
    assert sub_children["deep-skip"]["duration_seconds"] == "unknown"

    unnamed = by_name["group (4)"]
    assert unnamed["kind"] == "group"
    assert unnamed["parallel"] is True
    assert [c["name"] for c in unnamed["children"]] == ["unnamed-inner"]
    assert summary["counts"] == {
        "passed": 4,
        "failed": 1,
        "skipped": 2,
        "unknown": 0,
    }


def test_verify_output_open_and_spawn_failures_never_mark_native_started(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(verify, "_lefthook_binary", lambda: "lefthook")
    monkeypatch.setattr(
        verify.subprocess,
        "check_output",
        lambda cmd: (
            b"2.2.2\n"
            if cmd[1] == "version"
            else b'{"verify":{"jobs":[{"name":"lint","run":"echo ok"}]}}\n'
        ),
    )

    def spawn_fail_run(cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 0, b"a" * 40 + b"\n")
        raise FileNotFoundError("lefthook binary disappeared")

    monkeypatch.setattr(verify.subprocess, "run", spawn_fail_run)
    out_spawn = tmp_path / "spawn-fail"
    assert main(["verify", "--out", str(out_spawn)]) == 1
    assert not list(out_spawn.glob("*/verify-timing.json"))
    (spawn_status_path,) = out_spawn.glob("*/verify-status.json")
    spawn_status = json.loads(spawn_status_path.read_text(encoding="utf-8"))
    assert spawn_status["status"] == "spawn_failed"
    assert spawn_status["native_started"] is False
    assert spawn_status["native_exit_code"] is None

    orig_open = verify.Path.open

    def fail_stderr_open(self, *args, **kwargs):
        if self.name == "lefthook.stderr" and "x" in (args[0] if args else ""):
            raise PermissionError("stderr blocked")
        return orig_open(self, *args, **kwargs)

    monkeypatch.setattr(
        verify.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, b"a" * 40 + b"\n"),
    )
    monkeypatch.setattr(verify.Path, "open", fail_stderr_open)
    out_open = tmp_path / "open-fail"
    assert main(["verify", "--out", str(out_open)]) == 1
    assert not list(out_open.glob("*/verify-timing.json"))
    (open_status_path,) = out_open.glob("*/verify-status.json")
    open_status = json.loads(open_status_path.read_text(encoding="utf-8"))
    assert open_status["status"] == "output_open_failed"
    assert open_status["native_started"] is False
    assert open_status["native_exit_code"] is None


def test_verify_report_preparation_interrupt_preserves_observed_native_exit(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(verify, "_lefthook_binary", lambda: "lefthook")
    monkeypatch.setattr(
        verify.subprocess,
        "check_output",
        lambda cmd: (
            b"2.2.2\n"
            if cmd[1] == "version"
            else b'{"verify":{"jobs":[{"name":"lint","run":"echo ok"}]}}\n'
        ),
    )

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 0, b"d" * 40 + b"\n")
        kwargs["stdout"].write(b"raw stdout\n")
        return subprocess.CompletedProcess(cmd, 2)

    monkeypatch.setattr(verify.subprocess, "run", fake_run)
    monkeypatch.setattr(
        verify,
        "parse_lefthook_output",
        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    out = tmp_path / "prep-interrupt"
    assert main(["verify", "--out", str(out)]) == 130
    assert not list(out.glob("*/verify-timing.json"))
    (status_path,) = out.glob("*/verify-status.json")
    status_data = json.loads(status_path.read_text(encoding="utf-8"))
    assert status_data["status"] == "report_preparation_interrupted"
    assert status_data["native_started"] is True
    assert status_data["native_exit_code"] == 2
    assert status_data["stages"]["report_preparation"]["status"] == "interrupted"
    assert "verify interrupted; partial outputs in" in capsys.readouterr().err
