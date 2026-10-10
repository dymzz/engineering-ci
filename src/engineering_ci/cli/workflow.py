"""Choose existing workflows/runs/artifact names, then reuse the ghwf coordinator."""

from __future__ import annotations

import argparse
import sys


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
    repository, out, *, backend=None, query=api, ask=input, write=print, invoke=None
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
    write(f"Resume existing Run {run['id']}; output: {out}")
    if invoke is None:
        from ..execution.cli import entrypoint as invoke
    return invoke(args)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if not args or "--interactive" in args:
            parser = argparse.ArgumentParser(
                description="Select an existing Run and collect its outputs"
            )
            parser.add_argument("--interactive", action="store_true")
            parser.add_argument("--repo")
            parser.add_argument("--out", default="workflow-artifacts")
            parser.add_argument(
                "--download-backend", choices=("auto", "aria2", "native")
            )
            options = parser.parse_args(args)
            if not sys.stdin.isatty():
                parser.error(
                    "Interactive mode requires a terminal; supply --workflow and --resume RUN_ID for an agent"
                )
            return interactive(
                options.repo, options.out, backend=options.download_backend
            )
        from ..execution.cli import entrypoint

        return entrypoint(args)
    except KeyboardInterrupt:
        print(
            "Cancelled; inspect the existing ghwf state before resuming.",
            file=sys.stderr,
        )
        return 130
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(f"workflow: {error}", file=sys.stderr)
        return 1
