#!/usr/bin/env python3
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

from release_version import ROOT, checked_version, normalize

SHA = re.compile(r"^[0-9a-f]{40}$")


class ApiError(RuntimeError):
    def __init__(self, status=None):
        self.status = status
        super().__init__(f"GitHub request failed (HTTP {status or 'unavailable'})")


class GitHub:
    def __init__(self, repository):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Invalid repository")
        self.prefix = f"repos/{repository}/"

    def __call__(self, path, method="GET", data=None):
        args = ["gh", "api", "--method", method, self.prefix + path]
        if data is not None:
            args += ["--input", "-"]
        result = subprocess.run(
            args,
            input=json.dumps(data) if data is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=60,
        )
        if result.returncode:
            match = re.search(r"HTTP (\d{3})", result.stderr)
            # Never print or persist raw HTTP responses or command stderr.
            raise ApiError(int(match[1]) if match else None)
        if len(result.stdout) > 1024 * 1024:
            raise ValueError("GitHub metadata exceeds limit")
        return json.loads(result.stdout)


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
        environment["GITHUB_REF"] != "refs/heads/main"
        or environment["GITHUB_EVENT_NAME"] != "workflow_dispatch"
        or environment["RELEASE_SCENARIO"] != "pass"
    ):
        raise ValueError("Publication requires a manual main run with pass scenario")
    return "v" + version, publish == "true"


def candidate(api, tag, head, publish):
    if not publish:
        return None
    if api("git/ref/heads/main")["object"]["sha"] != head:
        raise ValueError("main advanced; refusing publication")
    existing = tag_commit(api, tag)
    if existing is not None and existing != head:
        raise ValueError("Tag conflict; refusing to overwrite")
    release = optional(api, f"releases/tags/{tag}")
    if release is not None and (
        existing != head or release.get("draft") or release.get("prerelease")
    ):
        raise ValueError("Existing Release does not match the validated final tag")
    return release


def require_jobs(needs, head, publishing=False):
    expected = (
        {"candidate", "verified"}
        if publishing
        else {"candidate", "core-tests", "complete-ci", "evidence"}
    )
    if set(needs) != expected:
        raise ValueError("Missing or unexpected verification jobs")
    if any(item.get("result") != "success" for item in needs.values()):
        raise ValueError("Every required job must have actually succeeded")
    if needs["candidate"].get("outputs", {}).get("sha") != head:
        raise ValueError("Verified candidate SHA mismatch")


def publish_release(api, tag, head):
    release = candidate(api, tag, head, True)
    if release is not None:
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
    # Recheck after tag creation. A tag-only partial publication is recoverable.
    release = candidate(api, tag, head, True)
    if release is None:
        release = api(
            "releases",
            "POST",
            {
                "tag_name": tag,
                "target_commitish": head,
                "generate_release_notes": True,
                "draft": False,
                "prerelease": False,
            },
        )
    if tag_commit(api, tag) != head:
        raise ValueError("Final tag SHA mismatch")
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
            with Path(os.environ["GITHUB_OUTPUT"]).open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(f"sha={head}\n")
        else:
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
            result["release_url"], result["status"] = publish_release(api, tag, head)
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
        KeyError,
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"Release refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
