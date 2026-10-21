"""Native gh API and Release Asset calls with safe HTTP diagnostics."""

import json
import re
import subprocess
import sys


class ApiError(RuntimeError):
    def __init__(self, status=None, *, exit_code=1):
        self.status = status
        self.exit_code = exit_code if exit_code >= 0 else 128 - exit_code
        super().__init__(f"GitHub request failed (HTTP {status or 'unavailable'})")


def require_api_success(result):
    if result.returncode:
        stderr = result.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        match = re.search(r"HTTP (\d{3})", stderr)
        # Never print or persist raw HTTP responses or command stderr.
        raise ApiError(int(match[1]) if match else None, exit_code=result.returncode)


class GitHub:
    def __init__(self, repository):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Invalid repository")
        self.prefix = f"repos/{repository}/"
        self.repository = repository

    def __call__(self, path, method="GET", data=None):
        args = ["gh", "api", "--method", method, self.prefix + path]
        if data is not None:
            args += ["--input", "-"]
        result = subprocess.run(
            args,
            input=json.dumps(data) if data is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=60,
        )
        require_api_success(result)
        if len(result.stdout) > 1024 * 1024:
            raise ValueError("GitHub metadata exceeds limit")
        if method == "DELETE" and not result.stdout.strip():
            return None
        value = json.loads(result.stdout)
        if not isinstance(value, (dict, list)):
            raise TypeError("GitHub metadata is not an object or array")
        return value

    def upload(self, tag, path):
        result = subprocess.run(
            ["gh", "release", "upload", tag, str(path), "--repo", self.repository],
            capture_output=True,
            check=False,
            timeout=120,
        )
        require_api_success(result)

    def download(self, asset_id):
        result = subprocess.run(
            [
                "gh",
                "api",
                self.prefix + f"releases/assets/{asset_id}",
                "-H",
                "Accept: application/octet-stream",
            ],
            capture_output=True,
            check=False,
            timeout=60,
        )
        require_api_success(result)
        if len(result.stdout) > 1024 * 1024:
            raise ValueError("Manifest asset exceeds limit")
        return json.loads(result.stdout)

    def artifact_archive(self, artifact_id):
        result = subprocess.run(
            ["gh", "api", self.prefix + f"actions/artifacts/{artifact_id}/zip"],
            capture_output=True,
            check=False,
            timeout=60,
        )
        require_api_success(result)
        if not 0 < len(result.stdout) <= 2 * 1024 * 1024:
            raise ValueError("Validation Manifest archive exceeds limit or is empty")
        return result.stdout

    def run_info(self, run_id):
        """Read one exact Run; an API read works without gh watch's checks scope."""
        value = self(f"actions/runs/{run_id}")
        if not isinstance(value, dict):
            raise ValueError("Run metadata is not an object")  # noqa: TRY004 - public metadata validation
        remote = value.get("repository")
        repo = remote.get("full_name") if isinstance(remote, dict) else None
        if (
            type(value.get("id")) is not int
            or value["id"] != run_id
            or not isinstance(repo, str)
            or repo.lower() != self.repository.lower()
            or type(value.get("run_attempt")) is not int
            or value["run_attempt"] < 1
            or not isinstance(value.get("head_sha"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", value["head_sha"])
        ):
            raise ValueError("Run identity does not match repository/ID/SHA/attempt")
        return value

    def watch(self, run_id, *, timeout=2700):
        result = subprocess.run(
            [
                "gh",
                "run",
                "watch",
                str(run_id),
                "--repo",
                self.repository,
                "--exit-status",
            ],
            check=False,
            timeout=timeout,
            stdout=sys.stderr,
            stderr=sys.stderr,
        )
        return result.returncode if result.returncode >= 0 else 128 - result.returncode

    def run_download(self, run_id, directory, names):
        args = [
            "gh",
            "run",
            "download",
            str(run_id),
            "--repo",
            self.repository,
            "--dir",
            str(directory),
        ]
        for name in names:
            args += ["--name", name]
        result = subprocess.run(args, capture_output=True, check=False, timeout=1800)
        require_api_success(result)

    def run_log(self, run_id, attempt, stream):
        result = subprocess.run(
            [
                "gh",
                "run",
                "view",
                str(run_id),
                "--repo",
                self.repository,
                "--attempt",
                str(attempt),
                "--log",
            ],
            stdout=stream,
            stderr=subprocess.PIPE,
            check=False,
            timeout=1800,
        )
        require_api_success(result)


def repository(raw=None):
    """Use native gh origin resolution; do not maintain a repository cache."""
    if raw is None:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=60,
        )
        require_api_success(result)
        raw = json.loads(result.stdout)["nameWithOwner"]
    GitHub(raw)  # validate before any command or local path construction
    return raw


def positive_id(raw):
    if not raw.isascii() or not raw.isdecimal() or int(raw) <= 0:
        import argparse

        raise argparse.ArgumentTypeError("Expected a positive integer")
    return int(raw)
