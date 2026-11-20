"""Read one existing release Run and its evidence; never start execution."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import tomllib
import zipfile
from copy import deepcopy
from datetime import UTC, datetime
from subprocess import TimeoutExpired
from urllib.parse import quote

from ..github_cli import ApiError
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
    # Parse/structure failures map to missing/invalid; already-read version
    # disagreement stays a distinct project-metadata mismatch (PR #78 P1-2).
    try:
        versions = _remote_versions(api, head)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ValueError("Remote version metadata is missing or invalid") from error
    if len(set(versions)) != 1:
        raise ValueError("Remote version does not match all project metadata fields")
    return versions[0]


def _remote_versions(api, head):
    paths = (
        "pyproject.toml",
        "uv.lock",
        ".dagger/pyproject.toml",
        ".dagger/uv.lock",
    )
    data = {path: tomllib.loads(content(api, path, head)) for path in paths}
    root_project = data["pyproject.toml"]["project"]
    module_project = data[".dagger/pyproject.toml"]["project"]
    if root_project["name"] != "engineering-ci":
        raise ValueError("Remote root package identity unavailable")
    versions = [normalize(root_project["version"])]
    root_packages = [
        item for item in data["uv.lock"]["package"] if item["name"] == "engineering-ci"
    ]
    if len(root_packages) != 1:
        raise ValueError("Remote root lock package identity unavailable")
    versions.append(normalize(root_packages[0]["version"]))

    module_name = module_project["name"]
    if module_name not in ("engineering-ci", "engineering-ci-module"):
        raise ValueError("Remote module package identity unavailable")
    versions.append(normalize(module_project["version"]))
    module_packages = [
        item
        for item in data[".dagger/uv.lock"]["package"]
        if item["name"] == module_name
    ]
    if len(module_packages) != 1:
        raise ValueError("Remote module lock package identity unavailable")
    versions.append(normalize(module_packages[0]["version"]))

    if module_name == "engineering-ci-module":
        # Current package layout: both dependency declaration and lock
        # source must identify the root project, not a registry wheel.
        if module_project.get("dependencies", []).count("engineering-ci") != 1:
            raise ValueError("Remote module shared dependency unavailable")
        sources = (
            data[".dagger/pyproject.toml"]
            .get("tool", {})
            .get("uv", {})
            .get("sources", {})
        )
        if sources.get("engineering-ci") != {"path": ".."}:
            raise ValueError("Remote module dependency source differs")
        shared_packages = [
            item
            for item in data[".dagger/uv.lock"]["package"]
            if item["name"] == "engineering-ci"
        ]
        if len(shared_packages) != 1 or shared_packages[0].get("source") != {
            "directory": "../"
        }:
            raise ValueError("Remote module lock source differs")
        module_deps = module_packages[0].get("dependencies", [])
        if sum(item.get("name") == "engineering-ci" for item in module_deps) != 1:
            raise ValueError("Remote module lock dependency missing")
        requires_dist = module_packages[0].get("metadata", {}).get("requires-dist", [])
        if (
            sum(
                item.get("name") == "engineering-ci" and item.get("directory") == "../"
                for item in requires_dist
            )
            != 1
        ):
            raise ValueError("Remote module lock dependency source missing")
        versions.append(normalize(shared_packages[0]["version"]))
    return versions


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
    manifest.verify_original_evidence(api, value, repository, head)
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
    asset_id = manifest.artifact_upload_id(
        api, "release-manifest", head, run_id, attempt
    )
    value = manifest_artifact(api, repository, head, run_id, asset_id)
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


def _publication_post_stage(api, release, value, authority):
    from .. import required_assessment_post as r5

    def unavailable(reason):
        return {"status": "unverified", "reasons": [reason]}

    if authority is None:
        return unavailable("release_post_stage_authority_missing")
    if not callable(authority):
        return unavailable("release_post_stage_authority_invalid")

    workflow = value["workflow"]
    run_id, attempt = workflow["run_id"], workflow["run_attempt"]
    head = value["release"]["commit_sha"]
    try:
        run = api(f"actions/runs/{run_id}/attempts/{attempt}")
        published_at = datetime.fromisoformat(release["published_at"])
        if (
            published_at.tzinfo is None
            or type(release["id"]) is not int
            or release["id"] <= 0
        ):
            return unavailable("release_post_stage_publication_identity_invalid")
        if (
            run["id"] != run_id
            or run["run_attempt"] != attempt
            or run["head_sha"] != head
            or run["repository"]["full_name"] != value["repository"]
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"
        ):
            return unavailable("release_post_stage_original_attempt_unverified")
        source = {
            "repository": value["repository"],
            "tag": value["release"]["tag"],
            "head_sha": head,
            "release_id": release["id"],
            "release_url": release["url"],
            "published_at": published_at.isoformat(),
            "run_id": run_id,
            "run_attempt": attempt,
            "artifacts": [
                {key: item.get(key) for key in ("id", "name", "digest")}
                for item in value["artifacts"]
            ],
        }
        inputs = authority(deepcopy(source))
        if not isinstance(inputs, dict):
            return unavailable("release_post_stage_inputs_unavailable")
        prior, post = inputs["prior_assessment"], inputs["post_assessment"]
        if any(
            (assessment.get("candidate") or {}).get("commit") != head
            for assessment in (prior, post)
        ):
            return unavailable("release_post_stage_candidate_mismatch")
        trigger = inputs["trigger_event"]
        artifact_id, artifact_digest = (
            inputs.get("artifact_id"),
            trigger.get("artifact_digest"),
        )
        artifact_mismatch = (
            artifact_id is not None or artifact_digest is not None
        ) and (
            type(artifact_id) is not int
            or artifact_digest is None
            or not any(
                row["id"] == artifact_id and row["digest"] == artifact_digest
                for row in source["artifacts"]
            )
        )
        if (
            trigger["kind"] != "publication_completed"
            or trigger["run_id"] != str(run_id)
            or type(trigger["run_attempt"]) is not int
            or trigger["run_attempt"] != attempt
            or trigger.get("run_completed") is not True
            or datetime.fromisoformat(trigger["observed_at"]) != published_at
            or artifact_mismatch
        ):
            return unavailable("release_post_stage_trigger_mismatch")
        trigger_authority = inputs.get("trigger_authority")
        if not callable(trigger_authority):
            return unavailable("release_post_stage_trigger_authority_missing")

        def verify_trigger(context):
            proof = trigger_authority(
                {
                    **context,
                    "publication_source": deepcopy(source),
                    "artifact_id": artifact_id,
                }
            )
            return proof if isinstance(proof, dict) else False

        pair = r5.reconcile_post_stage(
            prior,
            post,
            trigger_event=trigger,
            due_window=inputs["due_window"],
            phase_order=inputs["phase_order"],
            evaluated_at=datetime.now(UTC).isoformat(),
            phase_order_authority=inputs.get("phase_order_authority"),
            trigger_authority=verify_trigger,
            due_window_authority=inputs.get("due_window_authority"),
            disposition_authority=inputs.get("disposition_authority"),
        )
        r5.validate_post_stage(pair)
    except r5.RequiredAssessmentPostError as error:
        return unavailable(error.code)
    except (
        ApiError,
        OSError,
        TimeoutExpired,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
    ):
        return unavailable("release_post_stage_evidence_unavailable")
    return {
        "status": pair["status"],
        "due_state": pair["due_state"],
        "post_stage_ref": pair["post_stage_ref"],
        "prior_assessment_ref": pair["prior_assessment_ref"],
        "post_assessment_ref": pair["post_assessment_ref"],
        "artifact_id": artifact_id,
        "trigger_event": pair["trigger_event"],
        "requested_disposition": pair["disposition"],
        "reasons": pair["reasons"],
        "lifecycle_all_phases_passed": pair["lifecycle_all_phases_passed"],
    }


def published(api, repository, tag, *, head=None, post_stage_authority=None):
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
        "post_stage": _publication_post_stage(
            api, release, value, post_stage_authority
        ),
    }
