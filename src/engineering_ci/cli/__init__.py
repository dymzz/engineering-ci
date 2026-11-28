"""User commands; Workflow operations have a separate module entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _check_worktree_source_binding() -> str | None:
    """Diagnostic guard when editable CLI loads from another checkout of one repo."""
    package_file = Path(__file__).resolve()
    src_dir = package_file.parents[2]
    if src_dir.name != "src":
        return None
    pkg_checkout = package_file.parents[3]
    if not (pkg_checkout / ".git").exists():
        return None
    try:
        cwd = Path.cwd().resolve()
    except OSError:
        return None
    from engineering_ci import git_source

    cwd_checkout = git_source.enclosing_git_checkout(cwd)
    if cwd_checkout is None or cwd_checkout == pkg_checkout:
        return None
    if not (cwd_checkout / "src" / "engineering_ci" / "cli" / "__init__.py").is_file():
        return None

    if git_source.registered_worktree(pkg_checkout, cwd_checkout):
        relation = f"is registered worktree {cwd_checkout}"
    elif git_source.shared_git_common_dir(pkg_checkout, cwd_checkout):
        relation = f"shares Git common directory with {pkg_checkout} at {cwd_checkout}"
    else:
        return None
    candidate_src = cwd_checkout / "src"
    return (
        f"engineering-ci: loaded from {src_dir} while cwd {relation}; "
        "refusing to execute another checkout's CLI.\n"
        f"Bind the current worktree source with PYTHONPATH={candidate_src} "
        "(e.g. python -P -m engineering_ci.cli ... or engineering-ci ...)."
    )


def main(argv=None):
    mismatch = _check_worktree_source_binding()
    if mismatch is not None:
        print(mismatch, file=sys.stderr)
        return 2
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        description="Engineering CI: Release Workflow, version metadata and Run downloads; checks use dagger check"
    )
    parser.add_argument(
        "command",
        choices=(
            "release",
            "download",
            "version",
            "policy",
            "select",
            "hygiene",
            "sources",
            "execute",
            "verify",
            "hooks",
            "cicd-telemetry",
            "dagger-telemetry",
            "ci-applicability",
        ),
    )
    if not argv or argv[0] in {"-h", "--help"}:
        parser.print_help()
        return 0
    namespace = parser.parse_args(argv[:1])
    args = argv[1:]
    if namespace.command == "release":
        from .release import main as invoke
    elif namespace.command == "version":
        from ..release.version import main as invoke
    elif namespace.command == "policy":
        from ..security_policy import main as invoke
    elif namespace.command == "select":
        from engineering_ci.selection import main as invoke
    elif namespace.command == "hygiene":
        from engineering_ci.hygiene import main as invoke
    elif namespace.command == "sources":
        from engineering_ci.sources import main as invoke
    elif namespace.command == "execute":
        from engineering_ci.execution import main as invoke
    elif namespace.command == "verify":
        from .verify import main as invoke
    elif namespace.command == "hooks":
        from engineering_ci.hooks import main as invoke
    elif namespace.command == "cicd-telemetry":
        from engineering_ci.cicd_telemetry import main as invoke
    elif namespace.command == "dagger-telemetry":
        from engineering_ci.dagger_telemetry import main as invoke
    elif namespace.command == "ci-applicability":
        from engineering_ci.ci_applicability import main as invoke
    else:
        from .download import main as invoke
    return invoke(args)
