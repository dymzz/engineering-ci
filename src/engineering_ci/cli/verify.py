"""Measure an explicit Lefthook verify invocation and retain its native output."""

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from time import perf_counter, time_ns
from uuid import uuid4

from engineering_ci.verify_report import parse_lefthook_output, run_report_cli

_TIMING_BOUNDARIES = {
    "post_seal_io_excluded": True,
    "outer_launcher_startup_excluded": True,
    "note": (
        "Measured via time.perf_counter inside Python process; post-seal rename/cleanup "
        "and outer uv/shell startup are excluded."
    ),
}


def _lefthook_binary():
    names = ("lefthook.exe", "lefthook") if os.name == "nt" else ("lefthook",)
    # A virtual environment can link its Python launcher to the base interpreter.
    sibling_dir = Path(sys.executable).parent
    for name in names:
        candidate = sibling_dir / name
        if candidate.is_file():
            return str(candidate)
    resolved = shutil.which("lefthook")
    if resolved:
        return resolved
    raise FileNotFoundError("lefthook executable not found")


def _native_output(path):
    with path.open("rb") as stream:
        return {
            "path": path.name,
            "sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
        }


def _safe_native_outputs(stdout_path, stderr_path):
    outputs = {}
    for key, path in (("stdout", stdout_path), ("stderr", stderr_path)):
        if not path.is_file():
            continue
        try:
            outputs[key] = _native_output(path)
        except OSError:
            continue
    return outputs


def _write_json_atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(json.dumps(payload, indent=2) + "\n")
            handle.flush()
            with suppress(OSError):
                os.fsync(handle.fileno())
        tmp_path.replace(path)
    finally:
        if tmp_path is not None:
            with suppress(OSError):
                tmp_path.unlink(missing_ok=True)


def _write_status_best_effort(invocation, payload):
    path = invocation / "verify-status.json"
    text = json.dumps(payload, indent=2) + "\n"
    try:
        _write_json_atomic(path, payload)
    except OSError:
        # Atomic replace/cleanup can fail after a seal error. A direct write
        # still records the observed native exit instead of dropping it.
        try:
            path.write_text(text, encoding="utf-8", newline="\n")
        except OSError as exc:
            print(f"verify: status record unavailable: {exc}", file=sys.stderr)


def _status_payload(
    *,
    invocation_id,
    status,
    total_started_mono,
    stages,
    observed_git_head=None,
    observed_config_sha256=None,
    lefthook_version=None,
    native_started=False,
    native_exit_code=None,
    native_exit_source=None,
    outer_exit_code=None,
    started_at_ns=None,
    completed_at_ns=None,
    native_outputs=None,
    lefthook_summary=None,
    error=None,
    signal_name=None,
):
    payload = {
        "schema_version": 1,
        "invocation_id": invocation_id,
        "status": status,
        "observed_git_head": observed_git_head,
        "observed_lefthook_config_sha256": observed_config_sha256,
        "lefthook_version": lefthook_version,
        "native_started": native_started,
        "native_exit_code": native_exit_code,
        "started_at_ns": started_at_ns,
        "completed_at_ns": completed_at_ns,
        "time_source_verified": False,
        "timing_clock": "monotonic_perf_counter",
        "elapsed_seconds": perf_counter() - total_started_mono,
        "stages": stages,
        "timing_boundaries": _TIMING_BOUNDARIES,
    }
    if native_exit_source is not None:
        payload["native_exit_source"] = native_exit_source
    if outer_exit_code is not None:
        payload["outer_exit_code"] = outer_exit_code
    if native_outputs is not None:
        payload["native_outputs"] = native_outputs
    if lefthook_summary is not None:
        payload["lefthook_summary"] = lefthook_summary
    if error is not None:
        payload["error"] = error
    if signal_name is not None:
        payload["signal"] = signal_name
    return payload


def _verify(out):
    total_started_mono = perf_counter()
    invocation = out / f"verify-{uuid4().hex}"
    invocation.mkdir(parents=True)
    status_path = invocation / "verify-status.json"
    stdout_path = invocation / "lefthook.stdout"
    stderr_path = invocation / "lefthook.stderr"

    observed_git_head = None
    observed_config_sha256 = None
    lefthook_version = None
    parsed_verify_config = None
    preflight_stages = {}

    try:
        lefthook = _lefthook_binary()
        git_head = subprocess.run(
            ("git", "rev-parse", "--verify", "--quiet", "HEAD^{commit}"),
            stdout=subprocess.PIPE,
            check=False,
        )
        observed_git_head = (
            git_head.stdout.strip().decode("ascii")
            if git_head.returncode == 0
            else None
        )
        version = subprocess.check_output((lefthook, "version"))
        lefthook_version = version.decode("utf-8").strip()
        config = subprocess.check_output((lefthook, "dump", "--format", "json"))
        observed_config_sha256 = hashlib.sha256(config).hexdigest()
        try:
            parsed_config = json.loads(config)
        except ValueError as exc:
            raise ValueError(f"invalid lefthook dump JSON: {exc}") from None
        if not isinstance(parsed_config, dict) or not isinstance(
            parsed_config.get("verify"), dict
        ):
            raise TypeError("lefthook config has no 'verify' hook")
        parsed_verify_config = parsed_config["verify"]
    except KeyboardInterrupt:
        cli_preflight_seconds = perf_counter() - total_started_mono
        preflight_stages["cli_preflight"] = {
            "elapsed_seconds": cli_preflight_seconds,
            "status": "interrupted",
        }
        _write_status_best_effort(
            invocation,
            _status_payload(
                invocation_id=invocation.name,
                status="interrupted",
                total_started_mono=total_started_mono,
                stages=preflight_stages,
                observed_git_head=observed_git_head,
                observed_config_sha256=observed_config_sha256,
                lefthook_version=lefthook_version,
                native_started=False,
                signal_name="SIGINT_OR_SIGBREAK",
            ),
        )
        print(
            f"verify interrupted; partial outputs in {invocation}",
            file=sys.stderr,
        )
        return 130
    except Exception as exc:
        cli_preflight_seconds = perf_counter() - total_started_mono
        preflight_stages["cli_preflight"] = {
            "elapsed_seconds": cli_preflight_seconds,
            "status": "failed",
        }
        _write_status_best_effort(
            invocation,
            _status_payload(
                invocation_id=invocation.name,
                status="preflight_failed",
                total_started_mono=total_started_mono,
                stages=preflight_stages,
                observed_git_head=observed_git_head,
                observed_config_sha256=observed_config_sha256,
                lefthook_version=lefthook_version,
                native_started=False,
                error={"type": type(exc).__name__, "message": str(exc)},
            ),
        )
        raise

    cli_preflight_seconds = perf_counter() - total_started_mono
    print(f"verify: running lefthook verify (outputs: {invocation})", file=sys.stderr)
    _write_status_best_effort(
        invocation,
        _status_payload(
            invocation_id=invocation.name,
            status="preflight_ok",
            total_started_mono=total_started_mono,
            stages={
                "cli_preflight": {
                    "elapsed_seconds": cli_preflight_seconds,
                    "status": "ok",
                }
            },
            observed_git_head=observed_git_head,
            observed_config_sha256=observed_config_sha256,
            lefthook_version=lefthook_version,
            native_started=False,
        ),
    )

    sigbreak = getattr(signal, "SIGBREAK", None)
    previous_sigbreak = signal.getsignal(sigbreak) if sigbreak is not None else None
    if sigbreak is not None:
        signal.signal(sigbreak, signal.default_int_handler)

    native_started_mono = perf_counter()
    started_at_ns = None
    opened_streams = False
    spawn_attempted = False
    proc = None
    returncode = None
    observed_interrupt_exit = None
    observed_interrupt_source = None

    def _reap_child(child) -> tuple[int | None, str]:
        observed = None
        with suppress(OSError, subprocess.SubprocessError):
            observed = child.poll()
        if observed is not None:
            return observed, "observed_before_terminate"
        with suppress(OSError, subprocess.SubprocessError):
            child.terminate()
            child.wait(timeout=2)
        with suppress(OSError, subprocess.SubprocessError):
            if child.poll() is None:
                child.kill()
                child.wait(timeout=2)
        with suppress(OSError, subprocess.SubprocessError):
            observed = child.poll()
        if observed is not None:
            return observed, "observed_after_terminate"
        return None, "unobserved_after_terminate"

    try:
        try:
            stdout_cm = stdout_path.open("xb")
            try:
                stderr_cm = stderr_path.open("xb")
            except BaseException:
                with suppress(OSError):
                    stdout_cm.close()
                raise
        except OSError as exc:
            _write_status_best_effort(
                invocation,
                _status_payload(
                    invocation_id=invocation.name,
                    status="output_open_failed",
                    total_started_mono=total_started_mono,
                    stages={
                        "cli_preflight": {
                            "elapsed_seconds": cli_preflight_seconds,
                            "status": "ok",
                        },
                        "lefthook_native": {
                            "elapsed_seconds": 0.0,
                            "status": "output_open_failed",
                        },
                    },
                    observed_git_head=observed_git_head,
                    observed_config_sha256=observed_config_sha256,
                    lefthook_version=lefthook_version,
                    native_started=False,
                    native_exit_code=None,
                    error={"type": type(exc).__name__, "message": str(exc)},
                ),
            )
            raise

        opened_streams = True
        with stdout_cm as stdout, stderr_cm as stderr:
            native_started_mono = perf_counter()
            started_at_ns = time_ns()
            spawn_attempted = True
            cmd = (lefthook, "run", "verify", "--no-auto-install")
            try:
                proc = subprocess.Popen(cmd, stdout=stdout, stderr=stderr)
            except (OSError, subprocess.SubprocessError) as exc:
                _write_status_best_effort(
                    invocation,
                    _status_payload(
                        invocation_id=invocation.name,
                        status="spawn_failed",
                        total_started_mono=total_started_mono,
                        stages={
                            "cli_preflight": {
                                "elapsed_seconds": cli_preflight_seconds,
                                "status": "ok",
                            },
                            "lefthook_native": {
                                "elapsed_seconds": perf_counter() - native_started_mono,
                                "status": "spawn_failed",
                            },
                        },
                        observed_git_head=observed_git_head,
                        observed_config_sha256=observed_config_sha256,
                        lefthook_version=lefthook_version,
                        native_started=False,
                        native_exit_code=None,
                        started_at_ns=None,
                        completed_at_ns=None,
                        error={"type": type(exc).__name__, "message": str(exc)},
                    ),
                )
                raise

            try:
                returncode = proc.wait()
            except (OSError, subprocess.SubprocessError) as exc:
                observed_exit, observed_exit_source = _reap_child(proc)
                _write_status_best_effort(
                    invocation,
                    _status_payload(
                        invocation_id=invocation.name,
                        status="wait_failed",
                        total_started_mono=total_started_mono,
                        stages={
                            "cli_preflight": {
                                "elapsed_seconds": cli_preflight_seconds,
                                "status": "ok",
                            },
                            "lefthook_native": {
                                "elapsed_seconds": perf_counter() - native_started_mono,
                                "status": "wait_failed",
                            },
                        },
                        observed_git_head=observed_git_head,
                        observed_config_sha256=observed_config_sha256,
                        lefthook_version=lefthook_version,
                        native_started=True,
                        native_exit_code=observed_exit,
                        native_exit_source=observed_exit_source,
                        started_at_ns=started_at_ns,
                        completed_at_ns=None,
                        native_outputs=_safe_native_outputs(stdout_path, stderr_path),
                        lefthook_summary=parse_lefthook_output(
                            stdout_path,
                            stderr_path,
                            verify_config=parsed_verify_config,
                            native_exit_code=observed_exit,
                        ),
                        error={"type": type(exc).__name__, "message": str(exc)},
                    ),
                )
                raise
            except KeyboardInterrupt:
                observed_interrupt_exit, observed_interrupt_source = _reap_child(proc)
                raise

            result = subprocess.CompletedProcess(cmd, returncode)
            ended_at_ns = time_ns()
            native_elapsed_seconds = perf_counter() - native_started_mono
    except KeyboardInterrupt:
        native_elapsed_seconds = perf_counter() - native_started_mono
        report_started_mono = perf_counter()
        native_outputs = (
            _safe_native_outputs(stdout_path, stderr_path) if opened_streams else None
        )
        observed_output = False
        if opened_streams:
            for stream_file in (stdout_path, stderr_path):
                with suppress(OSError):
                    if stream_file.is_file() and stream_file.stat().st_size > 0:
                        observed_output = True
                        break
        if not opened_streams or not spawn_attempted:
            native_started: bool | str = False
            effective_started_ns = None
            native_stage_status = "not_started"
            interrupt_exit_code = None
            interrupt_exit_source = "not_started"
        elif returncode is not None:
            native_started = True
            effective_started_ns = started_at_ns
            native_stage_status = "interrupted"
            interrupt_exit_code = returncode
            interrupt_exit_source = "observed_before_report_preparation"
        elif proc is not None:
            native_started = True
            effective_started_ns = started_at_ns
            native_stage_status = "interrupted"
            if observed_interrupt_source is None:
                observed_interrupt_exit, observed_interrupt_source = _reap_child(proc)
            interrupt_exit_code = observed_interrupt_exit
            interrupt_exit_source = observed_interrupt_source
        elif observed_output:
            native_started = True
            effective_started_ns = started_at_ns
            native_stage_status = "interrupted"
            interrupt_exit_code = None
            interrupt_exit_source = "unobserved_process_handle"
        else:
            # Interrupted during Popen before process handle or child output exists.
            native_started = "unknown"
            effective_started_ns = None
            native_stage_status = "unknown"
            interrupt_exit_code = None
            interrupt_exit_source = "unknown_spawn"
        lefthook_summary = (
            parse_lefthook_output(
                stdout_path,
                stderr_path,
                verify_config=parsed_verify_config,
                native_exit_code=interrupt_exit_code,
            )
            if opened_streams
            else None
        )
        report_prep_seconds = perf_counter() - report_started_mono
        _write_status_best_effort(
            invocation,
            _status_payload(
                invocation_id=invocation.name,
                status="interrupted",
                total_started_mono=total_started_mono,
                stages={
                    "cli_preflight": {
                        "elapsed_seconds": cli_preflight_seconds,
                        "status": "ok",
                    },
                    "lefthook_native": {
                        "elapsed_seconds": native_elapsed_seconds,
                        "status": native_stage_status,
                    },
                    "report_preparation": {
                        "elapsed_seconds": report_prep_seconds,
                        "status": "status_only",
                    },
                },
                observed_git_head=observed_git_head,
                observed_config_sha256=observed_config_sha256,
                lefthook_version=lefthook_version,
                native_started=native_started,
                native_exit_code=interrupt_exit_code,
                native_exit_source=interrupt_exit_source,
                outer_exit_code=130,
                started_at_ns=effective_started_ns,
                completed_at_ns=None,
                native_outputs=native_outputs,
                lefthook_summary=lefthook_summary,
                signal_name="SIGINT_OR_SIGBREAK",
            ),
        )
        print(
            f"verify interrupted; partial outputs in {invocation}",
            file=sys.stderr,
        )
        return 130
    finally:
        if sigbreak is not None:
            signal.signal(sigbreak, previous_sigbreak)

    report_started_mono = perf_counter()
    native_exit = result.returncode
    exit_code = native_exit if native_exit >= 0 else 128 - native_exit
    wall_clock_monotonic = ended_at_ns >= started_at_ns
    stages: dict[str, dict] = {
        "cli_preflight": {"elapsed_seconds": cli_preflight_seconds, "status": "ok"},
        "lefthook_native": {
            "elapsed_seconds": native_elapsed_seconds,
            "status": "passed" if native_exit == 0 else "failed",
        },
    }
    native_outputs = None
    lefthook_summary = None
    try:
        native_outputs = {
            "stdout": _native_output(stdout_path),
            "stderr": _native_output(stderr_path),
        }
        lefthook_summary = parse_lefthook_output(
            stdout_path,
            stderr_path,
            verify_config=parsed_verify_config,
            native_exit_code=native_exit,
        )
        report_prep_seconds = perf_counter() - report_started_mono
        stages["report_preparation"] = {
            "elapsed_seconds": report_prep_seconds,
            "status": "ok",
        }
        receipt = {
            "schema_version": 2,
            "invocation_id": invocation.name,
            "report_status": "sealed",
            "observed_git_head": observed_git_head,
            "observed_lefthook_config_sha256": observed_config_sha256,
            "lefthook_version": lefthook_version,
            "started_at_ns": started_at_ns,
            "completed_at_ns": ended_at_ns if wall_clock_monotonic else None,
            "time_source_verified": False,
            "timing_clock": "monotonic_perf_counter",
            "elapsed_seconds": perf_counter() - total_started_mono,
            "stages": stages,
            "timing_boundaries": _TIMING_BOUNDARIES,
            "clock_diagnostics": {
                "wall_clock_regression_observed": not wall_clock_monotonic,
                "utc_elapsed_ns": (
                    ended_at_ns - started_at_ns if wall_clock_monotonic else None
                ),
            },
            "native_exit_code": native_exit,
            "native_outputs": native_outputs,
            "lefthook_summary": lefthook_summary,
        }
        receipt_path = invocation / "verify-timing.json"
        _write_json_atomic(receipt_path, receipt)
    except KeyboardInterrupt:
        stages["report_preparation"] = {
            "elapsed_seconds": perf_counter() - report_started_mono,
            "status": "interrupted",
        }
        if native_outputs is None:
            native_outputs = _safe_native_outputs(stdout_path, stderr_path)
        _write_status_best_effort(
            invocation,
            _status_payload(
                invocation_id=invocation.name,
                status="report_preparation_interrupted",
                total_started_mono=total_started_mono,
                stages=stages,
                observed_git_head=observed_git_head,
                observed_config_sha256=observed_config_sha256,
                lefthook_version=lefthook_version,
                native_started=True,
                native_exit_code=native_exit,
                native_exit_source="observed_before_report_preparation",
                outer_exit_code=130,
                started_at_ns=started_at_ns,
                completed_at_ns=ended_at_ns if wall_clock_monotonic else None,
                native_outputs=native_outputs,
                lefthook_summary=lefthook_summary,
                signal_name="SIGINT_OR_SIGBREAK",
            ),
        )
        print(
            f"verify interrupted; partial outputs in {invocation}",
            file=sys.stderr,
        )
        return 130
    except (OSError, ValueError, TypeError) as exc:
        report_prep_seconds = perf_counter() - report_started_mono
        stages["report_preparation"] = {
            "elapsed_seconds": report_prep_seconds,
            "status": "failed",
        }
        if native_outputs is None:
            native_outputs = _safe_native_outputs(stdout_path, stderr_path)
        _write_status_best_effort(
            invocation,
            _status_payload(
                invocation_id=invocation.name,
                status="seal_failed",
                total_started_mono=total_started_mono,
                stages=stages,
                observed_git_head=observed_git_head,
                observed_config_sha256=observed_config_sha256,
                lefthook_version=lefthook_version,
                native_started=True,
                native_exit_code=native_exit,
                native_exit_source="natural_exit",
                outer_exit_code=exit_code or 1,
                started_at_ns=started_at_ns,
                completed_at_ns=ended_at_ns if wall_clock_monotonic else None,
                native_outputs=native_outputs,
                lefthook_summary=lefthook_summary,
                error={"type": type(exc).__name__, "message": str(exc)},
            ),
        )
        print(
            f"verify exited {native_exit}; timing receipt unavailable: {exc}",
            file=sys.stderr,
        )
        return exit_code or 1

    with suppress(OSError):
        status_path.unlink(missing_ok=True)
    print(receipt_path)
    return exit_code


def main(argv=None):
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list[:1] == ["report"]:
        return run_report_cli(args_list[1:])
    if args_list[:1] == ["run"]:
        args_list = args_list[1:]

    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Subcommands:\n"
            "  run       Execute lefthook verify directly (default when passing --out)\n"
            "  report    Read-only correlation across Verify, Hooks, pytest, Dagger, and CI timings"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="Directory for verification artifacts"
    )
    args = parser.parse_args(args_list)
    try:
        return _verify(args.out)
    except (OSError, TypeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"verify: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("verify interrupted", file=sys.stderr)
        return 130
