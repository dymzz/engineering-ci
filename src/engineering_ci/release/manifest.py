"""Record existing manual-release evidence; never execute tests or infer verdicts."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import re
import subprocess
import sys
from pathlib import Path

from ..requirements import digest as requirement_digest
from ..requirements import digest_records as requirement_digest_records
from ..requirements import records as requirement_records
from . import RELEASE_CONFIG
from .registry import (
    ARTIFACT_NAMES,
    ARTIFACT_OUTPUTS,
    CHECKS,
    DAGGER_EXECUTION_CHECKS,
    DAGGER_POLICY_CHECKS,
    PERMITTED_REPLACED_CHECK,
    REQUIRED_TOOLS,
    SCOPES,
    STATES,
    TOOLS,
    resolve_historical_contract,
)
from .version import ROOT

REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA = re.compile(r"^[0-9a-f]{40}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
TOOL_VERSION = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[a-z0-9.+-]*)$")
# These are the actual v0.4.8 release jobs/steps, not a new execution plan.

# The fields a recorded check result and a re-derived one share. A recorded
# Dagger policy check carries one extra field, so the comparison uses the
# common ones instead of the whole record.
RESULT_FIELDS = (
    "required",
    "status",
    "job",
    "job_id",
    "step",
    "job_conclusion",
    "step_conclusion",
)


def dagger_cli_version(output):
    """Read only the version from legacy or 1.0 CLI output."""
    version = r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?"
    legacy = re.fullmatch(rf"dagger v({version})(?: [^\r\n]*)?", output.strip())
    if legacy:
        return legacy[1]
    fields = [
        line.strip()
        for line in output.splitlines()
        if line.strip().startswith("version:")
    ]
    match = (
        re.fullmatch(rf"version:[ \t]+v({version})", fields[0])
        if len(fields) == 1
        else None
    )
    if not match:
        raise ValueError("Dagger CLI version unavailable")
    return match[1]


def artifact_ids(needs):
    result = {}
    for name, (job, output) in ARTIFACT_OUTPUTS.items():
        value = needs.get(job, {}).get("outputs", {}).get(output, "")
        if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
            result[name] = int(value)
    return result


def workflow_jobs(api, run_id, attempt, head):
    jobs = pages(api, f"actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs")
    if any(
        job.get("run_id") != run_id
        or job.get("run_attempt") != attempt
        or job.get("head_sha") != head
        for job in jobs
    ):
        raise ValueError("Job does not match Run, attempt and commit")
    return jobs


def artifact_metadata(
    api, repository, name, asset_id, head, run_id, *, allow_expired=False
):
    if name not in ARTIFACT_NAMES or type(asset_id) is not int or asset_id <= 0:
        raise ValueError("Invalid native Artifact ID")
    item = api(f"actions/artifacts/{asset_id}")
    expected_url = (
        f"https://api.github.com/repos/{repository}/actions/artifacts/{asset_id}"
    )
    if (
        item["id"] != asset_id
        or item["name"] != name
        or item.get("workflow_run", {}).get("head_sha") != head
        or item.get("workflow_run", {}).get("id") != run_id
        or item.get("url") != expected_url
        or type(item.get("expired")) is not bool
    ):
        raise ValueError("Artifact identity or commit mismatch")
    if item.get("expired") and not allow_expired:
        return None
    return {
        "name": name,
        "id": asset_id,
        "digest": item.get("digest"),
        "url": item["url"],
    }


def encode(value):
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encode(value), encoding="utf-8")


def tools(scope):
    records = []
    if scope == "core":
        records.append(
            {"name": "python", "version": platform.python_version(), "scope": scope}
        )
        for name in ("pytest", "pytest-cov"):
            records.append(
                {
                    "name": name,
                    "version": importlib.metadata.version(name),
                    "scope": scope,
                }
            )
    for name in ("uv",) if scope == "core" else ("uv", "ruff"):
        command = (
            ["uv", "--version"]
            if name == "uv"
            else ["uv", "run", "--no-sync", "ruff", "--version"]
        )
        output = subprocess.check_output(
            command, text=True, encoding="utf-8", timeout=60
        )
        match = re.fullmatch(rf"{name} (\S+)(?: .*)?\s*", output.strip())
        if not match:
            raise ValueError("Tool version unavailable")
        records.append({"name": name, "version": match[1], "scope": scope})
    return clean_tools(records)


def clean_tools(records):
    if not isinstance(records, list):
        raise TypeError("Invalid tool version evidence")
    result = []
    for item in records:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "version", "scope"}
            or item["name"] not in TOOLS
            or item["scope"] not in SCOPES
            or (item["scope"], item["name"]) not in REQUIRED_TOOLS
            or not isinstance(item["version"], str)
            or not TOOL_VERSION.fullmatch(item["version"])
        ):
            raise ValueError("Invalid tool version evidence")
        result.append(dict(item))
    if len({(item["scope"], item["name"]) for item in result}) != len(result):
        raise ValueError("Duplicate tool version evidence")
    return sorted(result, key=lambda item: (item["scope"], item["name"]))


def facts(directory):
    from .publishing import read_json, require_evidence

    require_evidence(directory)
    records = read_json(directory / "core/tool-versions.json")
    records += read_json(directory / "ruff/tool-versions.json")
    dagger = (
        (directory / "pytest/dagger-version.txt").read_text(encoding="utf-8").strip()
    )
    records.append(
        {"name": "dagger", "version": dagger_cli_version(dagger), "scope": "dagger_cli"}
    )
    security = read_json(directory / "security/report.json")
    records.append(
        {
            "name": "osv-scanner",
            "version": security["scanner"]["version"],
            "scope": "root_lock",
        }
    )
    # The enclosing Job succeeding does not prove that every required native
    # check ran. The ledger names the set that its success covers, so the
    # required set is compared against the ledger instead.
    ledger = dagger_ledger(directory)
    if ledger["replaced"] != PERMITTED_REPLACED_CHECK:
        raise ValueError(
            f"The replaced Dagger check must be {PERMITTED_REPLACED_CHECK!r}, "
            f"got {ledger['replaced']!r}"
        )
    if ledger["replaced"] not in ledger["discovered"]:
        raise ValueError("The replaced Dagger check was never discovered")
    if ledger["ran"] != ledger["expected_ran"]:
        raise ValueError(
            "Dagger execution ledger disagrees with the discovered check listing"
        )
    executed = set(ledger["ran"]) | {ledger["replaced"]}
    if not set(DAGGER_EXECUTION_CHECKS) <= executed:
        raise ValueError("Required Dagger execution evidence missing")
    if not set(DAGGER_POLICY_CHECKS.values()) <= executed:
        raise ValueError("Existing Dagger policy execution evidence missing")
    records = clean_tools(records)
    if {(item["scope"], item["name"]) for item in records} != REQUIRED_TOOLS:
        raise ValueError("Required tool version evidence missing")
    return {"tools": records, "dagger_checks": sorted(executed)}


def pages(api, path, key):
    result = []
    for page in range(1, 21):
        response = api(f"{path}?per_page=100&page={page}")
        entries = response[key]
        result.extend(entries)
        if len(entries) < 100:
            return result
    raise ValueError("GitHub metadata pagination exceeds limit")


def run_context(api, repository, head, run_id, attempt, *, publication=False):
    if not REPOSITORY.fullmatch(repository) or not SHA.fullmatch(head):
        raise ValueError("Invalid release identity")
    if (
        type(run_id) is not int
        or run_id <= 0
        or type(attempt) is not int
        or attempt <= 0
    ):
        raise ValueError("Invalid workflow identity")
    run = api(f"actions/runs/{run_id}/attempts/{attempt}")
    expected_url = f"https://github.com/{repository}/actions/runs/{run_id}"
    if (
        run["id"] != run_id
        or run["run_attempt"] != attempt
        or run["head_sha"] != head
        or run["event"] != "workflow_dispatch"
        or run["repository"]["full_name"] != repository
        or run["html_url"] != expected_url
        or run["path"].split("@")[0]
        != f".github/workflows/{RELEASE_CONFIG['workflow']}"
        or (publication and run.get("head_branch") != RELEASE_CONFIG["branch"])
    ):
        raise ValueError("Workflow does not match verified release identity")
    return {
        "run_id": run_id,
        "run_attempt": attempt,
        "run_url": expected_url,
        "event": "workflow_dispatch",
        "path": f".github/workflows/{RELEASE_CONFIG['workflow']}",
    }


def check_record(jobs, name, step):
    matches = [job for job in jobs if job["name"] == name]
    result = {
        "required": True,
        "status": "unavailable",
        "job": name,
        "job_id": None,
        "step": step,
        "job_conclusion": None,
        "step_conclusion": None,
    }
    if len(matches) != 1:
        return result
    job = matches[0]
    result["job_id"] = job["id"]
    result["job_conclusion"] = job.get("conclusion")
    steps = [item for item in job.get("steps", []) if item["name"] == step]
    if len(steps) != 1:
        return result
    result["step_conclusion"] = steps[0].get("conclusion")
    if job.get("status") != "completed" or steps[0].get("status") != "completed":
        return result
    # A successful report step cannot mask a failed original execution job.
    conclusions = [job.get("conclusion"), steps[0].get("conclusion")]
    result["status"] = next(
        (
            STATES.get(value, "unavailable")
            for value in conclusions
            if value != "success"
        ),
        "passed",
    )
    return result


def effective_requirements(value: dict) -> list[dict]:
    """The requirement set this manifest says it covers.

    A manifest written before the requirement set was recorded is validated
    against the authentic historical contract that was in force when it was
    produced. It carries no coverage scope, which a candidate verification
    cannot accept.
    """
    recorded = value.get("requirements")
    if isinstance(recorded, dict) and isinstance(recorded.get("checks"), list):
        return recorded["checks"]
    rel = value.get("release", {})
    wf = value.get("workflow", {})
    contract = resolve_historical_contract(
        rel.get("tag"),
        rel.get("commit_sha"),
        wf.get("run_id"),
        wf.get("run_attempt"),
        value.get("repository"),
    )
    if contract is None:
        raise ValueError(
            f"Historical manifest lacks authentic release contract for tag {rel.get('tag')!r}"
        )
    result = []
    for key, (job, step) in sorted(contract["checks"].items()):
        result.append(
            {
                "key": key,
                "job": job,
                "step": step,
                "scope": "",
                "dagger_check": "",
            }
        )
    for key, dagger_check in sorted(contract["dagger_policy_checks"].items()):
        result.append(
            {
                "key": key,
                "job": contract["checks"]["dagger"][0],
                "step": contract["checks"]["dagger"][1],
                "scope": "",
                "dagger_check": dagger_check,
            }
        )
    return result


def verified_results(jobs, value):
    """Compare the recorded check results with the original Actions results.

    The manifest's own recorded job and step name the evidence, so this
    comparison never depends on the requirement set currently in force. A
    published release therefore stays verifiable after the requirements
    change, and a candidate is compared against the sources it was actually
    verified against.
    """
    for record in value["checks"].values():
        if record.get("required") is not True:
            continue
        current = check_record(jobs, record["job"], record["step"])
        if {field: current[field] for field in RESULT_FIELDS} != {
            field: record[field] for field in RESULT_FIELDS
        }:
            raise ValueError("Manifest does not match original Actions results")


def dagger_ledger(directory: Path) -> dict:
    """Read the native Dagger execution ledger the reusable Workflow wrote.

    Three independently produced facts: the checks the module offered, the
    checks that were left to run, and the one check a report execution
    replaced. A successful enclosing Job does not prove that the required
    set was covered; the ledger names the set that its success covers.
    """
    helper = str(ROOT / ".dagger/src/engineering_ci")
    if helper not in sys.path:
        sys.path.insert(0, helper)
    from pytest_ci import discovered_checks, remaining_checks

    listing = (directory / "pytest/dagger-checks.txt").read_text(encoding="utf-8")
    replaced = (
        (directory / "pytest/replaced-check.txt").read_text(encoding="utf-8").strip()
    )
    return {
        "discovered": list(discovered_checks(listing)),
        "ran": (
            (directory / "pytest/remaining-checks.txt")
            .read_text(encoding="utf-8")
            .splitlines()
        ),
        "replaced": replaced,
        "expected_ran": remaining_checks(listing, replaced),
    }


def manifest(api, repository, tag, head, run_id, attempt, mode, evidence, *, ids=None):
    if not tag.startswith("v") or not VERSION.fullmatch(tag[1:]):
        raise ValueError("Invalid release version")
    workflow = run_context(
        api, repository, head, run_id, attempt, publication=mode == "publication"
    )
    jobs = workflow_jobs(api, run_id, attempt, head)
    checks = {key: check_record(jobs, *source) for key, source in CHECKS.items()}
    for key, check in DAGGER_POLICY_CHECKS.items():
        record = dict(checks["dagger"])
        record["dagger_check"] = check
        if record["status"] == "passed" and (
            evidence is None or check not in evidence["dagger_checks"]
        ):
            record["status"] = "unavailable"
        checks[key] = record
    checks["core_coverage_threshold"] = {
        "required": False,
        "status": "not_enabled",
        "reason": "Core coverage is exported; no core coverage threshold is configured.",
    }
    checks["consumer_failure_probe"] = {
        "required": False,
        "status": "not_enabled",
        "reason": "The smoke-only Ruff rejection probe is disabled in core releases.",
    }
    records = clean_tools(evidence["tools"]) if evidence is not None else []
    if (
        checks["evidence"]["status"] == "passed"
        and {(item["scope"], item["name"]) for item in records} != REQUIRED_TOOLS
    ):
        checks["evidence"]["status"] = "unavailable"
    artifacts = []
    for name, asset_id in (ids or {}).items():
        record = artifact_metadata(api, repository, name, asset_id, head, run_id)
        if record is not None:
            artifacts.append(record)
    if (
        checks["evidence"]["status"] == "passed"
        and {item["name"] for item in artifacts} != ARTIFACT_NAMES
    ):
        checks["evidence"]["status"] = "unavailable"
    result = {
        "schema_version": 1,
        "repository": repository,
        "release": {
            "version": tag[1:],
            "tag": tag,
            "commit_sha": head,
            "release_url": None,
        },
        "workflow": workflow,
        "mode": mode,
        "checks": checks,
        "requirements": {
            "schema_version": 1,
            "digest": requirement_digest(),
            "checks": requirement_records(),
        },
        "tools": records,
        "artifacts": sorted(artifacts, key=lambda item: (item["name"], item["id"])),
        "result": "passed"
        if all(
            item["status"] == "passed" for item in checks.values() if item["required"]
        )
        else "failed",
    }
    validate(result, repository, tag, head)
    return result


def validate(value, repository, tag, head, *, published=False):
    """Validate release manifest structure, release metadata and cross-field facts.

    Current release candidate manifests must satisfy current requirements, all
    authoritative checks and all 5 artifacts. Historical published manifests
    omitting the `requirements` field are verified under their authentic legacy
    4-artifact and 12-check contract, without trusting arbitrary artifact JSON.
    """
    if not REPOSITORY.fullmatch(repository):
        raise ValueError("Invalid Manifest repository")
    expected = {
        "schema_version",
        "repository",
        "release",
        "workflow",
        "mode",
        "checks",
        "tools",
        "artifacts",
        "result",
    }
    if (
        set(value) not in (expected, expected | {"requirements"})
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["repository"] != repository
    ):
        raise ValueError("Manifest schema or repository mismatch")
    identity = value["release"]
    if (
        set(identity) != {"version", "tag", "commit_sha", "release_url"}
        or not SHA.fullmatch(head)
        or not tag.startswith("v")
        or not VERSION.fullmatch(tag[1:])
        or identity["version"] != tag[1:]
        or identity["tag"] != tag
        or identity["commit_sha"] != head
    ):
        raise ValueError("Manifest version or SHA mismatch")
    workflow = value["workflow"]
    if (
        set(workflow) != {"run_id", "run_attempt", "run_url", "event", "path"}
        or type(workflow["run_id"]) is not int
        or workflow["run_id"] <= 0
        or type(workflow["run_attempt"]) is not int
        or workflow["run_attempt"] <= 0
        or workflow["run_url"]
        != f"https://github.com/{repository}/actions/runs/{workflow['run_id']}"
        or workflow["event"] != "workflow_dispatch"
        or workflow["path"] != f".github/workflows/{RELEASE_CONFIG['workflow']}"
        or value["mode"] not in {"validation", "publication"}
    ):
        raise ValueError("Manifest workflow identity mismatch")
    url = identity["release_url"]
    if url is not None and not re.fullmatch(
        rf"https://api\.github\.com/repos/{re.escape(repository)}/releases/[1-9][0-9]*",
        url,
    ):
        raise ValueError("Invalid actual Release URL")
    # The manifest carries the requirement set it was verified against. A
    # manifest written before that field existed is still valid evidence for
    # the requirements it names, but it cannot establish coverage of today's
    # requirements; the currency check belongs to the candidate path.
    recorded = value.get("requirements")
    if recorded is not None:
        items = recorded.get("checks") if isinstance(recorded, dict) else None
        if (
            not isinstance(recorded, dict)
            or set(recorded) != {"schema_version", "digest", "checks"}
            or recorded["schema_version"] != 1
            or not isinstance(items, list)
            or any(
                not isinstance(item, dict)
                or set(item) != {"key", "job", "step", "scope", "dagger_check"}
                or not isinstance(item["key"], str)
                or not isinstance(item["job"], str)
                or not isinstance(item["step"], str)
                or not isinstance(item["scope"], str)
                or not isinstance(item["dagger_check"], str)
                for item in items
            )
            or len(items) != len({item["key"] for item in items})
            or recorded["digest"] != requirement_digest_records(items)
        ):
            raise ValueError("Invalid recorded requirement set")
        expected_artifacts = ARTIFACT_NAMES
    else:
        rel = value.get("release", {})
        wf = value.get("workflow", {})
        contract = resolve_historical_contract(
            rel.get("tag"),
            rel.get("commit_sha"),
            wf.get("run_id"),
            wf.get("run_attempt"),
            repository,
        )
        if contract is None:
            raise ValueError(
                f"Historical manifest lacks authentic release contract for tag {rel.get('tag')!r}"
            )
        expected_artifacts = contract["artifacts"]
    requirements = {item["key"]: item for item in effective_requirements(value)}
    required = set(requirements)
    optional = {"core_coverage_threshold", "consumer_failure_probe"}
    if set(value["checks"]) != required | optional:
        raise ValueError("Missing or unexpected manifest checks")
    for key, record in value["checks"].items():
        if key in optional:
            if record.get("required") is not False or record != {
                "required": False,
                "status": "not_enabled",
                "reason": (
                    "Core coverage is exported; no core coverage threshold is configured."
                    if key == "core_coverage_threshold"
                    else "The smoke-only Ruff rejection probe is disabled in core releases."
                ),
            }:
                raise ValueError("Invalid optional check")
            continue
        requirement = requirements[key]
        fields = {
            "required",
            "status",
            "job",
            "job_id",
            "step",
            "job_conclusion",
            "step_conclusion",
        }
        if requirement["dagger_check"]:
            fields.add("dagger_check")
            if record.get("dagger_check") != requirement["dagger_check"]:
                raise ValueError("Invalid Dagger check")
        if (
            set(record) != fields
            or record["required"] is not True
            or (record["job"], record["step"])
            != (requirement["job"], requirement["step"])
            or record["status"] not in set(STATES.values())
            or record["job_conclusion"] not in {None, *STATES}
            or record["step_conclusion"] not in {None, *STATES}
            or (
                record["job_id"] is not None
                and (type(record["job_id"]) is not int or record["job_id"] <= 0)
            )
        ):
            raise ValueError("Invalid required check")
        if record["status"] == "passed" and (
            record["job_conclusion"] != "success"
            or record["step_conclusion"] != "success"
            or record["job_id"] is None
        ):
            raise ValueError("Passed check lacks original successful execution")
    if clean_tools(value["tools"]) != value["tools"]:
        raise ValueError("Invalid or unsorted tool evidence")
    if (
        value["result"] == "passed"
        and {(item["scope"], item["name"]) for item in value["tools"]} != REQUIRED_TOOLS
    ):
        raise ValueError("Passed manifest lacks required tool evidence")
    for item in value["artifacts"]:
        if (
            set(item) != {"name", "id", "digest", "url"}
            or item["name"] not in expected_artifacts
            or type(item["id"]) is not int
            or item["id"] <= 0
            or item["url"]
            != f"https://api.github.com/repos/{repository}/actions/artifacts/{item['id']}"
            or (
                item["digest"] is not None
                and not re.fullmatch(r"sha256:[0-9a-f]{64}", item["digest"])
            )
        ):
            raise ValueError("Invalid artifact metadata")
    if len({item["name"] for item in value["artifacts"]}) != len(
        value["artifacts"]
    ) or len({item["id"] for item in value["artifacts"]}) != len(value["artifacts"]):
        raise ValueError("Duplicate artifact references")
    passed = all(value["checks"][key]["status"] == "passed" for key in required)
    if passed and {item["name"] for item in value["artifacts"]} != expected_artifacts:
        raise ValueError("Passed manifest lacks required artifact references")
    if value["result"] != ("passed" if passed else "failed"):
        raise ValueError("Manifest aggregate does not match checks")
    if published and (
        not passed
        or value["mode"] != "publication"
        or url is None
        or not value["tools"]
    ):
        raise ValueError("Publication requires complete passed evidence")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("tools", "facts"))
    parser.add_argument("--scope", choices=("core", "ruff"))
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "tools":
            if args.scope is None:
                raise ValueError("Tool scope required")
            value = tools(args.scope)
        else:
            if args.directory is None:
                raise ValueError("Evidence directory required")
            value = facts(args.directory)
        write(args.output, value)
        return 0
    except (
        ValueError,
        TypeError,
        KeyError,
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
    ):
        parser.exit(
            1, "Release evidence unavailable; inspect original job diagnostics.\n"
        )


if __name__ == "__main__":
    raise SystemExit(main())
