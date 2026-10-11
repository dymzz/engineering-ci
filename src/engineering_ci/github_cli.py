"""Native gh API and Release Asset calls with safe HTTP diagnostics."""

import json
import re
import subprocess


class ApiError(RuntimeError):
    def __init__(self, status=None):
        self.status = status
        super().__init__(f"GitHub request failed (HTTP {status or 'unavailable'})")


def require_api_success(result):
    if result.returncode:
        stderr = result.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        match = re.search(r"HTTP (\d{3})", stderr)
        # Never print or persist raw HTTP responses or command stderr.
        raise ApiError(int(match[1]) if match else None)


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
