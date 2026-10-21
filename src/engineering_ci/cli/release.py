"""Request, inspect or wait for the existing Release Workflow; Git/PR stay native."""

import argparse
import json
import subprocess
import sys

from ..github_cli import ApiError, GitHub, positive_id, repository
from ..release import RELEASE_CONFIG, verification
from ..release.publishing import tag_commit
from ..release.request import (
    DispatchRejected,
    UncertainDispatch,
    dispatch,
    request_path,
)
from ..release.version import normalize


def continuation(repo, version, run_id):
    return f"uv run --locked python scripts/release.py wait --repo {repo} --version v{version} --run {run_id}"


def inspect(api, repo, version, run_id):
    """Verify real evidence for this Run, including original published attempts."""
    identity = verification.run_target(api, repo, run_id, version)
    publication = verification.published(
        api, repo, identity["tag"], head=identity["commit_sha"]
    )
    if publication and publication["status"] == "published":
        workflow = publication["manifest"]["workflow"]
        if workflow["run_id"] != run_id:
            raise ValueError("Release belongs to another verification Run")
        return {
            "result": "published",
            "validation": {**workflow, "status": "passed"},
            "publication": {k: v for k, v in publication.items() if k != "manifest"},
        }
    validation = verification.validation_state(api, repo, identity, run_id)
    status = validation["status"]
    return {
        "result": "validated" if status == "passed" else status,
        "validation": validation,
        "publication": publication or {"status": "not_published"},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["publish", "validate", "status", "verify", "wait"]
    )
    parser.add_argument("--version", required=True, type=normalize)
    parser.add_argument(
        "--repo", help="OWNER/REPO; otherwise use native gh origin resolution"
    )
    parser.add_argument(
        "--run", type=positive_id, help="Explicit existing Run; never selects latest"
    )
    parser.add_argument("--ref", help="Required for validate; publication uses main")
    parser.add_argument("--scenario", choices=["pass", "assertion"], default="pass")
    parser.add_argument(
        "--wait", action="store_true", help="Wait after a requested dispatch"
    )
    parser.add_argument("--timeout", type=positive_id, default=2700)
    parser.add_argument("--json", action="store_true")
    if argv == [] or argv is None and len(sys.argv) == 1:
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    if args.command in {"status", "verify", "wait"} and args.run is None:
        parser.error("This read/wait action requires --run RUN_ID")
    if args.command == "validate" and not args.ref:
        parser.error("validate requires an explicit --ref")
    if args.command == "publish" and (
        args.ref not in (None, RELEASE_CONFIG["branch"]) or args.scenario != "pass"
    ):
        parser.error("Publication requires main and scenario pass")
    value = {
        "operation": args.command,
        "repository": args.repo,
        "version": args.version,
        "result": "unavailable",
        "publication": {"status": "unconfirmed"},
        "next_commands": [],
    }
    code = 1
    try:
        repo = repository(args.repo)
        value["repository"] = repo
        api = GitHub(repo)
        if args.run is None:
            identity = verification.target(
                api, args.ref or RELEASE_CONFIG["branch"], args.version
            )
            existing = verification.published(
                api, repo, identity["tag"], head=identity["commit_sha"]
            )
            if existing and existing["status"] == "published":
                args.run = existing["manifest"]["workflow"]["run_id"]
            else:
                current = tag_commit(api, identity["tag"])
                if current is not None and current != identity["commit_sha"]:
                    raise ValueError("Tag conflict; refusing dispatch")
                value["commit_sha"] = identity["commit_sha"]
                value["next_commands"] = [
                    f"gh run list --repo {repo} --workflow {RELEASE_CONFIG['workflow']} --commit {identity['commit_sha']}"
                ]
                args.run = dispatch(api, identity, args.command, args.scenario)
        value["run"] = {
            "id": args.run,
            "url": f"https://github.com/{repo}/actions/runs/{args.run}",
        }
        value["next_commands"] = [continuation(repo, args.version, args.run)]
        # Never watch or associate a Run until version/workflow/SHA identity is verified.
        identity_run = verification.run_target(
            api, repo, args.run, args.version, args.ref
        )
        if (
            value.get("commit_sha")
            and identity_run["commit_sha"] != value["commit_sha"]
        ):
            raise ValueError("Dispatched Run differs from requested commit")
        live = api.run_info(args.run)
        value["run"].update(
            attempt=live["run_attempt"],
            commit_sha=live["head_sha"],
            status=live["status"],
            conclusion=live.get("conclusion"),
        )
        if args.command == "wait" or args.wait:
            watch_code = api.watch(args.run, timeout=args.timeout)
            try:
                after = api.run_info(args.run)
            except (ApiError, OSError, ValueError, KeyError, TypeError):
                if not watch_code:
                    raise
                after = None
                value["run"].update(status="unavailable", conclusion=None)
            if after is not None and (
                after["head_sha"] != live["head_sha"]
                or after["run_attempt"] != live["run_attempt"]
            ):
                if not watch_code:
                    raise ValueError("Run changed SHA or attempt while waiting")
                after = None
                value["run"].update(status="unavailable", conclusion=None)
            if after:
                value["run"].update(
                    status=after["status"], conclusion=after.get("conclusion")
                )
            if watch_code:
                value.update(
                    result="failed",
                    reason="gh_run_watch_failed; use the same Run API if checks scope is unavailable",
                )
                code = watch_code
            else:
                value.update(inspect(api, repo, args.version, args.run))
                code = 0 if value["result"] in {"validated", "published"} else 1
        elif args.command in {"publish", "validate"} and live["status"] != "completed":
            value["result"] = "requested"
            code = 0  # request accepted; explicitly not publication success
        else:
            value.update(inspect(api, repo, args.version, args.run))
            code = (
                0
                if value["result"] in {"validated", "published"}
                else 3
                if value["result"] == "running"
                else 1
            )
        if args.command == "publish" and value["result"] == "validated":
            value.update(
                result="unavailable",
                reason="Workflow passed but this version is not published",
            )
            code = 1
    except UncertainDispatch as error:
        value.update(
            result="unknown",
            reason=error.reason,
            http_status=error.status,
            intent=str(request_path(args.version, args.command)),
        )
        if error.run_id:
            value["run"] = {
                "id": error.run_id,
                "url": f"https://github.com/{value['repository']}/actions/runs/{error.run_id}",
            }
            value["next_commands"] = [
                continuation(value["repository"], args.version, error.run_id)
            ]
        code = error.exit_code
    except DispatchRejected as error:
        value.update(result="rejected", reason=str(error), http_status=error.status)
        code = error.exit_code
    except ApiError as error:
        value.update(reason=str(error), http_status=error.status)
        code = error.exit_code
    except KeyboardInterrupt:
        value.update(
            result="interrupted", reason="Interrupted; do not resend a pending POST"
        )
        code = 130
    except subprocess.TimeoutExpired:
        value["reason"] = "Native gh timed out; query the same Run"
        code = 124
    except OSError as error:
        value["reason"] = (
            "Native tool or local file unavailable (" + type(error).__name__ + ")"
        )
        code = 2
    except (ValueError, KeyError, TypeError) as error:
        # Identity/manifest messages are authored locally; raw API content is excluded.
        value["reason"] = (
            str(error) if isinstance(error, ValueError) else "Invalid GitHub metadata"
        )
        code = 1
    value["exit_code"] = code
    if args.json:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Release v{args.version}: {value['repository']}; {value['result']}")
        if "run" in value:
            print(
                f"Run: {value['run']['url']}; {value['run'].get('status', '-')} / {value['run'].get('conclusion') or '-'}"
            )
        if value["publication"].get("release_url"):
            print("Release: " + value["publication"]["release_url"])
        if value.get("reason"):
            print(value["reason"])
        if value.get("intent"):
            print("Dispatch intent: " + value["intent"])
        for command in value["next_commands"]:
            print("Continue: " + command)
    return code
