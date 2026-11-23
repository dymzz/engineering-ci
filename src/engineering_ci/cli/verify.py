"""Measure an explicit Lefthook verify invocation and retain its native output."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from time import time_ns
from uuid import uuid4


def _native_output(path):
    with path.open("rb") as stream:
        return {
            "path": path.name,
            "sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
        }


def _verify(out):
    git_head = subprocess.run(
        ("git", "rev-parse", "--verify", "--quiet", "HEAD^{commit}"),
        stdout=subprocess.PIPE,
        check=False,
    )
    version = subprocess.check_output(("lefthook", "version"))
    config = subprocess.check_output(("lefthook", "dump", "--format", "json"))

    invocation = out / f"verify-{uuid4().hex}"
    invocation.mkdir(parents=True)
    stdout_path = invocation / "lefthook.stdout"
    stderr_path = invocation / "lefthook.stderr"
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        started_at_ns = time_ns()
        result = subprocess.run(
            ("lefthook", "run", "verify", "--no-auto-install"),
            stdout=stdout,
            stderr=stderr,
            check=False,
        )
        ended_at_ns = time_ns()

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
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"verify: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("verify interrupted", file=sys.stderr)
        return 130
