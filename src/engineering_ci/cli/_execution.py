"""Lightweight CLI options and the bridge to the execution application."""

import argparse
import sys


def add_wait_arguments(parser: argparse.ArgumentParser, *, include_state=True) -> None:
    parser.add_argument("--interval", type=float, default=10)
    parser.add_argument("--timeout", type=int, default=2700)
    if include_state:
        parser.add_argument("--show-state", action="store_true")


def add_runtime_arguments(p: argparse.ArgumentParser) -> None:
    """Download options shared by the existing Run and legacy Dispatch CLIs."""
    add_wait_arguments(p)
    p.add_argument("--refresh-repo", action="store_true")
    p.add_argument(
        "--artifact", help="Artifact name; {run_id} is expanded after dispatch"
    )
    p.add_argument("--out", default="workflow-artifacts")
    p.add_argument("--save-log", action="store_true")
    p.add_argument("--no-artifacts", action="store_true")
    p.add_argument(
        "--retry-attempts",
        type=int,
        default=1,
        help="Compatibility option; use --resume after a failed attempt",
    )
    p.add_argument(
        "--retry-delay",
        type=float,
        default=2,
        help="Compatibility option; no automatic short retry loop",
    )
    p.add_argument(
        "--download-backend",
        choices=["auto", "aria2", "native"],
        help="Download backend (default: auto; aria2 if available, otherwise native HTTP)",
    )
    p.add_argument(
        "--connections",
        type=int,
        help="aria2 connections per file (default: 4, range: 1..16)",
    )
    p.add_argument(
        "--download-timeout",
        type=int,
        help="Download read/inactivity timeout in seconds (default: 30)",
    )
    p.add_argument(
        "--connect-timeout",
        type=int,
        help="Connection timeout in seconds (default: 15)",
    )
    p.add_argument(
        "--download-process-timeout",
        type=int,
        help="Download time limit in seconds (default: 1800)",
    )
    p.add_argument(
        "--download-recovery",
        choices=["restart", "pause"],
        help="Restart owned partial data if Range/native progress is unavailable, or pause",
    )
    p.add_argument(
        "--aria2c",
        help="aria2 executable name or path (default: aria2c; overrides configuration)",
    )
    p.add_argument("--max-extract-bytes", type=int, default=8 * 1024**3)
    p.add_argument("--max-extract-files", type=int, default=100000)


def dependency_error(error: ModuleNotFoundError) -> int:
    if error.name not in {"transitions", "filelock"}:
        raise error
    print(
        f"Missing project dependency: {error.name}; Python: {sys.executable}. "
        "Run this entry with uv run --locked python, or use the project's .venv Python.",
        file=sys.stderr,
    )
    return 2


def invoke_execution(args, *, collect_outputs=True, restart_finished=False):
    try:
        from ..execution.cli import entrypoint

        return entrypoint(
            args, collect_outputs=collect_outputs, restart_finished=restart_finished
        )
    except ModuleNotFoundError as error:
        return dependency_error(error)


def execution_environment():
    try:
        from ..execution import cli

        return 0 if cli else 2
    except ModuleNotFoundError as error:
        return dependency_error(error)


def runtime_arguments(options):
    args = []
    for name in ("refresh_repo", "save_log", "no_artifacts", "show_state"):
        if getattr(options, name, False):
            args.append("--" + name.replace("_", "-"))
    for name in (
        "artifact",
        "out",
        "interval",
        "timeout",
        "retry_attempts",
        "retry_delay",
        "download_backend",
        "connections",
        "download_timeout",
        "connect_timeout",
        "download_process_timeout",
        "download_recovery",
        "aria2c",
        "max_extract_bytes",
        "max_extract_files",
    ):
        value = getattr(options, name, None)
        if value is not None:
            args += ["--" + name.replace("_", "-"), str(value)]
    return args


__all__ = ["add_runtime_arguments", "add_wait_arguments", "runtime_arguments"]
