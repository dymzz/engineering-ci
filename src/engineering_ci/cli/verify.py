"""Measure an explicit Lefthook verify invocation and retain its native output."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from time import time_ns
from uuid import uuid4


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


def _verify(out):
    lefthook = _lefthook_binary()
    git_head = subprocess.run(
        ("git", "rev-parse", "--verify", "--quiet", "HEAD^{commit}"),
        stdout=subprocess.PIPE,
        check=False,
    )
    version = subprocess.check_output((lefthook, "version"))
    config = subprocess.check_output((lefthook, "dump", "--format", "json"))
    try:
        parsed_config = json.loads(config)
    except ValueError as exc:
        raise ValueError(f"invalid lefthook dump JSON: {exc}") from None
    if not isinstance(parsed_config, dict) or not isinstance(
        parsed_config.get("verify"), dict
    ):
        raise TypeError("lefthook config has no 'verify' hook")

    invocation = out / f"verify-{uuid4().hex}"
    invocation.mkdir(parents=True)
    stdout_path = invocation / "lefthook.stdout"
    stderr_path = invocation / "lefthook.stderr"
    print(f"verify: running lefthook verify (outputs: {invocation})", file=sys.stderr)
    try:
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            started_at_ns = time_ns()
            result = subprocess.run(
                (lefthook, "run", "verify", "--no-auto-install"),
                stdout=stdout,
                stderr=stderr,
                check=False,
            )
            ended_at_ns = time_ns()
    except KeyboardInterrupt:
        print(
            f"verify interrupted; partial outputs in {invocation}",
            file=sys.stderr,
        )
        return 130

    native_exit = result.returncode
    exit_code = native_exit if native_exit >= 0 else 128 - native_exit
    try:
        receipt = {
            "invocation_id": invocation.name,
            "observed_git_head": git_head.stdout.strip().decode("ascii")
            if git_head.returncode == 0
            else None,
            "observed_lefthook_config_sha256": hashlib.sha256(config).hexdigest(),
            "lefthook_version": version.decode("utf-8").strip(),
            "started_at_ns": started_at_ns,
            "completed_at_ns": ended_at_ns if ended_at_ns >= started_at_ns else None,
            "time_source_verified": False,
            "native_exit_code": native_exit,
            "native_outputs": {
                "stdout": _native_output(stdout_path),
                "stderr": _native_output(stderr_path),
            },
        }
        receipt_path = invocation / "verify-timing.json"
        temp_path = receipt_path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        temp_path.replace(receipt_path)
    except OSError as exc:
        print(
            f"verify exited {native_exit}; timing receipt unavailable: {exc}",
            file=sys.stderr,
        )
        return exit_code or 1

    print(receipt_path)
    return exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, required=True, help="Directory for verification artifacts"
    )
    args = parser.parse_args(argv)
    try:
        return _verify(args.out)
    except (OSError, TypeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"verify: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("verify interrupted", file=sys.stderr)
        return 130
