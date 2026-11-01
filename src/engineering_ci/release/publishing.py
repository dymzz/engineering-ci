"""Small guards for the manual release workflow; no test execution or scheduling."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from ..github_cli import ApiError, GitHub
from ..github_cli import require_api_success as _require_api_success
from . import RELEASE_CONFIG
from .version import ROOT, checked_version, normalize

require_api_success = _require_api_success

SHA = re.compile(r"^[0-9a-f]{40}$")


def optional(api, path):
    try:
        return api(path)
    except ApiError as exc:
        if exc.status == 404:
            return None
        raise


def tag_commit(api, tag):
    ref = optional(api, f"git/ref/tags/{tag}")
    if ref is None:
        return None
    obj = ref["object"]
    for _ in range(8):
        if obj["type"] == "commit" and SHA.fullmatch(obj["sha"]):
            return obj["sha"]
        if obj["type"] != "tag" or not SHA.fullmatch(obj["sha"]):
            break
        obj = api(f"git/tags/{obj['sha']}")["object"]
    raise ValueError("Tag does not resolve to a commit")


def request(environment, head):
    version = normalize(environment["RELEASE_VERSION"])
    if version != checked_version():
        raise ValueError("Requested version does not match all four metadata fields")
    if not SHA.fullmatch(head) or head != environment["GITHUB_SHA"]:
        raise ValueError("Checkout differs from workflow candidate SHA")
    publish = environment["RELEASE_PUBLISH"]
    if publish not in ("true", "false"):
        raise ValueError("Publication must be explicitly true or false")
    if publish == "true" and (
        environment["GITHUB_REF"] != f"refs/heads/{RELEASE_CONFIG['branch']}"
        or environment["GITHUB_EVENT_NAME"] != "workflow_dispatch"
        or environment["RELEASE_SCENARIO"] != "pass"
    ):
        raise ValueError("Publication requires a manual main run with pass scenario")
    return "v" + version, publish == "true"


def candidate(api, tag, head, publish):
    if not publish:
        return None
    if api(f"git/ref/heads/{RELEASE_CONFIG['branch']}")["object"]["sha"] != head:
        raise ValueError("main advanced; refusing publication")
    existing = tag_commit(api, tag)
    if existing is not None and existing != head:
        raise ValueError("Tag conflict; refusing to overwrite")
    release = find_release(api, tag)
    if release is not None and (
        existing != head or release.get("tag_name") != tag or release.get("prerelease")
    ):
        raise ValueError("Existing Release does not match the validated final tag")
    return release


def find_release(api, tag):
    release = optional(api, f"releases/tags/{tag}")
    if release is not None:
        return release
    # The by-tag endpoint may omit drafts; authenticated list includes them.
    for page in range(1, 21):
        items = api(f"releases?per_page=100&page={page}")
        matches = [item for item in items if item["tag_name"] == tag]
        if len(matches) > 1:
            raise ValueError("Conflicting Releases for tag")
        if matches:
            return matches[0]
        if len(items) < 100:
            return None
    raise ValueError("Release pagination exceeds limit")


def require_jobs(needs, head, publishing=False):
    from .registry import RELEASE_DEPENDENCIES

    expected = RELEASE_DEPENDENCIES["publish" if publishing else "verified"]
    if not isinstance(needs, dict) or not expected <= needs.keys():
        raise ValueError("Missing required verification jobs")
    if any(
        not isinstance(item, dict) or item.get("result") != "success"
        for item in needs.values()
    ):
        raise ValueError("Every required job must have actually succeeded")
    if needs["candidate"].get("outputs", {}).get("sha") != head:
        raise ValueError("Verified candidate SHA mismatch")


def published_evidence(api, value, repository, tag, head):
    from .manifest import (
        CHECKS,
        artifact_metadata,
        check_record,
        run_context,
        validate,
        workflow_jobs,
    )

    validate(value, repository, tag, head)
    if (
        value["mode"] != "publication"
        or value["result"] != "passed"
        or not value["tools"]
    ):
        raise ValueError("Publication requires complete passed evidence")
    workflow = value["workflow"]
    run_context(
        api,
        repository,
        head,
        workflow["run_id"],
        workflow["run_attempt"],
        publication=True,
    )
    jobs = workflow_jobs(api, workflow["run_id"], workflow["run_attempt"], head)
    for key, source in CHECKS.items():
        if check_record(jobs, *source) != value["checks"][key]:
            raise ValueError("Manifest does not match original Actions results")
    for record in value["artifacts"]:
        current = artifact_metadata(
            api,
            repository,
            record["name"],
            record["id"],
            head,
            workflow["run_id"],
            allow_expired=True,
        )
        if record["digest"] is not None and record["digest"] != current["digest"]:
            raise ValueError("Manifest Artifact digest mismatch")


def release_asset(api, release, recovery_head=None):
    assets = []
    for page in range(1, 21):
        items = api(f"releases/{release['id']}/assets?per_page=100&page={page}")
        assets.extend(item for item in items if item["name"] == "release-manifest.json")
        if len(items) < 100:
            break
    else:
        raise ValueError("Release asset pagination exceeds limit")
    if len(assets) > 1:
        raise ValueError("Conflicting Manifest assets")
    if assets:
        asset = assets[0]
        if type(asset.get("id")) is not int or asset["id"] <= 0:
            raise ValueError("Invalid Manifest asset identity")
        if (
            recovery_head is not None
            and release.get("draft") is True
            and asset.get("state") == "starter"
            and asset.get("size") == 0
        ):
            live = candidate(api, release["tag_name"], recovery_head, True)
            if (
                live is None
                or live["id"] != release["id"]
                or live.get("draft") is not True
            ):
                raise ValueError("Cannot repair an asset outside the validated Draft")
            current = api(f"releases/assets/{asset['id']}")
            if (
                current.get("id") != asset["id"]
                or current.get("name") != "release-manifest.json"
                or current.get("state") != "starter"
                or current.get("size") != 0
            ):
                raise ValueError("Empty Manifest asset changed before repair")
            api(f"releases/assets/{asset['id']}", "DELETE")
            return None
        if (
            asset.get("state") != "uploaded"
            or not 0 < asset.get("size", 0) <= 1024 * 1024
        ):
            raise ValueError("Manifest upload incomplete")
        return api.download(asset["id"])
    return None


def release_url(release, repository):
    # The resource URL is stable when a Draft's browser URL changes at publication.
    actual = release["url"]
    if actual != f"https://api.github.com/repos/{repository}/releases/{release['id']}":
        raise ValueError("Release resource URL mismatch")
    return actual


def publish_release(api, tag, head, manifest, path):
    from .manifest import write

    repository = manifest["repository"]
    # Validate identity and original Actions results before any publication write.
    published_evidence(api, manifest, repository, tag, head)
    proposed = json.loads(json.dumps(manifest))
    release = candidate(api, tag, head, True)
    if release is not None and not release["draft"]:
        saved = release_asset(api, release)
        if saved is None or saved["release"]["release_url"] != release_url(
            release, repository
        ):
            raise ValueError("Existing final Release lacks matching Manifest")
        published_evidence(api, saved, repository, tag, head)
        return release["html_url"], "already-published"
    if tag_commit(api, tag) is None:
        # Create-only API: never PATCH, force-push, or rewrite an existing tag.
        try:
            api("git/refs", "POST", {"ref": f"refs/tags/{tag}", "sha": head})
        except ApiError as exc:
            if exc.status not in (409, 422) or tag_commit(api, tag) != head:
                raise
    if tag_commit(api, tag) != head:
        raise ValueError("Published tag SHA mismatch")
    release = candidate(api, tag, head, True)
    if release is None:
        release = api(
            "releases",
            "POST",
            {
                "tag_name": tag,
                "target_commitish": head,
                "generate_release_notes": True,
                "draft": True,
                "prerelease": False,
            },
        )
    proposed["release"]["release_url"] = release_url(release, repository)
    published_evidence(api, proposed, repository, tag, head)
    saved = release_asset(api, release, recovery_head=head)
    if saved is None:
        if path.name != "release-manifest.json":
            raise ValueError("Expected release-manifest.json asset filename")
        write(path, proposed)
        api.upload(tag, path)
        saved = release_asset(api, release)
        if saved != proposed:
            raise ValueError("Uploaded Manifest does not match verified content")
    else:
        # Preserve a prior valid attempt's evidence instead of overwriting its asset.
        if saved["release"]["release_url"] != release_url(release, repository):
            raise ValueError("Draft Manifest Release URL mismatch")
        published_evidence(api, saved, repository, tag, head)
    live = candidate(api, tag, head, True)
    if live is None or live["id"] != release["id"] or live.get("draft") is not True:
        raise ValueError("Draft changed before final publication")
    if tag_commit(api, tag) != head:
        raise ValueError("Final tag SHA mismatch")
    release = api(f"releases/{release['id']}", "PATCH", {"draft": False})
    if (
        tag_commit(api, tag) != head
        or release["draft"]
        or release["tag_name"] != tag
        or release_url(release, repository) != saved["release"]["release_url"]
    ):
        raise ValueError("Final Release identity mismatch")
    return release["html_url"], "published"


def read_json(path):
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Release evidence exceeds limit")
    return json.loads(path.read_text(encoding="utf-8"))


def require_evidence(directory):
    # Reuse the existing raw exit/JUnit gate, not a new report verdict.
    sys.path.insert(0, str(ROOT / ".dagger/src/engineering_ci"))
    from pytest_ci import execution_exit_code
    from pytest_report import summarize_pytest

    pytest = directory / "pytest"
    if execution_exit_code(pytest) != 0:
        raise ValueError("Original pytest execution failed")
    report = read_json(pytest / "report.json")
    parsed = summarize_pytest((pytest / "junit.xml").read_text(encoding="utf-8"), 0, "")
    if (
        not parsed["report_available"]
        or parsed["status"] != "passed"
        or report.get("report_available") is not True
        or report.get("counts") != parsed["counts"]
        or report.get("status") != "passed"
        or (pytest / "fixture-invocations.txt").read_text().strip() != "1"
        or not (pytest / "summary.md").read_text(encoding="utf-8").strip()
    ):
        raise ValueError("Pytest evidence missing, inconsistent, or duplicated")
    security = read_json(directory / "security/report.json")
    if (
        security.get("gate", {}).get("mode") != "enforce"
        or security.get("status") != "passed"
        or security.get("lock_sha256")
        != hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
        or security.get("gate", {}).get("passed") is not True
        or security.get("integrity", {}).get("status") != "passed"
        or security.get("scanner", {}).get("status") != "complete"
    ):
        raise ValueError("Required real-project security evidence unavailable")
    # The root pytest exit gates execution; this checks artifact availability only.
    xml = directory / "core/core-junit.xml"
    if xml.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Core JUnit exceeds limit")
    suites = ET.fromstring(xml.read_text(encoding="utf-8"))
    if not list(suites.iter("testcase")) or any(
        list(suites.iter(kind)) for kind in ("failure", "error")
    ):
        raise ValueError("Core JUnit unavailable or failing")
    coverage = read_json(directory / "core/core-coverage.json")
    if not coverage.get("files"):
        raise ValueError("Core coverage evidence unavailable")
    ET.fromstring((directory / "core/core-coverage.xml").read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "evidence", "verify", "publish"))
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "evidence":
            if args.directory is None:
                raise ValueError("Evidence directory required")
            require_evidence(args.directory)
            print(
                "PASS: original exits, JUnit, coverage and security evidence available"
            )
            return 0
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, encoding="ascii"
        ).strip()
        tag, publish = request(os.environ, head)
        api = GitHub(os.environ["GITHUB_REPOSITORY"])
        candidate(api, tag, head, publish)
        if args.command == "prepare":
            from .registry import REQUIRED_DAGGER_CHECKS

            with Path(os.environ["GITHUB_OUTPUT"]).open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(f"sha={head}\n")
                # The required Check selection is declared once, in the release
                # contract, and the Workflow selects exactly these names. The
                # evidence then has to show that each of them ran.
                stream.write("dagger_checks<<EOF\n")
                stream.write("\n".join(REQUIRED_DAGGER_CHECKS) + "\n")
                stream.write("EOF\n")
        else:
            if args.manifest is None:
                raise ValueError("Manifest path required")
            if args.command == "verify":
                from .manifest import artifact_ids, manifest, write

                evidence = (
                    read_json(args.directory / "facts.json")
                    if args.directory and (args.directory / "facts.json").is_file()
                    else None
                )
                value = manifest(
                    api,
                    os.environ["GITHUB_REPOSITORY"],
                    tag,
                    head,
                    int(os.environ["GITHUB_RUN_ID"]),
                    int(os.environ["GITHUB_RUN_ATTEMPT"]),
                    "publication" if publish else "validation",
                    evidence,
                    ids=artifact_ids(json.loads(os.environ["RELEASE_NEEDS"])),
                )
                write(args.manifest, value)
                summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
                if summary_path:
                    with Path(summary_path).open("a", encoding="utf-8") as stream:
                        stream.write(
                            f"### Release evidence: {value['result']}\n\n"
                            f"Version: {tag}\n\nVerified SHA: {head}\n\n"
                            f"Workflow: {value['workflow']['run_url']}\n\n"
                            "Manifest: Actions artifact `release-manifest` "
                            "(formal publication also attaches the file to Release).\n\n"
                            "| Existing check | Actual state |\n|---|---|\n"
                        )
                        stream.writelines(
                            f"| {name} | {record['status']} |\n"
                            for name, record in value["checks"].items()
                        )
                if value["result"] != "passed":
                    raise ValueError(
                        "Manifest records incomplete or failed verification"
                    )
            require_jobs(
                json.loads(os.environ["RELEASE_NEEDS"]),
                head,
                publishing=args.command == "publish",
            )
        result = {
            "version": tag,
            "sha": head,
            "publish": publish,
            "status": "candidate" if args.command == "prepare" else "verified",
        }
        if args.command == "publish":
            if not publish:
                raise ValueError("Dry runs cannot publish")
            value = read_json(args.manifest)
            if (
                value["workflow"]["run_id"] != int(os.environ["GITHUB_RUN_ID"])
                or value["workflow"]["run_attempt"]
                != int(os.environ["GITHUB_RUN_ATTEMPT"])
                or value["repository"] != os.environ["GITHUB_REPOSITORY"]
            ):
                raise ValueError("Publication Manifest belongs to another workflow")
            result["release_url"], result["status"] = publish_release(
                api,
                tag,
                head,
                value,
                args.manifest,
            )
        print(json.dumps(result))
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a", encoding="utf-8") as stream:
                stream.write(
                    f"### Manual release: {result['status']}\n\n"
                    f"Version: {tag}\n\nVerified SHA: {head}\n\n"
                    f"Publication requested: {publish}\n\n"
                    f"Release: {result.get('release_url', 'not published')}\n"
                )
        return 0
    except (
        ValueError,
        TypeError,
        KeyError,
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"Release refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
