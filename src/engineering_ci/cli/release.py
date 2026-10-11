"""Deliver the current branch for review, then publish after human merge."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "engineering_ci.cli"

from ..github_cli import GitHub
from ..pipeline import PIPELINE
from ..release import verification
from ..release.version import checked_version, normalize
from ._execution import (
    add_wait_arguments,
    execution_environment,
    invoke_execution,
    runtime_arguments,
)

RELEASE_CONFIG = PIPELINE["delivery"]["release"]


def continue_command(options, *, run_id=None, inspect_run=False):
    """Print an executable continuation with the actual repository/version/Run."""
    command = "verify" if inspect_run else options.command
    args = [
        "uv",
        "run",
        "--locked",
        "python",
        "scripts/release.py",
        command,
        "--repo",
        options.repo,
        "--version",
        "v" + options.version,
    ]
    if run_id:
        args += ["--run" if inspect_run else "--resume", str(run_id)]
    if not inspect_run and options.command == "validate":
        args += ["--ref", options.ref, "--scenario", options.scenario]
    if not inspect_run and not run_id and options.command == "publish":
        if getattr(options, "include_all", False):
            args += ["--all"]
        if getattr(options, "message", None):
            args += ["-m", options.message]
    # Single-quoted arguments work for both PowerShell and shell in normal examples.
    import os
    import shlex

    def quote(value):
        if os.name == "nt":
            if re.fullmatch(r"[A-Za-z0-9_./:@+-]+", value):
                return value
            return "'" + value.replace("'", "''") + "'"
        return shlex.quote(value)

    print("Continue: " + " ".join(quote(value) for value in args))


def execution_checkpoint(options, inputs, ref, *, api=None, expected_head=None):
    """Read the existing execution checkpoint; do not introduce another state file."""
    from ..execution import cli
    from ..execution.models import DispatchIdentity
    from ..execution.store import StateStore

    identity = DispatchIdentity.create(
        options.repo, RELEASE_CONFIG["workflow"], ref, inputs
    )
    store = StateStore(cli.default_cache_dir() / "runs" / f"{identity.key}.json")
    state = store.load()
    if state is None:
        return False
    print(
        f"Execution checkpoint: {options.command} on {ref}: {state.phase}; state file: {store.path}"
    )
    if state.run_id:
        print(f"Run: https://github.com/{options.repo}/actions/runs/{state.run_id}")
        if state.remote_conclusion:
            print(f"Remote conclusion: {state.remote_conclusion}")
        if api:
            run = api(f"actions/runs/{state.run_id}")
            if run.get("id") != state.run_id or run.get("head_branch") != ref:
                raise ValueError("Cached Run identity does not match the queried Run")
            print(
                f"Live Run: {run['status']} / {run.get('conclusion') or '-'}; "
                f"attempt: {run['run_attempt']}; commit: {run['head_sha']}"
            )
            actual_version = verification.version_at(api, run["head_sha"])
            if actual_version != options.version:
                print(
                    f"Historical Run: metadata is v{actual_version}, requested v{options.version}. "
                    "It is not a usable checkpoint for this version; the saved state is preserved."
                )
                return False
            if expected_head and run["head_sha"] != expected_head:
                print(
                    f"Historical Run: commit {run['head_sha']} differs from the current "
                    f"publication commit {expected_head}; the saved state is preserved."
                )
                return False
        if state.phase == "FINISHED":
            print(
                "The recorded attempt finished; inspect its linked failure before requesting another attempt."
            )
            continue_command(
                options,
                run_id=state.run_id if state.remote_conclusion == "success" else None,
            )
        else:
            continue_command(options, run_id=state.run_id)
    elif state.phase in {"UNKNOWN", "DISPATCHING"}:
        print("Dispatch is uncertain: no automatic second POST is allowed.")
        print(
            f"Confirm the Run at https://github.com/{options.repo}/actions/workflows/{RELEASE_CONFIG['workflow']}"
        )
        print("The Run ID cannot be filled in safely until GitHub confirms it.")
        if api:
            from urllib.parse import quote

            value = api(
                f"actions/workflows/{RELEASE_CONFIG['workflow']}/runs?event=workflow_dispatch&per_page=20&branch="
                + quote(ref, safe="")
            )
            for run in value.get("workflow_runs", []):
                if (
                    run.get("created_at", "")[:19]
                    < (state.dispatch_started_at or "")[:19]
                ):
                    continue
                if verification.version_at(api, run["head_sha"]) != options.version:
                    continue
                print(
                    f"Unconfirmed candidate Run {run['id']}: "
                    f"https://github.com/{options.repo}/actions/runs/{run['id']}; "
                    f"commit: {run['head_sha']}; {run['status']} / {run.get('conclusion') or '-'}"
                )
                print("Only after confirming this is the original request:")
                continue_command(options, run_id=run["id"])
    return True


def show_status(options):
    """One read-only stop-point lookup for delivery, PR and existing release Runs."""
    from argparse import Namespace

    from ..release.context import ExecutionContext
    from ..release.delivery import Checkout
    from ..release.lifecycle import ReleaseLifecycle
    from ..release.pipeline import DeliveryPipeline

    checkout = Checkout(options.repo)
    context = checkout.context(options.version, validate_version=False)
    execution = ExecutionContext(checkout.root, context)
    lifecycle = ReleaseLifecycle(execution)
    checkout.show(context, diff=options.diff)
    print(f"Delivery phase: {lifecycle.phase}")
    path = checkout.root / ".test-tools/checks/pr.json"
    if path.is_file():
        from .. import checks

        try:
            result, code = checks.inspect(path, root=checkout.root)
            print(
                f"Local checks: {'passed for current input' if not code else 'not a current pass'}; {path}"
            )
            for entry in result["checks"]:
                platform = (entry.get("environment") or {}).get("platform", "unknown")
                print(
                    f"Local check {entry['name']} [{platform}]: {entry['status']}; "
                    f"freshness: {entry['freshness']}; tool exit: {entry.get('exit_code')}"
                )
                if entry.get("counts"):
                    print(
                        f"  Recorded JUnit counts: {json.dumps(entry['counts'], sort_keys=True)}"
                    )
                if entry.get("reason"):
                    print(f"  {entry['reason']}")
                if entry.get("invalidation_reason"):
                    print(f"  {entry['invalidation_reason']}")
        except (OSError, ValueError, KeyError, TypeError):
            print(
                f"Local checks: saved report unavailable or invalid; {path}; not a pass"
            )
    else:
        print("Local checks: no saved PR report")
    pr = context["pr"]
    pending_delivery = False
    publication_head = None
    if lifecycle.phase in {"REVIEW_merged", "BLOCKED_merged"} and pr:
        print(
            f"Human merge confirmed: {pr['url']}; merge commit: {pr['mergeCommit']['oid']}"
        )
        if lifecycle.phase == "BLOCKED_merged":
            pending_delivery = True
            print(
                f"Stopped at delivery: PR #{pr['number']} is merged, but the listed local "
                "changes were not included. Preserve them and deliver a follow-up PR before publishing."
            )
        else:
            publication_head = DeliveryPipeline(execution, checkout).merged()
            lifecycle.advance("confirm_merge")
            print(f"Remote main includes the reviewed PR; commit: {publication_head}")
    elif lifecycle.phase == "REVIEW_merged":
        publication_head = DeliveryPipeline(execution, checkout).merged()
        lifecycle.advance("confirm_merge")
    elif lifecycle.phase.startswith("BLOCKED_"):
        pending_delivery = True
        print(
            "Stopped before delivery: resolve local metadata, conflicts or main changes; historical Runs do not authorize publication."
        )
    if pr and pr["state"] == "OPEN":
        from ..release.delivery import handoff

        handoff(checkout, context, require_head=False, execution=execution)
        if context["files"] or context["head"] != context["pr"]["headRefOid"]:
            print("Stopped before committing the listed local changes.")
            if context["files"]:
                print(
                    "Repeat publish with your git commit -m message; the PR title is not a saved commit message."
                )
            continue_command(
                Namespace(
                    command="publish",
                    repo=options.repo,
                    version=options.version,
                    include_all=bool(context["files"]),
                    message=None,
                )
            )
    api = GitHub(options.repo)
    receipt = verification.published(api, options.repo, "v" + options.version)
    if receipt and receipt["status"] == "published":
        print(f"Completed: published v{options.version}; {receipt['release_url']}")
        print(f"Published commit: {receipt['commit_sha']}")
        evidence = receipt["manifest"]["workflow"]
        print(
            f"Verified Run: {evidence['run_url']}; attempt: {evidence['run_attempt']}"
        )
        return 0
    publication_found = False
    for mode, ref, scenario in dict.fromkeys(
        (
            ("publish", RELEASE_CONFIG["branch"], "pass"),
            ("validate", context["branch"], "pass"),
            ("validate", RELEASE_CONFIG["branch"], "pass"),
            ("validate", context["branch"], "assertion"),
            ("validate", RELEASE_CONFIG["branch"], "assertion"),
        )
    ):
        resume = Namespace(
            command=mode,
            repo=options.repo,
            version=options.version,
            ref=ref,
            scenario=scenario,
            include_all=False,
            message=None,
        )
        inputs = {
            "version": "v" + options.version,
            "publish": str(mode == "publish").lower(),
            "pytest_scenario": scenario,
        }
        applicable = execution_checkpoint(
            resume,
            inputs,
            ref,
            api=api,
            expected_head=publication_head if mode == "publish" else None,
        )
        if mode == "publish" and applicable:
            publication_found = True
    if lifecycle.phase == "BLOCKED_merged":
        print(
            f"Current stop: local changes are not delivered; merged PR #{pr['number']} cannot "
            "be updated. A follow-up development PR is needed; no publication was requested."
        )
    elif (
        not pending_delivery
        and not publication_found
        and (not pr or pr["state"] != "OPEN")
    ):
        print("No usable saved publication Run for this version and reviewed commit.")
        if publication_head:
            identity = resolve_target(api, RELEASE_CONFIG["branch"], options.version)
            if identity["commit_sha"] != publication_head:
                raise ValueError(
                    "Remote main changed during status lookup; inspect status again"
                )
            print(
                f"Next step: request complete validation and publication of {identity['tag']} at {publication_head}."
            )
        continue_command(
            Namespace(
                command="publish",
                repo=options.repo,
                version=options.version,
                include_all=bool(context["files"]),
                message=None,
            )
        )
    print("Publication is not confirmed; status only queried the current facts.")
    return 0


def entrypoint(args):
    return invoke_execution(args, collect_outputs=False, restart_finished=True)


def resolve_target(api, ref, version):
    return verification.target(api, ref, version)


def inspect(api, repository, identity, run_id):
    receipt = verification.published(
        api, repository, identity["tag"], head=identity["commit_sha"]
    )
    if receipt and receipt["status"] == "published":
        if receipt["manifest"]["workflow"]["run_id"] != run_id:
            raise ValueError("Release evidence is bound to another Run")
        state = {**receipt["manifest"]["workflow"], "status": "passed"}
    else:
        state = verification.validation_state(api, repository, identity, run_id)
    return {
        "schema_version": 1,
        "repository": repository,
        "release": identity,
        "validation": state,
        "publication": {
            "status": receipt["status"] if receipt else "not_published",
            "release_url": receipt["release_url"] if receipt else None,
        },
        "result": "passed" if state["status"] == "passed" else state["status"],
    }


def positive_id(raw):
    if not raw.isdecimal() or int(raw) <= 0:
        raise argparse.ArgumentTypeError("Run ID must be a positive integer")
    return int(raw)


def prepare_publication(options):
    from ..release import delivery

    checkout = delivery.Checkout(options.repo)
    try:
        result = delivery.prepare(
            options.repo,
            options.version,
            message=options.message,
            include_all=options.include_all,
            report=options.checks_report,
            checkout=checkout,
        )
    finally:
        if hasattr(checkout, "execution_context"):
            options.execution_context = checkout.execution_context
            options.lifecycle = checkout.lifecycle
    if not result:
        options.publication_sha = checkout.publication_sha
    return result


def repository(raw):
    if not verification.manifest.REPOSITORY.fullmatch(raw):
        raise argparse.ArgumentTypeError("Repository must be OWNER/REPO")
    return raw


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    for name in ("status", "validate", "verify", "publish"):
        command = commands.add_parser(
            name,
            help={
                "status": "Show the current checkout, changed files and PR without writing",
                "validate": "Start complete validation without publishing (optional standalone check)",
                "verify": "Inspect the specified Run and its evidence; GET requests only",
                "publish": "Deliver checks/commit/push/PR; after human merge, validate and publish",
            }[name],
        )
        command.add_argument("--repo", type=repository, default="dymzz/engineering-ci")
        command.add_argument(
            "--version",
            type=normalize,
            required=True,
            help="Explicit version; checked against all four metadata fields",
        )
        if name == "publish":
            command.add_argument(
                "-m",
                "--message",
                help="git commit -m message; its subject supplies a new PR title",
            )
            command.add_argument(
                "--all",
                dest="include_all",
                action="store_true",
                help="git add --all: stage the listed tracked and untracked changes",
            )
            command.add_argument(
                "--checks-report",
                type=Path,
                help="Reuse a complete current PR result, otherwise run the same PR profile",
            )
        if name == "status":
            command.add_argument(
                "--diff",
                action="store_true",
                help="Also show tracked changes as a patch",
            )
        if name in {"validate", "verify"}:
            command.add_argument(
                "--ref",
                default=RELEASE_CONFIG["branch"] if name == "validate" else None,
                help="Validation branch; optional Run identity constraint for verify",
            )
        if name == "verify":
            command.add_argument(
                "--json", action="store_true", help="Print machine-readable state"
            )
            command.add_argument(
                "--run",
                type=positive_id,
                required=True,
                help="Existing Release or validation Run ID",
            )
        elif name != "status":
            add_wait_arguments(command, include_state=False)
            command.add_argument(
                "--resume",
                type=positive_id,
                metavar="RUN_ID",
                help="Resume a confirmed existing Run, including UNKNOWN recovery",
            )
            if name == "validate":
                command.add_argument(
                    "--scenario", choices=("pass", "assertion"), default="pass"
                )
    if not argv:
        parser.print_help()
        return 0
    options = parser.parse_args(argv)
    if options.command is None:
        parser.error("Choose status, validate, verify or publish")
    if options.command in {"status", "validate", "publish"}:
        environment_status = execution_environment()
        if environment_status:
            return environment_status
    try:
        if options.command == "status":
            return show_status(options)
        api = GitHub(options.repo)
        if options.command == "verify":
            identity = verification.run_target(
                api, options.repo, options.run, options.version, options.ref
            )
            result = inspect(api, options.repo, identity, options.run)
            if options.json:
                print(json.dumps(result, sort_keys=True))
            else:
                print(f"Release {identity['tag']} at {identity['commit_sha']}")
                print(
                    f"Validation: {result['validation']['status']}; "
                    f"{result['validation'].get('run_url', 'no Run')}"
                )
                if result["validation"].get("conclusion"):
                    print(f"Run conclusion: {result['validation']['conclusion']}")
                print(
                    f"Publication: {result['publication']['status']}; "
                    f"{result['publication']['release_url'] or 'no Release'}"
                )
            return 0 if result["result"] == "passed" else 1
        if options.command == "publish":
            receipt = verification.published(api, options.repo, "v" + options.version)
            if receipt and receipt["status"] == "published":
                print(f"Already published: {receipt['release_url']}")
                return 0
            if not options.resume:
                result = prepare_publication(options)
                if result:
                    # The review handoff already names the next human action.
                    phase = getattr(getattr(options, "lifecycle", None), "phase", None)
                    if phase != "REVIEW_waiting":
                        continue_command(options)
                    return result
        identity = resolve_target(
            api, getattr(options, "ref", RELEASE_CONFIG["branch"]), options.version
        )
        if options.command == "publish" and getattr(options, "lifecycle", None):
            options.lifecycle.admit_publication(identity)
        if (
            options.command == "publish"
            and getattr(options, "publication_sha", identity["commit_sha"])
            != identity["commit_sha"]
        ):
            raise ValueError(
                "Remote main changed after delivery; stopped before Dispatch"
            )
        inputs = {
            "version": identity["tag"],
            "publish": str(options.command == "publish").lower(),
            "pytest_scenario": getattr(options, "scenario", "pass"),
        }
        if options.resume:
            run = api(f"actions/runs/{options.resume}")
            if (
                run.get("head_sha") != identity["commit_sha"]
                or run.get("head_branch") != identity["ref"]
            ):
                raise ValueError("Resume Run belongs to another release commit")
        args = [
            "--repo",
            options.repo,
            "--workflow",
            RELEASE_CONFIG["workflow"],
            "--ref",
            identity["ref"],
            *runtime_arguments(options),
        ]
        for key, value in inputs.items():
            args += ["--input", f"{key}={value}"]
        if options.resume:
            args += ["--resume", str(options.resume)]
        print(
            f"Release {options.command}: {identity['tag']} on {options.repo}@{identity['ref']}",
            flush=True,
        )
        result = entrypoint(args)
        if result:
            try:
                execution_checkpoint(options, inputs, identity["ref"])
            except (OSError, ValueError, RuntimeError):
                print(
                    "Checkpoint could not be read; original execution failure is retained."
                )
        if result == 0 and options.command == "publish":
            receipt = verification.published(api, options.repo, identity["tag"])
            if receipt is None or receipt["status"] != "published":
                raise ValueError("Release Run ended without a verified final Release")
            print(f"Published: {receipt['release_url']}")
        return result
    except KeyboardInterrupt:
        print("Release operation interrupted", file=sys.stderr)
        print(
            "Inspect context: uv run --locked python scripts/release.py status "
            f"--repo {options.repo} --version v{options.version}"
        )
        return 130
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as error:
        if options.command == "verify" and options.json:
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "result": "unavailable",
                        "reason": str(error),
                    },
                    sort_keys=True,
                )
            )
        else:
            print(f"Release refused: {error}", file=sys.stderr)
            if options.command == "publish":
                if getattr(options, "lifecycle", None):
                    print(f"Stopped phase: {options.lifecycle.phase}")
                print(
                    f"Stopped for v{options.version} on {options.repo}; formal publication is not confirmed."
                )
                print(
                    "Inspect context: uv run --locked python scripts/release.py status "
                    f"--repo {options.repo} --version v{options.version}"
                )
                try:
                    actual = checked_version()
                except (OSError, ValueError, KeyError, TypeError):
                    actual = None
                if actual == options.version:
                    continue_command(options)
                else:
                    print(
                        f"Local metadata: v{actual}; requested v{options.version}."
                        if actual
                        else "Local version metadata is unavailable or inconsistent."
                    )
                    if actual:
                        from copy import copy

                        current_options = copy(options)
                        current_options.version = actual
                        print(
                            "If the local metadata version is intended, use this explicit request:"
                        )
                        continue_command(current_options)
                    else:
                        print(
                            "Resolve the four version fields before requesting publication."
                        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
