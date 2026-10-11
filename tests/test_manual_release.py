"""Manual release safety uses facts and create-only API calls; no live publication."""

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_release_manifest import IDS, ManifestGitHub, make_manifest, publish

ROOT = Path(__file__).resolve().parents[1]
from engineering_ci.release import publishing as release

HEAD, OTHER = "a" * 40, "b" * 40
TAG = "v0.4.11"


class FakeGitHub(ManifestGitHub):
    def __init__(self, *, main=HEAD, tag=None, published=None, fail_release=False):
        super().__init__()
        self.main, self.tag, self.published = main, tag, published
        self.fail_release = fail_release
        self.calls = []
        self.saved = None
        self.fail_upload = False
        self.starter = False

    def __call__(self, path, method="GET", data=None):
        self.calls.append((path, method, data))
        if path == "git/ref/heads/main":
            return {"object": {"sha": self.main, "type": "commit"}}
        if path.startswith("git/ref/tags/"):
            if self.tag is None:
                raise release.ApiError(404)
            return {"object": {"sha": self.tag, "type": "commit"}}
        if path.startswith("releases/tags/"):
            if self.published is None:
                raise release.ApiError(404)
            return self.published
        if path.startswith("releases?"):
            return [] if self.published is None else [self.published]
        if path.startswith("releases/10/assets?"):
            if self.starter:
                return [
                    {
                        "id": 20,
                        "name": "release-manifest.json",
                        "state": "starter",
                        "size": 0,
                    }
                ]
            return (
                []
                if self.saved is None
                else [
                    {
                        "id": 20,
                        "name": "release-manifest.json",
                        "state": "uploaded",
                        "size": 100,
                    }
                ]
            )
        if path == "releases/assets/20" and method == "GET":
            return {
                "id": 20,
                "name": "release-manifest.json",
                "state": "starter" if self.starter else "uploaded",
                "size": 0 if self.starter else 100,
            }
        if path == "git/refs" and method == "POST":
            self.tag = data["sha"]
            return {}
        if path == "releases" and method == "POST":
            if self.fail_release:
                raise release.ApiError(503)
            self.published = {
                "id": 10,
                "url": "https://api.github.com/repos/example/ci/releases/10",
                "tag_name": TAG,
                "html_url": "https://github.com/example/ci/releases/tag/" + TAG,
                "draft": data["draft"],
                "prerelease": False,
            }
            return self.published
        if path == "releases/10" and method == "PATCH":
            self.published.update(data)
            return self.published
        if path == "releases/assets/20" and method == "DELETE":
            self.starter = False
            return None
        return super().__call__(path, method, data)

    def upload(self, tag, path):
        self.calls.append(("upload", "POST", tag))
        if self.fail_upload:
            raise release.ApiError(503)
        self.saved = json.loads(path.read_text(encoding="utf-8"))

    def download(self, asset_id):
        return copy.deepcopy(self.saved)

    @property
    def writes(self):
        return [c for c in self.calls if c[1] != "GET"]


def environment(**changes):
    values = {
        "RELEASE_VERSION": TAG,
        "RELEASE_PUBLISH": "true",
        "RELEASE_SCENARIO": "pass",
        "GITHUB_SHA": HEAD,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
    }
    values.update(changes)
    return values


def needs():
    result = {
        name: {"result": "success"}
        for name in ("candidate", "core-tests", "complete-ci", "evidence")
    }
    result["candidate"]["outputs"] = {"sha": HEAD}
    return result


class ManualReleaseTests(unittest.TestCase):
    def setUp(self):
        # Request-policy fixtures have their own version, independent of this checkout.
        metadata = patch.object(release, "checked_version", return_value=TAG[1:])
        metadata.start()
        self.addCleanup(metadata.stop)

    def test_requested_version_and_exact_checkout(self):
        self.assertEqual(release.request(environment(), HEAD), (TAG, True))
        for values, sha in [
            (environment(RELEASE_VERSION="v9.9.9"), HEAD),
            (environment(GITHUB_SHA=OTHER), HEAD),
            (environment(RELEASE_VERSION="main"), HEAD),
        ]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                release.request(values, sha)

    def test_only_manual_main_pass_can_publish(self):
        for values in [
            environment(GITHUB_REF="refs/heads/feature"),
            environment(GITHUB_EVENT_NAME="push"),
            environment(RELEASE_SCENARIO="assertion"),
            environment(RELEASE_PUBLISH="yes"),
        ]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                release.request(values, HEAD)
        self.assertEqual(
            release.request(
                environment(
                    RELEASE_PUBLISH="false",
                    GITHUB_REF="refs/heads/candidate",
                    RELEASE_SCENARIO="assertion",
                ),
                HEAD,
            ),
            (TAG, False),
        )

    def test_every_job_must_run_and_pass_with_same_sha(self):
        release.require_jobs(needs(), HEAD)
        for state in ("failure", "skipped", "cancelled"):
            for name in needs():
                facts = needs()
                facts[name]["result"] = state
                with (
                    self.subTest(state=state, name=name),
                    self.assertRaises(ValueError),
                ):
                    release.require_jobs(facts, HEAD)
        for facts in ({}, {**needs(), "extra": {"result": "success"}}):
            with self.assertRaises(ValueError):
                release.require_jobs(facts, HEAD)
        facts = needs()
        facts["candidate"]["outputs"]["sha"] = OTHER
        with self.assertRaises(ValueError):
            release.require_jobs(facts, HEAD)

    def test_main_advance_and_conflicting_tag_never_write(self):
        for api in (FakeGitHub(main=OTHER), FakeGitHub(tag=OTHER)):
            with self.assertRaises(ValueError):
                publish(api)
            self.assertFalse(api.writes)

    def test_dry_candidate_is_read_only(self):
        api = FakeGitHub()
        release.candidate(api, TAG, HEAD, False)
        self.assertFalse(api.calls)

    def test_success_creates_one_immutable_tag_and_release(self):
        api = FakeGitHub()
        url, state = publish(api)
        self.assertTrue(url.endswith(TAG))
        self.assertEqual(state, "published")
        self.assertEqual(
            [c[0] for c in api.writes],
            ["git/refs", "releases", "upload", "releases/10"],
        )
        self.assertEqual(api.tag, HEAD)
        self.assertTrue(api.writes[1][2]["generate_release_notes"])
        self.assertTrue(api.writes[1][2]["draft"])
        self.assertFalse(api.published["draft"])
        self.assertEqual(api.saved["release"]["commit_sha"], HEAD)

    def test_duplicate_request_never_republishes(self):
        api = FakeGitHub()
        publish(api)
        api.calls.clear()
        self.assertEqual(publish(api)[1], "already-published")
        self.assertFalse(api.writes)

    def test_tag_only_failure_recovers_without_rewriting(self):
        api = FakeGitHub(fail_release=True)
        with self.assertRaises(release.ApiError):
            publish(api)
        self.assertEqual(api.tag, HEAD)
        api.calls.clear()
        api.fail_release = False
        publish(api)
        self.assertEqual(
            [c[0] for c in api.writes], ["releases", "upload", "releases/10"]
        )

    def test_main_advance_after_tag_does_not_create_release(self):
        api = FakeGitHub()
        original = api.__call__

        def move(path, method="GET", data=None):
            result = original(path, method, data)
            if path == "git/refs":
                api.main = OTHER
            return result

        with self.assertRaises(ValueError):
            publish(move)
        self.assertEqual([c[0] for c in api.writes], ["git/refs"])
        self.assertIsNone(api.published)

    def test_upload_failure_preserves_draft_and_recovers_without_clobber(self):
        api = FakeGitHub()
        api.fail_upload = True
        with self.assertRaises(release.ApiError):
            publish(api)
        self.assertTrue(api.published["draft"])
        self.assertEqual(api.tag, HEAD)
        self.assertNotIn("releases/10", [c[0] for c in api.writes])
        api.fail_upload = False
        api.calls.clear()
        publish(api)
        self.assertEqual([c[0] for c in api.writes], ["upload", "releases/10"])

    def test_empty_starter_asset_is_repaired_only_on_validated_draft(self):
        api = FakeGitHub()
        api.fail_upload = True
        with self.assertRaises(release.ApiError):
            publish(api)
        api.starter = True
        api.fail_upload = False
        api.calls.clear()
        publish(api)
        self.assertEqual(
            [c[0] for c in api.writes], ["releases/assets/20", "upload", "releases/10"]
        )
        self.assertFalse(api.starter)
        self.assertFalse(api.published["draft"])

    def test_starter_asset_of_final_release_is_never_deleted(self):
        api = FakeGitHub()
        publish(api)
        api.starter = True
        api.calls.clear()
        with self.assertRaises(ValueError, msg="incomplete final asset"):
            publish(api)
        self.assertFalse(api.writes)

    def test_starter_asset_that_completed_upload_is_not_deleted(self):
        class CompletedUpload(FakeGitHub):
            def __call__(self, path, method="GET", data=None):
                if path == "releases/assets/20" and method == "GET":
                    self.starter = False
                return super().__call__(path, method, data)

        api = CompletedUpload()
        api.fail_upload = True
        with self.assertRaises(release.ApiError):
            publish(api)
        api.starter = True
        api.fail_upload = False
        api.calls.clear()
        with self.assertRaisesRegex(ValueError, "changed before repair"):
            publish(api)
        self.assertFalse(api.writes)

    def test_concurrent_promotion_is_not_patched_as_a_draft(self):
        class PromotedDraft(FakeGitHub):
            def download(self, asset_id):
                saved = super().download(asset_id)
                self.published["draft"] = False
                return saved

        api = PromotedDraft()
        with self.assertRaisesRegex(ValueError, "Draft changed"):
            publish(api)
        self.assertNotIn("releases/10", [call[0] for call in api.writes])

    def test_lost_upload_response_recovers_without_uploading_again(self):
        class LostUpload(FakeGitHub):
            def upload(self, tag, path):
                super().upload(tag, path)
                if self.lose_response:
                    raise release.ApiError()

        api = LostUpload()
        api.lose_response = True
        with self.assertRaises(release.ApiError):
            publish(api)
        self.assertTrue(api.published["draft"])
        api.lose_response = False
        api.calls.clear()
        publish(api)
        self.assertEqual([c[0] for c in api.writes], ["releases/10"])

    def test_lost_promotion_response_is_not_reported_success_then_is_idempotent(self):
        class LostPromotion(FakeGitHub):
            def __call__(self, path, method="GET", data=None):
                result = super().__call__(path, method, data)
                if path == "releases/10" and method == "PATCH":
                    raise release.ApiError()
                return result

        api = LostPromotion()
        with self.assertRaises(release.ApiError):
            publish(api)
        self.assertFalse(api.published["draft"])
        api.calls.clear()
        self.assertEqual(publish(api)[1], "already-published")
        self.assertFalse(api.writes)

    def test_existing_manifest_artifact_digest_conflict_refuses_promotion(self):
        api = FakeGitHub()
        api.fail_upload = True
        with self.assertRaises(release.ApiError):
            publish(api)
        value = make_manifest(api)
        value["release"]["release_url"] = api.published["url"]
        value["artifacts"][0]["digest"] = "sha256:" + "e" * 64
        api.saved = value
        api.fail_upload = False
        api.calls.clear()
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            publish(api)
        self.assertFalse(api.writes)

    def test_upload_readback_failure_does_not_promote(self):
        api = FakeGitHub()
        original = api.download

        def corrupt(asset_id):
            value = original(asset_id)
            value["release"]["commit_sha"] = OTHER
            return value

        api.download = corrupt
        with self.assertRaises(ValueError):
            publish(api)
        self.assertTrue(api.published["draft"])
        self.assertNotIn("releases/10", [c[0] for c in api.writes])

    def test_manifest_resource_url_survives_draft_browser_url_change(self):
        class ChangedURL(FakeGitHub):
            def __call__(self, path, method="GET", data=None):
                result = super().__call__(path, method, data)
                if path == "releases/10" and method == "PATCH":
                    result["html_url"] = (
                        "https://github.com/example/ci/releases/tag/" + TAG
                    )
                elif path == "releases" and method == "POST":
                    result["html_url"] = (
                        "https://github.com/example/ci/releases/tag/untagged-draft"
                    )
                return result

        api = ChangedURL()
        self.assertEqual(publish(api)[1], "published")
        self.assertEqual(api.saved["release"]["release_url"], api.published["url"])
        self.assertEqual(publish(api)[1], "already-published")

    def test_existing_final_release_without_evidence_is_not_rewritten(self):
        api = FakeGitHub()
        publish(api)
        api.saved = None
        api.calls.clear()
        with self.assertRaisesRegex(ValueError, "lacks matching Manifest"):
            publish(api)
        self.assertFalse(api.writes)

    def test_invalid_manifest_or_failed_check_prevents_any_write(self):
        api = FakeGitHub()
        value = make_manifest(api)
        value["release"]["commit_sha"] = OTHER
        with self.assertRaises(ValueError):
            publish(api, value)
        self.assertFalse(api.writes)
        value = make_manifest(api)
        api.jobs[0]["conclusion"] = "failure"
        with self.assertRaises(ValueError):
            publish(api, value)
        self.assertFalse(api.writes)

    def test_matching_draft_found_by_list_after_by_tag_404(self):
        api = FakeGitHub()
        api.fail_upload = True
        with self.assertRaises(release.ApiError):
            publish(api)
        api.fail_upload = False
        original = api.__call__

        def hide_draft(path, method="GET", data=None):
            if path.startswith("releases/tags/"):
                raise release.ApiError(404)
            return original(path, method, data)

        self.assertTrue(release.candidate(hide_draft, TAG, HEAD, True)["draft"])

    def test_promotion_api_failure_reuses_uploaded_manifest(self):
        api = FakeGitHub()
        original = api.__call__

        class PromotionFailure(FakeGitHub):
            def __call__(self, path, method="GET", data=None):
                if path == "releases/10" and method == "PATCH":
                    raise release.ApiError(503)
                return original(path, method, data)

            def upload(self, tag, path):
                api.upload(tag, path)

            def download(self, asset_id):
                return api.download(asset_id)

        with self.assertRaises(release.ApiError):
            publish(PromotionFailure())
        self.assertTrue(api.published["draft"])
        saved = copy.deepcopy(api.saved)
        api.calls.clear()
        publish(api)
        self.assertEqual([c[0] for c in api.writes], ["releases/10"])
        self.assertEqual(api.saved, saved)

    def test_new_attempt_recovers_with_original_verified_manifest(self):
        class Recovery(FakeGitHub):
            def __call__(self, path, method="GET", data=None):
                if path == "actions/runs/123/attempts/2":
                    return {**self.run, "run_attempt": 2}
                if path.startswith("actions/runs/123/attempts/2/jobs?"):
                    return {
                        "jobs": [
                            {**job, "id": job["id"] + 100, "run_attempt": 2}
                            for job in self.jobs
                        ]
                    }
                if path == "releases/10" and method == "PATCH" and self.fail_promote:
                    raise release.ApiError(503)
                return super().__call__(path, method, data)

        api = Recovery()
        api.fail_promote = True
        with self.assertRaises(release.ApiError):
            publish(api)
        saved = copy.deepcopy(api.saved)
        api.fail_promote = False
        api.calls.clear()
        from engineering_ci.release.manifest import manifest

        fresh = manifest(
            api,
            "example/ci",
            TAG,
            HEAD,
            123,
            2,
            "publication",
            {
                "tools": saved["tools"],
                "dagger_checks": [
                    "engineering-ci:fixture-quality-gates",
                    "engineering-ci:fixture-python-matrix",
                ],
            },
            ids=IDS,
        )
        publish(api, fresh)
        self.assertEqual([c[0] for c in api.writes], ["releases/10"])
        self.assertEqual(api.saved, saved)
        self.assertEqual(api.saved["workflow"]["run_attempt"], 1)

    def test_annotated_tag_is_peeled(self):
        def api(path):
            if path.startswith("git/ref/"):
                return {"object": {"type": "tag", "sha": OTHER}}
            return {"object": {"type": "commit", "sha": HEAD}}

        self.assertEqual(release.tag_commit(api, TAG), HEAD)

    def test_unavailable_api_is_not_missing_tag(self):
        with self.assertRaises(release.ApiError):
            release.optional(
                lambda _: (_ for _ in ()).throw(release.ApiError(503)), "tag"
            )

    def test_real_pytest_and_missing_or_wrong_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for name in ("pytest", "security", "core"):
                (directory / name).mkdir()
            test = directory / "test_case.py"
            test.write_text("def test_ok():\n    assert 1 == 1\n", encoding="utf-8")
            xml = directory / "pytest/junit.xml"
            process = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", str(test), f"--junitxml={xml}"],
                cwd=directory,
                env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            sys.path.insert(0, str(ROOT / ".dagger/src/engineering_ci"))
            from pytest_report import summarize_pytest

            facts = summarize_pytest(xml.read_text(encoding="utf-8"), 0, "")
            (directory / "pytest/report.json").write_text(
                json.dumps(facts), encoding="utf-8"
            )
            (directory / "pytest/exit-code.txt").write_text("0")
            (directory / "pytest/fixture-invocations.txt").write_text("1")
            (directory / "pytest/summary.md").write_text("passed")
            (directory / "core/core-junit.xml").write_bytes(xml.read_bytes())
            (directory / "core/core-coverage.json").write_text(
                '{"files": {"test_case.py": {}}}'
            )
            (directory / "core/core-coverage.xml").write_text("<coverage/>")
            security = {
                "status": "passed",
                "gate": {"mode": "enforce", "passed": True},
                "integrity": {"status": "passed"},
                "scanner": {"status": "complete"},
                "lock_sha256": hashlib.sha256(
                    (ROOT / "uv.lock").read_bytes()
                ).hexdigest(),
            }
            security_path = directory / "security/report.json"
            security_path.write_text(json.dumps(security))
            release.require_evidence(directory)
            security["lock_sha256"] = "fixture-lock"
            security_path.write_text(json.dumps(security))
            with self.assertRaises(ValueError):
                release.require_evidence(directory)
            security["lock_sha256"] = hashlib.sha256(
                (ROOT / "uv.lock").read_bytes()
            ).hexdigest()
            security_path.write_text(json.dumps(security))
            (directory / "pytest/exit-code.txt").write_text("1")
            with self.assertRaises(ValueError):
                release.require_evidence(directory)
            (directory / "pytest/exit-code.txt").write_text("0")
            xml.unlink()
            with self.assertRaises((ValueError, OSError)):
                release.require_evidence(directory)

    def test_workflow_triggers_and_privileges(self):
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertNotIn("workflow_run:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("workflow_dispatch:", ci)
        self.assertNotIn("dagger call", ci)
        self.assertNotIn("docker pull", ci)
        self.assertIn("engineering_ci.workflow_actions checks tests", ci)
        self.assertEqual(workflow.count("contents: write"), 1)
        self.assertIn("default: false", workflow)
        self.assertIn("dependency_security_project: .\n", workflow)
        self.assertIn("dependency_security_mode: enforce", workflow)
        self.assertIn("verify_module_lock: true", workflow)
        self.assertIn("inputs.publish && needs.verified.result == 'success'", workflow)
        shared = (ROOT / ".github/workflows/reusable-python-ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("default: 'v0.4.7'", shared)

    def test_release_metadata_error_has_clean_exit(self):
        with (
            patch.object(
                release, "checked_version", side_effect=ValueError("mismatch")
            ),
            self.assertRaisesRegex(ValueError, "mismatch"),
        ):
            release.request(environment(), HEAD)


if __name__ == "__main__":
    unittest.main()
