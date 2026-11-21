"""Temporary native Git checkouts for executing against verified source."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path


class GitSourceError(ValueError):
    """Git could not prepare or verify the requested source."""


def _git(root, *args, env=None, data=None):
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        env=env,
        input=data,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitSourceError(f"git {args[0]} ({result.returncode}): {diagnostic}")
    return result.stdout


def _verify_files(worktree, records, object_format):
    seen = set()
    for record in records.split(b"\0"):
        if not record:
            continue
        fields, raw_path = record.split(b"\t", 1)
        mode, kind, oid = fields.split(b" ")
        relative = Path(os.fsdecode(raw_path))
        path = worktree / relative
        if relative.is_absolute() or not path.resolve().is_relative_to(worktree):
            raise GitSourceError("checkout_member_outside_source")
        key = os.path.normcase(relative)
        if key in seen:
            raise GitSourceError("checkout_member_path_collision")
        seen.add(key)
        if kind != b"blob":
            raise GitSourceError("checkout_gitlink_requires_source")
        if mode == b"120000" and path.is_symlink():
            content = os.fsencode(os.readlink(path))
        elif mode in (b"100644", b"100755") and not path.is_symlink():
            if not path.is_file():
                raise GitSourceError("checkout_member_unavailable")
            content = path.read_bytes()
        else:
            raise GitSourceError("checkout_member_mode_mismatch")
        header = f"blob {len(content)}\0".encode("ascii")
        digest = hashlib.new(object_format, header + content, usedforsecurity=False)
        if digest.hexdigest().encode("ascii") != oid:
            raise GitSourceError("checkout_member_content_mismatch")


@contextmanager
def materialized_tree(root, tree, *, head=None):
    """Yield a byte-verified Git checkout and its isolated subprocess environment."""
    root = Path(root).resolve()
    object_format = _git(root, "rev-parse", "--show-object-format").decode().strip()
    if object_format not in {"sha1", "sha256"}:
        raise GitSourceError("checkout_object_format_unsupported")
    object_dir = os.fsdecode(
        _git(root, "rev-parse", "--path-format=absolute", "--git-path", "objects")
    ).rstrip("\r\n")
    if not Path(object_dir).is_dir() or any(char in object_dir for char in "\r\n"):
        raise GitSourceError("checkout_object_directory_invalid")
    local_variables = _git(root, "rev-parse", "--local-env-vars").decode().splitlines()
    env = {
        key: value for key, value in os.environ.items() if key not in local_variables
    }
    with tempfile.TemporaryDirectory(prefix="eci-tree-") as directory:
        temporary = Path(directory).resolve()
        config = temporary / "gitconfig"
        config.touch()
        env.update(
            GIT_CONFIG_GLOBAL=str(config),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_ATTR_NOSYSTEM="1",
            PYTHONDONTWRITEBYTECODE="1",
        )
        worktree = temporary / "source"
        worktree.mkdir()
        _git(
            worktree,
            "init",
            "--quiet",
            "--template=",
            "--initial-branch=source",
            f"--object-format={object_format}",
            env=env,
        )
        alternates = worktree / ".git" / "objects" / "info" / "alternates"
        alternates.write_bytes(os.fsencode(Path(object_dir).as_posix()) + b"\n")
        if head is not None:
            _git(worktree, "update-ref", "--no-deref", "HEAD", head, env=env)
        else:
            branch = subprocess.run(
                ["git", "symbolic-ref", "--quiet", "HEAD"],
                cwd=root,
                env=env,
                capture_output=True,
                check=False,
            )
            ref = branch.stdout.decode("utf-8", errors="replace").strip()
            if branch.returncode == 0 and ref.startswith("refs/heads/"):
                _git(worktree, "symbolic-ref", "HEAD", ref, env=env)
        empty = _git(worktree, "mktree", env=env, data=b"").decode().strip()
        _git(worktree, "read-tree", tree, env=env)
        _git(
            worktree,
            f"--attr-source={empty}",
            "checkout-index",
            "--all",
            "--index",
            env=env,
        )
        actual = _git(worktree, "write-tree", env=env).decode().strip()
        if actual != tree:
            raise GitSourceError("checkout_index_tree_mismatch")
        records = _git(worktree, "ls-tree", "-r", "-z", tree, env=env)
        _verify_files(worktree, records, object_format)
        yield worktree, env
