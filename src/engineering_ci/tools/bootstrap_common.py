"""Process failures and path ownership shared by the bootstrap entry and its flow."""

import json
import os
import re
import subprocess

from ..github_cli import ApiError, require_api_success


class BootstrapError(Exception):
    def __init__(self, reason, code=3, *, step=None, http_status=None, observed=None):
        super().__init__(reason)
        self.code, self.step = code, step
        self.http_status = http_status
        self.observed = observed


def owned(root, relative):
    """Reject redirected files before accessing the owned project."""
    path = root / relative
    if not path.resolve().is_relative_to(root) or any(
        p.is_symlink()
        for p in [path, *path.parents]
        if p != root and p.is_relative_to(root)
    ):
        raise BootstrapError("Scaffold path is a symlink or escapes the destination")
    return path


class Commands:
    def __init__(self, run=subprocess.run):
        self.run, self.step = run, "preflight"

    def execute(
        self, args, cwd, *, allowed=(0,), stream=False, timeout=120, input=None
    ):
        """Keep JSON stdout clean; native setup/check output goes to terminal stderr."""
        environment = {}
        if args[0] == "dagger":
            # UPSTREAM: Dagger@1.0.0-beta.16 - see .agent/upstream-observations.md
            environment["env"] = {
                **os.environ,
                "FORCE_DARK_MODE": os.environ.get("FORCE_DARK_MODE") or "1",
            }
        try:
            result = self.run(
                args,
                cwd=cwd,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=timeout,
                input=input,
                **environment,
                **({"stdout": 2, "stderr": 2} if stream else {"capture_output": True}),
            )
        except FileNotFoundError:
            raise BootstrapError(
                f"Required tool is missing: {args[0]}", 2, step=self.step
            ) from None
        except (OSError, subprocess.TimeoutExpired):
            raise BootstrapError(
                f"{args[0]} is unavailable or timed out", step=self.step
            ) from None
        if result.returncode not in allowed:
            status = re.search(r"HTTP (\d{3})", result.stderr or "")
            suffix = f"; HTTP {status[1]}" if status else ""
            raise BootstrapError(
                f"{args[0]} failed (exit {result.returncode}{suffix}); raw responses are not saved",
                result.returncode if result.returncode > 0 else 128 - result.returncode,
                step=self.step,
                http_status=int(status[1]) if status else None,
            )
        return result

    def git(self, root, *args, allowed=(0,)):
        return self.execute(["git", *args], root, allowed=allowed).stdout.strip()

    def api(self, path, *, missing=False):
        result = self.execute(["gh", "api", path], None, allowed=(0, 1))
        try:
            require_api_success(result)
        except ApiError as error:
            if missing and error.status == 404:
                return None
            raise BootstrapError(
                str(error),
                result.returncode or 3,
                step=self.step,
                http_status=error.status,
            ) from None
        if len(result.stdout) > 1024 * 1024:
            raise BootstrapError("GitHub metadata exceeds limit", step=self.step)
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise BootstrapError(
                "GitHub did not return repository metadata", step=self.step
            )
        return value
