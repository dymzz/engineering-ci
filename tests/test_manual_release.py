"""Manual release safety uses facts and create-only API calls; no live publication."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "manual_release", ROOT / "scripts/manual_release.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
HEAD, OTHER = "a" * 40, "b" * 40
TAG = "v0.4.8"


class FakeGitHub:
    def __init__(self, *, main=HEAD, tag=None, published=None, fail_release=False):
        self.main, self.tag, self.published = main, tag, published
        self.fail_release = fail_release
        self.calls = []

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
        if path == "git/refs" and method == "POST":
            self.tag = data["sha"]
            return {}
        if path == "releases" and method == "POST":
            if self.fail_release:
                raise release.ApiError(503)
            self.published = {
                "html_url": "https://github.com/example/ci/releases/tag/" + TAG,
                "draft": False,
                "prerelease": False,
            }
            return self.published
        raise AssertionError(path)

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
                release.publish_release(api, TAG, HEAD)
            self.assertFalse(api.writes)

    def test_dry_candidate_is_read_only(self):
        api = FakeGitHub()
        release.candidate(api, TAG, HEAD, False)
        self.assertFalse(api.calls)

    def test_success_creates_one_immutable_tag_and_release(self):
        api = FakeGitHub()
        url, state = release.publish_release(api, TAG, HEAD)
        self.assertTrue(url.endswith(TAG))
        self.assertEqual(state, "published")
        self.assertEqual([c[0] for c in api.writes], ["git/refs", "releases"])
        self.assertEqual(api.tag, HEAD)
        self.assertTrue(api.writes[1][2]["generate_release_notes"])

    def test_duplicate_request_never_republishes(self):
        api = FakeGitHub()
        release.publish_release(api, TAG, HEAD)
        api.calls.clear()
        self.assertEqual(
            release.publish_release(api, TAG, HEAD)[1], "already-published"
        )
        self.assertFalse(api.writes)

    def test_tag_only_failure_recovers_without_rewriting(self):
        api = FakeGitHub(fail_release=True)
        with self.assertRaises(release.ApiError):
            release.publish_release(api, TAG, HEAD)
        self.assertEqual(api.tag, HEAD)
        api.calls.clear()
        api.fail_release = False
        release.publish_release(api, TAG, HEAD)
        self.assertEqual([c[0] for c in api.writes], ["releases"])

    def test_main_advance_after_tag_does_not_create_release(self):
        api = FakeGitHub()
        original = api.__call__

        def move(path, method="GET", data=None):
            result = original(path, method, data)
            if path == "git/refs":
                api.main = OTHER
            return result

        with self.assertRaises(ValueError):
            release.publish_release(move, TAG, HEAD)
        self.assertEqual([c[0] for c in api.writes], ["git/refs"])
        self.assertIsNone(api.published)

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
        self.assertIn("uv run --locked --group dev pytest", ci)
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
