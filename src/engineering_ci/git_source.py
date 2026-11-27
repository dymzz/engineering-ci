"""Temporary native Git checkouts for executing against verified source."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path


class GitSourceError(ValueError):
    """Git could not prepare or verify the requested source."""


_OBJECT_FORMATS = {40: "sha1", 64: "sha256"}


def _run(root, *args, env=None, data=None):
    return subprocess.run(
        ["git", *args],
        cwd=root,
        env=env,
        input=data,
        capture_output=True,
        check=False,
    )


def _git(root, *args, env=None, data=None):
    result = _run(root, *args, env=env, data=data)
    if result.returncode:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitSourceError(f"git {args[0]} ({result.returncode}): {diagnostic}")
    return result.stdout


def _clean_git_env():
    return {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }


def _git_common_dir(root, *, env):
    top, common = os.fsdecode(
        _git(
            root,
            "rev-parse",
            "--path-format=absolute",
            "--show-toplevel",
            "--git-common-dir",
            env=env,
        )
    ).splitlines()
    return Path(common).resolve() if Path(top).resolve() == root else None


def enclosing_git_checkout(path: Path) -> Path | None:
    """Return the nearest enclosing directory containing ``.git``, if any."""
    resolved = Path(path).resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def shared_git_common_dir(checkout, candidate):
    """Prove that two checkout roots share one Git common directory.

    This is strictly weaker than :func:`registered_worktree` (for example under
    ``--separate-git-dir`` without ``core.worktree``). Callers may use it for
    non-authorizing local diagnostics and environment attribution. It must not
    replace :func:`registered_worktree` for Dagger module admission.
    """
    env = _clean_git_env()
    try:
        checkout_root = Path(checkout).resolve()
        candidate_root = Path(candidate).resolve()
        common = _git_common_dir(checkout_root, env=env)
        return common is not None and _git_common_dir(candidate_root, env=env) == common
    except (OSError, RuntimeError, ValueError):
        return False


def registered_worktree(checkout, candidate):
    """Prove that both checkout roots belong to one Git worktree registration."""
    env = _clean_git_env()
    try:
        common = _git_common_dir(checkout, env=env)
        if common is None or _git_common_dir(candidate, env=env) != common:
            return False
        worktrees = {
            Path(os.fsdecode(field.removeprefix(b"worktree "))).resolve()
            for field in _git(
                checkout, "worktree", "list", "--porcelain", "-z", env=env
            ).split(b"\0")
            if field.startswith(b"worktree ")
        }
        return checkout in worktrees and candidate in worktrees
    except (OSError, RuntimeError, ValueError):
        return False


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
def raw_checkout(root, tree, *, head=None):
    """Check out Tree bytes with no checkout filters or ambient Git policy."""
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
        attributes = temporary / "gitattributes"
        attributes.touch()
        config.write_text(
            f"[core]\n\tattributesFile = {attributes.as_posix()}\n",
            encoding="utf-8",
        )
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
        yield worktree, env, object_format


@contextmanager
def materialized_tree(root, tree, *, head=None):
    """Yield a byte-verified checkout and its isolated subprocess environment.

    Native checks can read the checkout's tracked attributes using clean Git
    defaults. Checkout itself always uses the original Git blob bytes.
    """
    with raw_checkout(root, tree, head=head) as (worktree, env, object_format):
        records = _git(worktree, "ls-tree", "-r", "-z", tree, env=env)
        _verify_files(worktree, records, object_format)
        yield worktree, env


def add_git_context(source, destination, git):
    """Supply the exact Index/Ref and comparison objects without host Git metadata."""
    tree = git["execution_tree"]
    roots = {tree}
    if git.get("base_tree"):
        roots.add(git["base_tree"])
    objects = {git[key] for key in ("base_commit", "head_commit") if git.get(key)}
    for commit in objects:
        roots.add(
            _git(source, "rev-parse", "--verify", f"{commit}^{{tree}}").decode().strip()
        )
    objects.update(roots)
    for root in roots:
        records = _git(source, "ls-tree", "-r", "-t", "-z", root)
        for record in records.split(b"\0"):
            if record:
                _, kind, oid = record.partition(b"\t")[0].split(b" ")
                if kind != b"commit":
                    objects.add(oid.decode("ascii"))
    object_format = _git(source, "rev-parse", "--show-object-format").decode().strip()
    _git(
        destination,
        "init",
        "--quiet",
        "--template=",
        "--initial-branch=source",
        f"--object-format={object_format}",
    )
    membership = ("\n".join(sorted(objects)) + "\n").encode("ascii")
    pack = destination / ".git" / "objects" / "pack" / "pack"
    _git(source, "pack-objects", str(pack), data=membership)
    if git.get("head_commit"):
        _git(
            destination,
            "-c",
            "core.logAllRefUpdates=false",
            "update-ref",
            "--no-deref",
            "HEAD",
            git["head_commit"],
        )
    _git(destination, "read-tree", tree)


@contextmanager
def isolated_index_comparison(source, git, *, exported):
    """Compare the captured Git trees with fresh Git configuration and objects only."""
    source = Path(source).resolve()
    objects = source / ".git" / "objects"
    if not objects.is_dir() or objects.resolve() != objects:
        raise GitSourceError("whitespace_object_store_unavailable")
    if exported:
        # The Hook exports a self-contained object store. Git metadata outside
        # that export must not affect the native comparison.
        if any(
            (objects / "info" / name).exists()
            for name in ("alternates", "http-alternates")
        ):
            raise GitSourceError("whitespace_export_object_store_not_self_contained")
        for directory, subdirs, files in objects.walk():
            if any(
                (entry := directory / name).is_symlink() or entry.is_junction()
                for name in (*subdirs, *files)
            ):
                raise GitSourceError("whitespace_export_object_store_linked")

    target = git.get("execution_tree")
    object_format = (
        _OBJECT_FORMATS.get(len(target)) if isinstance(target, str) else None
    )
    if (
        git.get("snapshot") != "index"
        or not object_format
        or not re.fullmatch(r"[0-9a-f]+", target)
        or git.get("index_tree") != target
    ):
        raise GitSourceError("whitespace_index_tree_unverified")

    with tempfile.TemporaryDirectory(prefix="eci-git-comparison-") as directory:
        workspace = Path(directory).resolve()
        checkout = workspace / "comparison"
        checkout.mkdir()
        config = workspace / "gitconfig"
        attributes = workspace / "attributes"
        config.touch()
        attributes.touch()
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.upper().startswith("GIT_")
        }
        env.update(
            GIT_CONFIG_GLOBAL=str(config),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_ATTR_NOSYSTEM="1",
            GIT_NO_REPLACE_OBJECTS="1",
        )
        _git(
            checkout,
            "init",
            "--quiet",
            "--template=",
            f"--object-format={object_format}",
            env=env,
        )
        _git(
            checkout,
            "config",
            "--local",
            "core.attributesFile",
            attributes.as_posix(),
            env=env,
        )
        env["GIT_OBJECT_DIRECTORY"] = objects.as_posix()

        if _git(checkout, "cat-file", "-t", target, env=env).strip() != b"tree":
            raise GitSourceError("whitespace_index_tree_unverified")
        records = _git(checkout, "ls-tree", "-r", "-z", target, env=env)
        _verify_files(source, records, object_format)

        base = git.get("base_commit")
        if base is None:
            empty = _git(
                checkout, "hash-object", "-t", "tree", "--stdin", data=b"", env=env
            )
            base_tree = empty.decode("ascii").strip()
            if (
                git.get("baseline_status") != "unborn"
                or git.get("head_commit") is not None
                or git.get("base_tree") != base_tree
            ):
                raise GitSourceError("whitespace_baseline_unverified")
        else:
            if (
                not isinstance(base, str)
                or len(base) != len(target)
                or not re.fullmatch(r"[0-9a-f]+", base)
            ):
                raise GitSourceError("whitespace_baseline_unverified")
            if _git(checkout, "cat-file", "-t", base, env=env).strip() != b"commit":
                raise GitSourceError("whitespace_baseline_unverified")
            base_tree = (
                _git(checkout, "rev-parse", "--verify", f"{base}^{{tree}}", env=env)
                .decode("ascii")
                .strip()
            )
        if _git(checkout, "cat-file", "-t", base_tree, env=env).strip() != b"tree":
            raise GitSourceError("whitespace_baseline_unverified")
        yield checkout, env, base_tree


def whitespace_diff_args(base, *, tree=None, paths=None):
    if tree is None:
        args = ["diff", "--check", base]
    else:
        args = [
            f"--attr-source={tree}",
            "diff",
            "--check",
            "--no-ext-diff",
            "--no-textconv",
            base,
            tree,
        ]
    args.append("--")
    if paths is not None:
        args.extend(f":(literal){path}" for path in paths)
    return args


def whitespace_settings(root, paths, *, tree=None, env=None):
    """Observe the policy and Git-resolved attributes of the actual diff inputs."""
    root = Path(root)

    def config_value(*args):
        result = _run(root, "config", *args, env=env)
        if result.returncode == 1:
            return None
        if result.returncode != 0:
            raise GitSourceError("whitespace_config_unreadable")
        return result.stdout.decode("utf-8", errors="replace").strip()

    def file_digest(path, *, prefixed=False):
        if not path:
            return None
        candidate = root / path
        if not candidate.exists():
            return f"missing:{candidate.as_posix()}" if prefixed else None
        if not candidate.is_file():
            raise GitSourceError("whitespace_config_unreadable")
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        return f"sha256:{digest}" if prefixed else digest

    info = (
        _git(root, "rev-parse", "--git-path", "info/attributes", env=env)
        .decode("utf-8", errors="replace")
        .strip()
    )
    if not info:
        raise GitSourceError("whitespace_config_unreadable")
    paths = sorted({str(path).replace("\\", "/") for path in paths if path})
    triples = []
    if paths:
        attr_source = [f"--attr-source={tree}"] if tree is not None else []
        payload = b"".join(path.encode("utf-8") + b"\0" for path in paths)
        output = _git(
            root,
            *attr_source,
            "check-attr",
            "-a",
            "-z",
            "--stdin",
            env=env,
            data=payload,
        )
        fields = output.decode("utf-8", errors="replace").split("\0")
        if fields[-1] == "":
            fields.pop()
        if len(fields) % 3 or set(fields[::3]) - set(paths):
            raise GitSourceError("whitespace_config_unreadable")
        triples = sorted(
            fields[index : index + 3] for index in range(0, len(fields), 3)
        )
    attrs = json.dumps(triples, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "core.whitespace": config_value("--get", "core.whitespace"),
        "info_attributes_sha256": file_digest(info),
        "core_attributes_file_sha256": file_digest(
            config_value("--path", "--get", "core.attributesfile"), prefixed=True
        ),
        "effective_git_attributes_sha256": f"sha256:{hashlib.sha256(attrs).hexdigest()}",
    }


def whitespace_comparison_ref(checkout, env, *, base, target, paths):
    paths = sorted({path.replace("\\", "/") for path in paths})
    return {
        "kind": "git_whitespace_comparison",
        "base_tree": base,
        "target_tree": target,
        "paths": paths,
        "git_config": whitespace_settings(checkout, paths, tree=target, env=env),
    }


def run_exported_index_whitespace(source, git, paths):
    """Run one native diff and retain the settings measured in its Git process environment."""
    try:
        with isolated_index_comparison(source, git, exported=True) as (
            checkout,
            env,
            base,
        ):
            target = git["execution_tree"]
            before = whitespace_comparison_ref(
                checkout, env, base=base, target=target, paths=paths
            )
            result = _run(
                checkout,
                *whitespace_diff_args(base, tree=target, paths=before["paths"]),
                env=env,
            )
            try:
                after = whitespace_comparison_ref(
                    checkout, env, base=base, target=target, paths=paths
                )
            except (GitSourceError, OSError):
                after = None
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout.decode("utf-8", errors="replace"),
                "stderr": result.stderr.decode("utf-8", errors="replace"),
                "comparison": before if before == after else None,
            }
    except (GitSourceError, OSError) as error:
        return {"exit_code": None, "stderr": str(error)}
