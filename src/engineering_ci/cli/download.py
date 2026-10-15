"""Download an existing Run; this entry point cannot dispatch a Workflow."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Also support IDE/direct-file execution without losing the package context.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "engineering_ci.cli"

from ..task_observation import task_entry
from ._execution import (
    add_runtime_arguments,
    dependency_error,
    invoke_execution,
    runtime_arguments,
)


def entrypoint(args):
    return invoke_execution(args)


def resolve_repo(**kwargs):
    from ..execution.repository import resolve_repo as resolve

    return resolve(**kwargs)


def api(repository, path):
    from ..github_cli import GitHub

    return GitHub(repository)(path)


def pages(repository, path, key, query=api):
    values = []
    for page in range(1, 101):
        batch = query(repository, f"{path}?per_page=100&page={page}")[key]
        if not isinstance(batch, list):
            raise TypeError("GitHub returned an invalid list")
        values.extend(batch)
        if len(batch) < 100:
            return values
    raise ValueError(
        "Too many entries for the interactive menu; use an explicit workflow/Run ID"
    )


def choose(values, title, label, ask=input, write=print, *, all_option=False):
    if not values:
        raise ValueError(f"No {title} available")
    write(title)
    if all_option:
        write("0. All artifact names")
    for index, value in enumerate(values, 1):
        # Remote names cannot inject terminal control sequences.
        display = "".join(char if char.isprintable() else " " for char in label(value))
        write(f"{index}. {display}")
    while True:
        answer = ask("Select number (q to cancel): ").strip()
        if answer.lower() == "q":
            raise KeyboardInterrupt
        if answer.isdecimal() and all_option and int(answer) == 0:
            return None
        if answer.isdecimal() and 1 <= int(answer) <= len(values):
            return values[int(answer) - 1]
        write("Enter one of the listed numbers, or q.")


def interactive(
    repository,
    out,
    *,
    backend=None,
    runtime=(),
    query=api,
    ask=input,
    write=print,
    invoke=None,
):
    from ..execution.repository import REPO_PATTERN, resolve_repo

    repository = repository or resolve_repo()[0]
    if not REPO_PATTERN.fullmatch(repository):
        raise ValueError("Repository must be OWNER/REPO")
    workflow = choose(
        pages(repository, "actions/workflows", "workflows", query),
        "Workflows",
        lambda value: f"{value['name']} ({value['path']}, {value['id']})",
        ask,
        write,
    )
    workflow_id = str(workflow["id"])
    runs = query(repository, f"actions/workflows/{workflow_id}/runs?per_page=30")[
        "workflow_runs"
    ]
    run = choose(
        runs,
        "Latest 30 runs",
        lambda value: (
            f"#{value['run_number']} {value.get('display_title', '')} | {value['head_branch']} | {value['status']}/{value.get('conclusion')} | {value['id']}"
        ),
        ask,
        write,
    )
    if run["workflow_id"] != workflow["id"] or not run.get("head_branch"):
        raise ValueError("Selected Run does not match workflow or branch")
    artifacts = pages(
        repository, f"actions/runs/{run['id']}/artifacts", "artifacts", query
    )
    names = sorted({value["name"] for value in artifacts if not value.get("expired")})
    artifact = (
        choose(names, "Artifact names", str, ask, write, all_option=True)
        if names
        else None
    )
    logs_only = not names and run["status"] == "completed"
    if logs_only:
        write("No unexpired artifacts; collecting logs only.")
    elif not names:
        write("Run is not complete; wait for it and collect its artifacts and logs.")
    args = [
        "--repo",
        repository,
        "--workflow",
        workflow_id,
        "--ref",
        run["head_branch"],
        "--resume",
        str(run["id"]),
        "--out",
        out,
        "--save-log",
    ]
    if artifact:
        args += ["--artifact", artifact]
    elif logs_only:
        args += ["--no-artifacts"]
    if backend:
        args += ["--download-backend", backend]
    args += list(runtime)
    write(f"Resume existing Run {run['id']}; output: {out}")
    if invoke is None:
        from ..execution.cli import entrypoint as invoke
    return invoke(args)


def existing_run(repository, run_id, workflow=None, ref=None, *, query=None):
    """Resolve identity from the actual Run, rejecting conflicting caller constraints."""
    run = (query or api)(repository, f"actions/runs/{run_id}")
    if (
        not isinstance(run, dict)
        or type(run.get("id")) is not int
        or run["id"] != run_id
        or type(run.get("workflow_id")) is not int
        or run["workflow_id"] <= 0
        or not isinstance(run.get("head_branch"), str)
        or not run["head_branch"]
    ):
        raise ValueError("GitHub returned an invalid Run identity")
    remote = run.get("repository")
    remote_repo = remote.get("full_name") if isinstance(remote, dict) else None
    if not isinstance(remote_repo, str) or remote_repo.lower() != repository.lower():
        raise ValueError("Run does not belong to the requested repository")
    if ref is not None and ref != run["head_branch"]:
        raise ValueError("Run does not belong to the requested ref")
    if workflow is not None:
        remote_path = run.get("path")
        path = remote_path.split("@", 1)[0] if isinstance(remote_path, str) else ""
        if workflow != str(run["workflow_id"]) and workflow not in (
            path,
            path.rsplit("/", 1)[-1],
        ):
            raise ValueError("Run does not belong to the requested workflow")
    return workflow or str(run["workflow_id"]), run["head_branch"]


@task_entry(
    "Download existing run artifacts", prerequisites=("transitions", "filelock")
)
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Examples: download.py --interactive --repo OWNER/REPO; "
        "download.py --run RUN_ID --save-log --out workflow-artifacts.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run", "--resume", dest="run", metavar="RUN_ID")
    group.add_argument("--interactive", action="store_true")
    parser.add_argument("--repo", help="OWNER/REPO; infer from current Git origin")
    parser.add_argument("--workflow", help="Optional constraint on the selected Run")
    parser.add_argument("--ref", help="Optional constraint on the selected Run")
    add_runtime_arguments(parser)
    if not argv:
        parser.print_help()
        return 0
    options = parser.parse_args(argv)
    if options.run is None:
        if not options.interactive:
            parser.error("Choose --interactive or --run RUN_ID")
        if not sys.stdin.isatty():
            parser.error(
                "Interactive mode requires a terminal; supply --run RUN_ID for an agent"
            )
        if options.workflow is not None or options.ref is not None:
            parser.error("--workflow and --ref require --run RUN_ID")
        if options.artifact is not None or options.no_artifacts:
            parser.error(
                "Select artifacts in the menu, or use --run with --artifact/--no-artifacts"
            )
    elif not options.run.isdecimal() or int(options.run) <= 0:
        parser.error("--run/--resume requires a positive Run ID")
    try:
        from ..execution.repository import REPO_PATTERN
    except ModuleNotFoundError as error:
        return dependency_error(error)
    if options.repo and not REPO_PATTERN.fullmatch(options.repo):
        parser.error("--repo must be OWNER/REPO")
    try:
        if options.run is None:
            return interactive(
                options.repo, options.out, runtime=runtime_arguments(options)
            )
        repository = options.repo or resolve_repo(refresh=options.refresh_repo)[0]
        workflow, ref = existing_run(
            repository, int(options.run), options.workflow, options.ref
        )
        return entrypoint(
            [
                "--repo",
                repository,
                "--workflow",
                workflow,
                "--ref",
                ref,
                "--resume",
                options.run,
                *runtime_arguments(options),
            ]
        )
    except KeyboardInterrupt:
        print(
            "Cancelled; inspect the existing ghwf state before resuming.",
            file=sys.stderr,
        )
        return 130
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(f"download: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
