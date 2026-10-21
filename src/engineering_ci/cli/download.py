"""Query and download an explicitly selected existing Run with native gh."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ..github_cli import ApiError, GitHub, positive_id, repository


def api(repo, path):
    return GitHub(repo)(path)


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


def interactive(repo, *, ask=input):
    workflow = choose(
        pages(repo, "actions/workflows", "workflows"),
        "Workflows",
        lambda w: f"{w['name']} ({w['id']})",
        ask,
    )
    runs = api(repo, f"actions/workflows/{workflow['id']}/runs?per_page=30")[
        "workflow_runs"
    ]
    return choose(
        runs,
        "Runs",
        lambda r: f"{r['id']} {r['head_branch']} {r['status']}/{r.get('conclusion')}",
        ask,
    )["id"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", type=positive_id)
    group.add_argument("--interactive", action="store_true")
    parser.add_argument("--repo")
    parser.add_argument("--out", type=Path, default=Path("workflow-artifacts"))
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--save-log", action="store_true")
    parser.add_argument("--no-artifacts", action="store_true")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--timeout", type=positive_id, default=2700)
    parser.add_argument("--json", action="store_true")
    if argv == [] or argv is None and len(sys.argv) == 1:
        parser.print_help()
        return 0
    options = parser.parse_args(argv)
    if options.interactive and (not sys.stdin.isatty() or options.json):
        parser.error("Interactive selection requires a terminal; agents use --run")
    if options.no_artifacts and options.artifact:
        parser.error("--artifact conflicts with --no-artifacts")
    result = {
        "operation": "download",
        "repository": options.repo,
        "run_id": options.run,
        "result": "unavailable",
        "artifact_scope": "run",
        "next_commands": [],
    }
    code = 1
    try:
        repo = repository(options.repo)
        result["repository"] = repo
        client = GitHub(repo)
        run_id = options.run or interactive(repo)
        result.update(
            run_id=run_id, run_url=f"https://github.com/{repo}/actions/runs/{run_id}"
        )
        run = client.run_info(run_id)
        result.update(
            attempt=run["run_attempt"],
            commit_sha=run["head_sha"],
            conclusion=run.get("conclusion"),
        )
        watch_code = (
            client.watch(run_id, timeout=options.timeout) if options.wait else 0
        )
        try:
            current = client.run_info(run_id) if options.wait else run
        except (ApiError, OSError, ValueError, KeyError, TypeError):
            if watch_code:
                raise ApiError(exit_code=watch_code) from None
            raise
        if (
            current["head_sha"] != run["head_sha"]
            or current["run_attempt"] != run["run_attempt"]
        ):
            raise ValueError("Run changed attempt or commit during collection")
        if current["status"] != "completed":
            result["reason"] = "Run is not complete; use --wait for this Run"
            code = watch_code or 3
        else:
            directory = options.out / str(run_id)
            directory.mkdir(parents=True, exist_ok=True)
            result["output"] = str(directory)
            if options.save_log:
                target = directory / "run.log"
                if target.exists():
                    raise ValueError("run.log exists; choose an empty --out directory")
                with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
                    partial = Path(stream.name)
                    try:
                        client.run_log(run_id, run["run_attempt"], stream)
                    except BaseException:
                        stream.close()
                        partial.unlink(missing_ok=True)
                        raise
                try:
                    os.link(partial, target)  # atomic creation; never overwrite a log
                finally:
                    partial.unlink(missing_ok=True)
                result["log"] = str(target)
            if not options.no_artifacts:
                client.run_download(run_id, directory / "artifacts", options.artifact)
            result.update(result="downloaded", conclusion=current.get("conclusion"))
            code = watch_code
    except ApiError as error:
        result.update(reason=str(error), http_status=error.status)
        code = error.exit_code
    except KeyboardInterrupt:
        result["reason"] = "Interrupted; query/download the same Run"
        code = 130
    except subprocess.TimeoutExpired:
        result["reason"] = "Native gh timed out"
        code = 124
    except OSError as error:
        result["reason"] = (
            "Native tool or file unavailable (" + type(error).__name__ + ")"
        )
        code = 2
    except (ValueError, KeyError, TypeError) as error:
        result["reason"] = (
            str(error) if isinstance(error, ValueError) else "Invalid GitHub metadata"
        )
        code = 1
    result["exit_code"] = code
    if code and result.get("run_url"):
        command = f'uv run --locked python scripts/download.py --repo {result["repository"]} --run {result["run_id"]} --out "{options.out}"'
        if options.save_log and not result.get("log"):
            command += " --save-log"
        if options.no_artifacts:
            command += " --no-artifacts"
        for name in options.artifact:
            command += " --artifact " + json.dumps(name)
        result["next_commands"] = [command]
    if options.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"Run {result['run_id']}: {result['result']}; {result.get('run_url', '')}; conclusion: {result.get('conclusion') or '-'}"
        )
        if result.get("reason"):
            print(result["reason"])
        for command in result["next_commands"]:
            print("Continue: " + command)
    return code
