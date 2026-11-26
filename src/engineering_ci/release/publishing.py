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
from copy import deepcopy
from datetime import UTC, datetime
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
        raise ValueError("Requested version does not match all project metadata fields")
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
    from .manifest import run_context, validate, verify_original_evidence

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
    verify_original_evidence(api, value, repository, head)


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


def _require_write_authorization(
    api, manifest, context, release_authority, *, recovery=None
):
    from .. import required_assessment_release as r4

    if release_authority is None:
        raise ValueError("release_publication_authority_missing")
    if not callable(release_authority):
        raise TypeError("release_publication_authority_invalid")
    request = context if recovery is None else {**context, "recovery": recovery}
    inputs = release_authority(deepcopy(request), deepcopy(manifest))
    if not isinstance(inputs, dict) or set(inputs) - {
        "assessment",
        "policy_snapshot_authority",
        "artifact_obligations",
        "artifact_obligation_authority",
        "check_bindings",
        "recovery_authority",
    }:
        raise ValueError("release_publication_authority_inputs_invalid")
    if inputs.get("assessment") is None:
        raise ValueError("release_publication_assessment_missing")
    envelope = r4.reconcile_release_assessment(
        inputs["assessment"],
        manifest,
        publish_context=context,
        policy_snapshot_authority=inputs.get("policy_snapshot_authority"),
        artifact_obligations=inputs.get("artifact_obligations"),
        artifact_obligation_authority=inputs.get("artifact_obligation_authority"),
        check_bindings=inputs.get("check_bindings"),
        require_policy_check_bindings=True,
        manifest_provider_authority=r4.manifest_provider_from_publication(api),
    )
    r4.require_reconciliation_manifest(
        envelope,
        manifest,
        repository=context["repository"],
        tag=context["tag"],
        head=context["head_sha"],
    )
    if (
        envelope["status"] != "verified"
        or envelope["release_authorized"] is not True
        or envelope["contract_sample"] is not False
    ):
        reasons = ", ".join(envelope["reasons"]) or envelope["status"]
        raise ValueError(f"release_publication_not_authorized: {reasons}")
    if recovery is not None:
        authority = inputs.get("recovery_authority")
        if authority is None:
            raise ValueError("release_draft_recovery_authority_missing")
        if not callable(authority):
            raise TypeError("release_draft_recovery_authority_invalid")
        decision = authority(
            deepcopy(
                {
                    **recovery,
                    "retained_publish_context": envelope["publish_context"],
                    "retained_manifest_identity": envelope["manifest_identity"],
                    "retained_reconciliation_ref": envelope["reconciliation_ref"],
                    "retained_assessment_ref": envelope["assessment_ref"],
                    "artifact_obligations": envelope["artifact_obligations"],
                }
            )
        )
        if (
            not isinstance(decision, dict)
            or decision.get("status") != "authorized"
            or decision.get("accepted") is not True
        ):
            raise ValueError("release_draft_recovery_authority_rejected")
    return envelope


def _authorize_draft_recovery(api, saved, release, authorization, authority):
    original = saved["workflow"]
    request = authorization["publish_context"]
    recovered = _require_write_authorization(
        api,
        saved,
        {
            **request,
            "run_id": original["run_id"],
            "run_attempt": original["run_attempt"],
        },
        authority,
        recovery={
            "release_id": release["id"],
            "release_url": release["url"],
            "request_publish_context": request,
            "request_manifest_identity": authorization["manifest_identity"],
            "request_reconciliation_ref": authorization["reconciliation_ref"],
            "request_assessment_ref": authorization["assessment_ref"],
        },
    )
    if {
        item["name"]: item["obligation_ref"]
        for item in recovered["artifact_obligations"]["items"]
    } != {
        item["name"]: item["obligation_ref"]
        for item in authorization["artifact_obligations"]["items"]
    }:
        raise ValueError("release_draft_recovery_artifact_obligations_mismatch")
    keys = ("profile_ref", "profile_revision", "profile_sha256", "assessment_scope")
    if any(
        recovered["profile_snapshot"][key] != authorization["profile_snapshot"][key]
        for key in keys
    ):
        raise ValueError("release_draft_recovery_policy_mismatch")
    return recovered


def publish_release(
    api,
    tag,
    head,
    manifest,
    path,
    *,
    run_id=None,
    run_attempt=None,
    release_authority=None,
    authorization_receipt=None,
):
    """Publish with fresh R4 evidence from the protected caller.

    The authority resolves the original R1 assessment and governed policy and
    artifact inputs for this exact request. The native Provider is read here.
    """
    from .manifest import validate, write

    repository = manifest["repository"]
    validate(manifest, repository, tag, head)
    if (
        manifest["mode"] != "publication"
        or manifest["result"] != "passed"
        or not manifest["tools"]
    ):
        raise ValueError("Publication requires complete passed evidence")
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
    workflow = manifest["workflow"]
    if (
        type(run_id) is not int
        or type(run_attempt) is not int
        or (run_id, run_attempt) != (workflow["run_id"], workflow["run_attempt"])
    ):
        raise ValueError("release_publication_run_attempt_mismatch")
    authorization = _require_write_authorization(
        api,
        manifest,
        {
            "repository": repository,
            "tag": tag,
            "head_sha": head,
            "mode": "publication",
            "phase": "pre_publish",
            "manifest_binding": "same_run",
            "run_id": run_id,
            "run_attempt": run_attempt,
            "evaluated_at": datetime.now(UTC).isoformat(),
        },
        release_authority,
    )
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
    recovery = None
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
        if saved != proposed:
            recovery = _authorize_draft_recovery(
                api, saved, release, authorization, release_authority
            )
    live = candidate(api, tag, head, True)
    if live is None or live["id"] != release["id"] or live.get("draft") is not True:
        raise ValueError("Draft changed before final publication")
    if tag_commit(api, tag) != head:
        raise ValueError("Final tag SHA mismatch")
    if release_asset(api, live) != saved:
        raise ValueError("Draft Manifest changed before final publication")
    release = api(f"releases/{release['id']}", "PATCH", {"draft": False})
    if (
        tag_commit(api, tag) != head
        or release["draft"]
        or release["tag_name"] != tag
        or release_url(release, repository) != saved["release"]["release_url"]
    ):
        raise ValueError("Final Release identity mismatch")
    if authorization_receipt is not None:
        from ..required_assessment_release import bind_manifest_identity

        authorization_receipt.update(
            request_reconciliation=authorization,
            retained_manifest_identity=bind_manifest_identity(
                saved, repository=repository, tag=tag, head=head
            ),
        )
        if recovery is not None:
            authorization_receipt["recovery_reconciliation"] = recovery
    return release["html_url"], "published"


def read_json(path):
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("Release evidence exceeds limit")
    return json.loads(path.read_text(encoding="utf-8"))


def require_evidence(directory):
    # Reuse the existing raw exit/JUnit gate, not a new report verdict.
    sys.path.insert(0, str(ROOT / ".dagger/src/engineering_ci_module"))
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


def main(argv=None, *, release_authority=None):
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
            with Path(os.environ["GITHUB_OUTPUT"]).open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write(f"sha={head}\n")
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
            receipt = {}
            result["release_url"], result["status"] = publish_release(
                api,
                tag,
                head,
                value,
                args.manifest,
                run_id=int(os.environ["GITHUB_RUN_ID"]),
                run_attempt=int(os.environ["GITHUB_RUN_ATTEMPT"]),
                release_authority=release_authority,
                authorization_receipt=receipt,
            )
            if receipt:
                result["authorization"] = receipt
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
