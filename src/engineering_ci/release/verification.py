"""Read one existing release Run and its evidence; never start execution."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import tomllib
import zipfile
from urllib.parse import quote

from . import manifest
from .publishing import (
    find_release,
    published_evidence,
    release_asset,
    release_url,
    tag_commit,
)
from .version import normalize


def content(api, path, head):
    item = api(f"contents/{path}?ref={head}")
    if item.get("path") != path or item.get("encoding") != "base64":
        raise ValueError("Remote version metadata identity unavailable")
    raw = base64.b64decode("".join(item["content"].split()), validate=True)
    if len(raw) > 1024 * 1024:
        raise ValueError("Remote version metadata exceeds limit")
    return raw.decode("utf-8")


def version_at(api, head):
    if not manifest.SHA.fullmatch(head):
        raise ValueError("Remote commit identity unavailable")
    versions = []
    shared_dependency = False
    for path in (
        "pyproject.toml",
        "uv.lock",
        ".dagger/pyproject.toml",
        ".dagger/uv.lock",
    ):
        try:
            data = tomllib.loads(content(api, path, head))
            if path.endswith("pyproject.toml"):
                project = data["project"]
                expected = project["name"]
                allowed = (
                    {"engineering-ci", "engineering-ci-module"}
                    if path.startswith(".dagger/")
                    else {"engineering-ci"}
                )
                if expected not in allowed:
                    raise ValueError("Remote project package identity unavailable")
                shared_dependency = any(
                    re.match(r"(?i)^engineering[-_]ci(?=$|[\s\[<>=!~;])", item)
                    for item in project.get("dependencies", [])
                )
                versions.append(normalize(project["version"]))
            else:
                names = {expected}
                if shared_dependency:
                    names.add("engineering-ci")
                for name in names:
                    packages = [
                        item for item in data["package"] if item["name"] == name
                    ]
                    if len(packages) != 1:
                        raise ValueError("Remote lockfile package identity unavailable")
                    versions.append(normalize(packages[0]["version"]))
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError("Remote version metadata is missing or invalid") from error
    if len(set(versions)) != 1:
        raise ValueError("Remote version does not match all project metadata fields")
    return versions[0]


def target(api, ref, version):
    obj = api(f"git/ref/heads/{quote(ref, safe='')}")["object"]
    if obj.get("type") != "commit" or not manifest.SHA.fullmatch(obj["sha"]):
        raise ValueError("Remote branch does not resolve to a commit")
    head = obj["sha"]
    if normalize(version) != version_at(api, head):
        raise ValueError("Requested version does not match remote commit metadata")
    return {
        "version": normalize(version),
        "tag": "v" + normalize(version),
        "commit_sha": head,
        "ref": ref,
    }


def run_target(api, repository, run_id, version, ref=None):
    run = api(f"actions/runs/{run_id}")
    head = run["head_sha"]
    manifest.run_context(api, repository, head, run_id, run["run_attempt"])
    if (
        normalize(version) != version_at(api, head)
        or ref is not None
        and run.get("head_branch") != ref
    ):
        raise ValueError("Requested version or branch does not match the specified Run")
    return {
        "version": normalize(version),
        "tag": "v" + normalize(version),
        "commit_sha": head,
        "ref": run["head_branch"],
    }


def completed_run(api, repository, head, run_id, attempt, *, ref=None):
    current = api(f"actions/runs/{run_id}")
    if current.get("run_attempt") != attempt:
        raise ValueError("Verification is stale: Run attempt changed")
    context = manifest.run_context(api, repository, head, run_id, attempt)
    if (
        current.get("id") != run_id
        or current.get("head_sha") != head
        or current.get("status") != "completed"
        or current.get("conclusion") != "success"
        or ref is not None
        and current.get("head_branch") != ref
    ):
        raise ValueError(
            "Verification Run did not complete successfully for this commit"
        )
    return context


def evidence(api, value, repository, tag, head):
    manifest.validate(value, repository, tag, head)
    if value["result"] != "passed":
        raise ValueError("Required release verification did not pass")
    workflow = value["workflow"]
    completed_run(api, repository, head, workflow["run_id"], workflow["run_attempt"])
    jobs = manifest.workflow_jobs(
        api, workflow["run_id"], workflow["run_attempt"], head
    )
    for key, source in manifest.CHECKS.items():
        if manifest.check_record(jobs, *source) != value["checks"][key]:
            raise ValueError("Manifest does not match original Actions results")
    for item in value["artifacts"]:
        current = manifest.artifact_metadata(
            api,
            repository,
            item["name"],
            item["id"],
            head,
            workflow["run_id"],
            allow_expired=True,
        )
        if item["digest"] is not None and item["digest"] != current["digest"]:
            raise ValueError("Manifest Artifact digest mismatch")
    return value


def manifest_artifact(api, repository, head, run_id, asset_id):
    if type(asset_id) is not int or asset_id <= 0:
        raise ValueError("Invalid Manifest Artifact ID")
    item = api(f"actions/artifacts/{asset_id}")
    if (
        item.get("id") != asset_id
        or item.get("name") != "release-manifest"
        or item.get("expired") is not False
        or not 0 < item.get("size_in_bytes", 0) <= 2 * 1024 * 1024
        or item.get("workflow_run", {}).get("id") != run_id
        or item.get("workflow_run", {}).get("head_sha") != head
        or item.get("url")
        != f"https://api.github.com/repos/{repository}/actions/artifacts/{asset_id}"
    ):
        raise ValueError("Validation Manifest Artifact identity unavailable or expired")
    raw = api.artifact_archive(asset_id)
    digest = item.get("digest")
    if digest is not None and (
        not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        or digest != "sha256:" + hashlib.sha256(raw).hexdigest()
    ):
        raise ValueError("Validation Manifest archive digest mismatch")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if (
                len(entries) != 1
                or entries[0].filename != "release-manifest.json"
                or entries[0].flag_bits & 1
                or not 0 < entries[0].file_size <= 1024 * 1024
            ):
                raise ValueError("Invalid validation Manifest archive layout")
            return json.loads(archive.read(entries[0]))
    except (zipfile.BadZipFile, EOFError, RuntimeError) as error:
        raise ValueError("Corrupt validation Manifest archive") from error


def validation(api, repository, identity, run_id):
    head, tag = identity["commit_sha"], identity["tag"]
    run = api(f"actions/runs/{run_id}")
    attempt = run["run_attempt"]
    completed_run(api, repository, head, run_id, attempt, ref=identity["ref"])
    items = manifest.pages(api, f"actions/runs/{run_id}/artifacts", "artifacts")
    items = [item for item in items if item.get("name") == "release-manifest"]
    if len(items) != 1:
        raise ValueError(
            "Validation Manifest missing or ambiguous for this Run and attempt"
        )
    value = manifest_artifact(api, repository, head, run_id, items[0]["id"])
    if (
        value.get("workflow", {}).get("run_id") != run_id
        or value["workflow"].get("run_attempt") != attempt
    ):
        raise ValueError("Validation Manifest belongs to another Run or attempt")
    evidence(api, value, repository, tag, head)
    return value


def validation_state(api, repository, identity, run_id):
    run = api(f"actions/runs/{run_id}")
    context = manifest.run_context(
        api, repository, identity["commit_sha"], run_id, run["run_attempt"]
    )
    if run.get("head_branch") != identity["ref"]:
        raise ValueError("Validation Run belongs to another branch")
    state, conclusion = run.get("status"), run.get("conclusion")
    if state in {"queued", "in_progress", "waiting", "pending", "requested"}:
        return {
            **context,
            "status": "running",
            "execution_status": state,
            "conclusion": None,
        }
    if state == "completed" and conclusion in {
        "failure",
        "cancelled",
        "timed_out",
        "skipped",
        "neutral",
        "action_required",
        "stale",
    }:
        return {
            **context,
            "status": "failed",
            "execution_status": state,
            "conclusion": conclusion,
        }
    if state != "completed" or conclusion != "success":
        return {**context, "status": "unavailable"}
    value = validation(api, repository, identity, run_id)
    return {**value["workflow"], "status": "passed"}


def published(api, repository, tag, *, head=None):
    release = find_release(api, tag)
    if release is None:
        return None
    actual = tag_commit(api, tag)
    if actual is None or head is not None and actual != head:
        raise ValueError("Existing Release tag points to another commit")
    if release.get("prerelease") or release.get("tag_name") != tag:
        raise ValueError("Existing Release identity differs from the final version")
    if "v" + version_at(api, actual) != tag:
        raise ValueError("Published version differs from its commit metadata")
    if release.get("draft"):
        return {
            "status": "draft",
            "release_url": release["html_url"],
            "commit_sha": actual,
        }
    value = release_asset(api, release)
    if value is None:
        raise ValueError("Published Release lacks validation evidence")
    # A recovered Draft keeps the original verified attempt's immutable receipt.
    published_evidence(api, value, repository, tag, actual)
    if value["mode"] != "publication" or value["release"]["release_url"] != release_url(
        release, repository
    ):
        raise ValueError("Release Manifest does not match publication")
    return {
        "status": "published",
        "release_url": release["html_url"],
        "commit_sha": actual,
        "manifest": value,
    }
