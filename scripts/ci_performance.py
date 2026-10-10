#!/usr/bin/env python3
"""Read-only GitHub CI timing collector; results never determine quality gates."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import subprocess

ANSI = re.compile(r"\x1b\[[0-9;]*m")
PHASES = {"report-export", "check-list", "remaining-checks", "all-checks"}


def timestamp(value):
    if not isinstance(value, str):
        return None
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timing timestamps must include a timezone")
    return parsed


def seconds(start, end):
    if start is None or end is None:
        return None
    value = (end - start).total_seconds()
    if value < 0:
        raise ValueError("Timing interval is negative")
    return value


def duration(value):
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|m|s)", value)
    if "".join(n + unit for n, unit in parts) != value:
        raise ValueError("Unrecognized native duration")
    return round(
        sum(float(n) * {"ms": 0.001, "s": 1, "m": 60}[unit] for n, unit in parts), 6
    )


def dagger_timings(log):
    """Extract explicit markers and selected native spans, not test verdicts."""
    if len(log.encode("utf-8")) > 8 * 1024 * 1024:
        raise ValueError("Dagger log exceeds 8 MiB")
    phases, spans, preparations, installations, seen = [], [], [], [], set()
    generation, connected = 0, False
    for raw in log.splitlines():
        line = ANSI.sub("", raw).lstrip("\ufeff")
        line = re.sub(r"^\d{4}-\d\d-\d\dT\S+\s+", "", line)
        if re.fullmatch(r"1\s*:\s*connect", line):
            if not connected:
                generation += 1
                connected = True
        if re.fullmatch(r"1\s*:\s*connect DONE \[[^]]+\]", line):
            connected = False
        marker = re.search(r"(?:^|\|\s+)CI_TIMING (\{.*\})$", line)
        if marker:
            data = json.loads(marker[1])
            if (
                set(data) != {"phase", "seconds", "exit_code"}
                or data["phase"] not in PHASES
            ):
                raise ValueError("Invalid performance marker")
            if (
                type(data["seconds"]) not in (int, float)
                or not 0 <= data["seconds"] < 86400
            ):
                raise ValueError("Invalid performance duration")
            if type(data["exit_code"]) is not int:
                raise ValueError("Invalid command exit")
            phases.append(data)
        match = re.fullmatch(
            r"(\d+)\s*:\s*(?:┆\s*)*(load workspace: \.|loading type definitions|fetch check information|load module: [a-z0-9-]+|EngineeringCi\.fixture(?:Lint|Format|FailureReport|PythonMatrix|QualityGates)) DONE \[([0-9.ms]+)\](?:.*)",
            line,
        )
        if match:
            key = (generation, *match.groups())
            if key not in seen:
                spans.append(
                    {
                        "session": generation,
                        "phase": match[2],
                        "seconds": duration(match[3]),
                    }
                )
                seen.add(key)
        match = re.fullmatch(
            r"(\d+)\s*:\s*(?:┆\s*)*\[[^]]+\] \| (Prepared|Installed) (\d+) packages in ([0-9.ms]+)",
            line,
        )
        if match:
            key = (generation, *match.groups())
            if key not in seen:
                (preparations if match[2] == "Prepared" else installations).append(
                    {
                        "session": generation,
                        "packages": int(match[3]),
                        "seconds": duration(match[4]),
                    }
                )
                seen.add(key)
    return {
        "commands": phases,
        "native_spans": spans,
        "uv_preparations_observed": preparations,
        "uv_installations_observed": installations,
        "note": "Overlapping/nested spans are not summed into wall time. Observations may be incomplete in dropped progress logs.",
    }


def summarize(run, jobs, log=""):
    active = [job for job in jobs if job.get("conclusion") != "skipped"]
    if not active or any(job.get("status") != "completed" for job in active):
        raise ValueError("Performance comparisons require completed jobs")
    rows = []
    for job in active:
        start, end = (
            timestamp(job.get("started_at")),
            timestamp(job.get("completed_at")),
        )
        if start is None or end is None:
            raise ValueError("Completed job timing is unavailable")
        created = timestamp(job.get("created_at"))
        rows.append(
            {
                "id": job["id"],
                "name": job["name"],
                "conclusion": job.get("conclusion"),
                "started_at": job["started_at"],
                "completed_at": job["completed_at"],
                "execution_seconds": seconds(start, end),
                "queue_or_scheduling_seconds": seconds(created, start),
                "steps": [
                    {
                        "name": s["name"],
                        "conclusion": s.get("conclusion"),
                        "seconds": seconds(
                            timestamp(s.get("started_at")),
                            timestamp(s.get("completed_at")),
                        ),
                    }
                    for s in job.get("steps", [])
                ],
            }
        )
    first = min(timestamp(j["started_at"]) for j in active)
    last = max(timestamp(j["completed_at"]) for j in active)
    critical = max(rows, key=lambda r: timestamp(r["completed_at"]))
    failed_steps = [
        timestamp(step["completed_at"])
        for job in active
        for step in job.get("steps", [])
        if step.get("conclusion") == "failure" and step.get("completed_at")
    ]
    return {
        "schema_version": 1,
        "run_id": run["id"],
        "workflow": run["name"],
        "event": run["event"],
        "sha": run["head_sha"],
        "url": run["html_url"],
        "run_attempt": run["run_attempt"],
        "conclusion": run.get("conclusion"),
        "queued_before_first_job_seconds": seconds(
            timestamp(run.get("created_at")), first
        ),
        "checks_execution_window_seconds": seconds(first, last),
        "submission_to_checks_complete_seconds": seconds(
            timestamp(run.get("created_at")), last
        ),
        "critical_finishing_job": critical["name"],
        "parallel_runner_seconds": sum(row["execution_seconds"] for row in rows),
        "first_failed_step_completion_seconds": seconds(
            timestamp(run.get("created_at")),
            min(failed_steps) if failed_steps else None,
        ),
        "jobs": rows,
        "dagger": dagger_timings(log),
        "limitations": [
            "Queue values include scheduling/dependency waits; absent job created_at is unknown.",
            "Completion uses final job timestamp, not mutable workflow updated_at.",
            "Parallel runner seconds describe resource use, not CI latency.",
            "Different test selections/versions/settings are not before/after samples.",
            "First failed step completion is an API observation; live diagnosis may arrive earlier.",
        ],
    }


def collect(repo, run_id, *, gh="gh", runner=subprocess.run):
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
        or type(run_id) is not int
        or run_id < 1
    ):
        raise ValueError("Invalid repository or run id")

    def get(route):
        result = runner(
            [gh, "api", "--method", "GET", "--paginate", "--slurp", route],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("Read-only GitHub metadata request failed")
        if len(result.stdout.encode("utf-8")) > 8 * 1024 * 1024:
            raise ValueError("GitHub metadata exceeds 8 MiB")
        data = json.loads(result.stdout)
        return data if isinstance(data, list) else [data]

    prefix = f"repos/{repo}/actions/runs/{run_id}"
    run_pages = get(prefix)
    job_pages = get(prefix + "/jobs?per_page=100")
    if len(run_pages) != 1 or len(job_pages) > 20:
        raise ValueError("Unexpected metadata pagination")
    jobs = [job for page in job_pages for job in page["jobs"]]
    if not jobs or len(jobs) != job_pages[0]["total_count"]:
        raise ValueError("Job metadata is incomplete")
    return run_pages[0], jobs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--run", type=int, required=True)
    parser.add_argument("--gh", default="gh")
    parser.add_argument("--dagger-log", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    run, jobs = collect(args.repo, args.run, gh=args.gh)
    log = ""
    if args.dagger_log:
        with args.dagger_log.open("rb") as stream:
            blob = stream.read(8 * 1024 * 1024 + 1)
        if len(blob) > 8 * 1024 * 1024:
            raise ValueError("Dagger log exceeds 8 MiB")
        log = blob.decode("utf-8")
    report = summarize(run, jobs, log)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix(args.out.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.out)
    print(
        f"Checks window: {report['checks_execution_window_seconds']}s; finishing job: {report['critical_finishing_job']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
