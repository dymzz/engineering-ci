"""Trusted policy publication, first receipt, rollback guards and per-asset decisions."""

import datetime as dt
import json
from pathlib import Path

import pytest
from test_release_manifest import HEAD, REPOSITORY, TAG, make_manifest

from engineering_ci import security_policy as policy
from engineering_ci.release import manifest as evidence

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config/security-policy.example.json"
NOW = dt.datetime(2026, 10, 24, 12, 0, tzinfo=dt.UTC)


def published_manifest():
    value = make_manifest()
    value["release"]["release_url"] = (
        f"https://api.github.com/repos/{REPOSITORY}/releases/9"
    )
    evidence.validate(value, REPOSITORY, TAG, HEAD, published=True)
    return value


def anchor(**overrides):
    value = {
        "schema_version": 1,
        "kind": policy.ANCHOR_KIND,
        "repository": REPOSITORY,
        "release_workflow": ".github/workflows/release.yml",
        "minimum_policy_revision": 1,
    }
    value.update(overrides)
    return value


def policy_document(revision=7, **overrides):
    value = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    value["revision"] = revision
    value["minimum_revision"] = min(overrides.pop("minimum_revision", 1), revision)
    value["publisher"] = {
        "repository": REPOSITORY,
        "workflow": ".github/workflows/release.yml",
    }
    value.update(overrides)
    return value


def policy_bytes(document=None):
    return policy.encode(document or policy_document()).encode("utf-8")


def receive(
    document=None,
    *,
    state,
    manifest=None,
    trust=None,
    bootstrap=True,
    now=NOW,
    publication=None,
):
    raw = policy_bytes(document)
    manifest = manifest or published_manifest()
    envelope = publication or policy.publish(raw, manifest, now=now)
    return policy.receive(
        raw,
        envelope,
        manifest,
        trust or anchor(),
        state,
        now=now,
        bootstrap=bootstrap,
    )


class TestPolicyDocument:
    def test_example_policy_is_a_valid_publish_input(self):
        value = policy.load_policy(EXAMPLE.read_bytes())
        assert value["revision"] == 7
        assert value["minimum_revision"] == 1
        assert value["publisher"]["repository"] == "dymzz/engineering-ci"
        assert {rule["effect"] for rule in value["rules"]} == {"allow", "deny"}
        assert policy.digest(EXAMPLE.read_bytes()) == policy.digest(
            policy.encode(value).encode("utf-8")
        )

    def test_policy_rejects_unknown_fields_and_unsupported_schema(self):
        value = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        value["unexpected"] = True
        with pytest.raises(policy.PolicyError) as raised:
            policy.load_policy(json.dumps(value).encode("utf-8"))
        assert raised.value.code == "policy_document_invalid"

        value = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        value["schema_version"] = 2
        with pytest.raises(policy.PolicyError) as raised:
            policy.load_policy(json.dumps(value).encode("utf-8"))
        assert raised.value.code == "policy_schema_unsupported"

    def test_policy_rejects_invalid_effect_and_window(self):
        value = policy_document()
        value["rules"][0]["effect"] = "maybe"
        with pytest.raises(policy.PolicyError) as raised:
            policy.load_policy(json.dumps(value).encode("utf-8"))
        assert raised.value.code == "policy_document_invalid"

        value = policy_document(expires_at="2026-01-01T00:00:00Z")
        with pytest.raises(policy.PolicyError) as raised:
            policy.load_policy(json.dumps(value).encode("utf-8"))
        assert raised.value.code == "policy_window_invalid"


class TestPublication:
    def test_publication_binds_policy_bytes_to_a_published_release(self):
        manifest = published_manifest()
        raw = policy_bytes()
        envelope = policy.publish(raw, manifest, now=NOW)
        assert envelope["policy"]["sha256"] == policy.digest(raw)
        assert envelope["policy"]["size"] == len(raw)
        assert envelope["release"] == {
            "repository": manifest["repository"],
            "tag": manifest["release"]["tag"],
            "commit_sha": manifest["release"]["commit_sha"],
            "release_url": manifest["release"]["release_url"],
            "run_id": manifest["workflow"]["run_id"],
            "run_attempt": manifest["workflow"]["run_attempt"],
        }
        assert envelope["release_manifest_sha256"] == policy.digest(
            evidence.encode(manifest).encode("utf-8")
        )

    def test_publication_rejects_failed_or_unpublished_evidence(self):
        failed = published_manifest()
        failed["result"] = "failed"
        with pytest.raises(policy.PolicyError) as raised:
            policy.publish(policy_bytes(), failed, now=NOW)
        assert raised.value.code == "release_manifest_invalid"

        validation = make_manifest(mode="validation")
        validation["release"]["release_url"] = (
            f"https://api.github.com/repos/{REPOSITORY}/releases/9"
        )
        with pytest.raises(policy.PolicyError) as raised:
            policy.publish(policy_bytes(), validation, now=NOW)
        assert raised.value.code == "release_manifest_invalid"

    def test_publication_rejects_a_policy_claiming_another_publisher(self):
        value = policy_document()
        value["publisher"]["workflow"] = ".github/workflows/other.yml"
        with pytest.raises(policy.PolicyError) as raised:
            policy.publish(policy_bytes(value), published_manifest(), now=NOW)
        assert raised.value.code == "policy_publisher_mismatch"


class TestReceive:
    def test_bootstrap_writes_state_and_returns_a_snapshot(self, tmp_path):
        state = tmp_path / "state.json"
        snapshot = receive(state=state)
        assert snapshot["kind"] == policy.SNAPSHOT_KIND
        assert snapshot["policy"]["revision"] == 7
        assert snapshot["policy"]["sha256"] == policy.digest(policy_bytes())
        assert snapshot["release"]["repository"] == REPOSITORY
        assert snapshot["state"] == {"initialized": True, "previous_revision": None}
        saved = json.loads(state.read_text(encoding="utf-8"))
        assert saved["kind"] == policy.STATE_KIND
        assert saved["revision"] == 7
        assert saved["policy_sha256"] == snapshot["policy"]["sha256"]

    def test_receive_without_a_trusted_baseline_is_refused(self, tmp_path):
        with pytest.raises(policy.PolicyError) as raised:
            receive(state=tmp_path / "state.json", bootstrap=False)
        assert raised.value.code == "policy_state_missing"
        assert not (tmp_path / "state.json").exists()

    def test_untrusted_publisher_anchor_is_refused(self, tmp_path):
        with pytest.raises(policy.PolicyError) as raised:
            receive(state=tmp_path / "state.json", trust=anchor(repository="evil/fork"))
        assert raised.value.code == "publication_publisher_untrusted"

    def test_bootstrap_below_the_anchor_minimum_is_refused(self, tmp_path):
        with pytest.raises(policy.PolicyError) as raised:
            receive(
                state=tmp_path / "state.json",
                trust=anchor(minimum_policy_revision=8),
            )
        assert raised.value.code == "policy_below_minimum"
        assert not (tmp_path / "state.json").exists()

    def test_expired_and_not_yet_valid_policies_are_refused(self, tmp_path):
        expired = dt.datetime(2027, 12, 1, tzinfo=dt.UTC)
        with pytest.raises(policy.PolicyError) as raised:
            receive(state=tmp_path / "state.json", now=expired)
        assert raised.value.code == "policy_expired"

        future = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
        with pytest.raises(policy.PolicyError) as raised:
            receive(state=tmp_path / "state.json", now=future)
        assert raised.value.code == "policy_not_yet_valid"

    def test_same_revision_is_idempotent(self, tmp_path):
        state = tmp_path / "state.json"
        receive(state=state)
        saved = state.read_bytes()
        snapshot = receive(state=state)
        assert snapshot["state"] == {"initialized": False, "previous_revision": 7}
        assert state.read_bytes() == saved

    def test_rollback_and_same_revision_conflict_are_refused(self, tmp_path):
        state = tmp_path / "state.json"
        receive(state=state)
        with pytest.raises(policy.PolicyError) as raised:
            receive(policy_document(6), state=state)
        assert raised.value.code == "policy_rollback"

        changed = policy_document()
        changed["rules"].append(
            {
                "rule_id": "extra",
                "effect": "deny",
                "reason": "changed content",
                "asset": {"tool_id": "trivy"},
            }
        )
        with pytest.raises(policy.PolicyError) as raised:
            receive(changed, state=state)
        assert raised.value.code == "policy_digest_conflict"

    def test_revision_order_is_numeric_not_lexicographic(self, tmp_path):
        state = tmp_path / "state.json"
        receive(policy_document(9), state=state)
        snapshot = receive(policy_document(10), state=state)
        assert snapshot["state"]["previous_revision"] == 9
        with pytest.raises(policy.PolicyError) as raised:
            receive(policy_document(9), state=state)
        assert raised.value.code == "policy_rollback"

    def test_corrupt_state_is_not_silently_replaced(self, tmp_path):
        state = tmp_path / "state.json"
        state.write_text('{"schema_version": 1, "kind": "wrong"}', encoding="utf-8")
        with pytest.raises(policy.PolicyError) as raised:
            receive(state=state)
        assert raised.value.code == "policy_state_corrupt"
        assert json.loads(state.read_text(encoding="utf-8"))["kind"] == "wrong"

    def test_failed_receive_keeps_the_previous_state(self, tmp_path):
        state = tmp_path / "state.json"
        receive(state=state)
        before = state.read_bytes()
        expired = dt.datetime(2027, 12, 1, tzinfo=dt.UTC)
        with pytest.raises(policy.PolicyError):
            receive(policy_document(8), state=state, now=expired)
        assert state.read_bytes() == before
        assert not list(tmp_path.glob("*.tmp"))


class TestDecision:
    def snapshot(self, tmp_path, document=None):
        return receive(document, state=tmp_path / "state.json")

    def asset(self, **overrides):
        value = {
            "tool_id": "osv-scanner",
            "access_id": "dependency-security",
            "version": "2.6.0",
            "platform": {"os": "linux", "arch": "x86_64"},
            "source": "https://github.com/google/osv-scanner/releases/download/v2.6.0/osv-scanner_linux_amd64",
            "digest": "ca69b3d3cd08f889a49dc0a383122f71cc528b83803671df5fd874d97485b108",
        }
        value.update(overrides)
        return value

    def test_deny_overrides_allow_even_for_a_complete_asset(self, tmp_path):
        snapshot = self.snapshot(tmp_path)
        decision = policy.decide(
            snapshot,
            self.asset(version="3.0.0"),
            selection_id="selection-1",
            selection_policy_revision="caller-policy-1",
            protected_profile="ci-security",
            now=NOW,
        )
        assert decision["decision"] == "deny"
        assert decision["allowed"] is False
        assert decision["rule_id"] == "deny-osv-3"

    def test_allowed_asset_carries_policy_and_selection_identities(self, tmp_path):
        snapshot = self.snapshot(tmp_path)
        decision = policy.decide(
            snapshot,
            self.asset(),
            selection_id="selection-1",
            selection_policy_revision="caller-policy-1",
            protected_profile="ci-security",
            now=NOW,
        )
        assert decision["decision"] == "allow"
        assert decision["allowed"] is True
        assert decision["rule_id"] == "allow-osv-2.6.0"
        assert decision["policy"]["revision"] == 7
        assert decision["policy"]["sha256"] == snapshot["policy"]["sha256"]
        assert decision["selection"] == {
            "selection_id": "selection-1",
            "policy_revision": "caller-policy-1",
        }

    def test_unmatched_protected_asset_is_unknown_not_allowed(self, tmp_path):
        snapshot = self.snapshot(tmp_path)
        decision = policy.decide(
            snapshot,
            self.asset(tool_id="unknown-tool"),
            selection_id="selection-1",
            protected_profile="ci-security",
            now=NOW,
        )
        assert decision["decision"] == "unknown"
        assert decision["allowed"] is False
        assert decision["reason"] == "no_matching_rule"

    def test_protected_profile_must_be_covered_by_the_snapshot(self, tmp_path):
        snapshot = self.snapshot(tmp_path)
        decision = policy.decide(
            snapshot,
            self.asset(),
            selection_id="selection-1",
            protected_profile="ci-release",
            now=NOW,
        )
        assert decision["decision"] == "unknown"
        assert decision["reason"] == "protected_profile_not_covered"
        assert decision["allowed"] is False

    def test_new_policy_can_authorize_an_older_tool_version(self, tmp_path):
        state = tmp_path / "state.json"
        receive(policy_document(7), state=state)
        document = policy_document(8)
        document["rules"] = [
            {
                "rule_id": "allow-older-osv",
                "effect": "allow",
                "reason": "approved rollback of the scanner",
                "asset": {
                    "tool_id": "osv-scanner",
                    "version": "2.5.0",
                    "platform": {"os": "linux", "arch": "x86_64"},
                },
            }
        ]
        snapshot = receive(document, state=state)
        decision = policy.decide(
            snapshot,
            self.asset(version="2.5.0"),
            selection_id="selection-2",
            protected_profile="ci-security",
            now=NOW,
        )
        assert decision["decision"] == "allow"
        assert decision["policy"]["revision"] == 8

    def test_expired_snapshot_is_not_usable_at_decision_time(self, tmp_path):
        snapshot = self.snapshot(tmp_path)
        decision = policy.decide(
            snapshot,
            self.asset(),
            selection_id="selection-1",
            protected_profile="ci-security",
            now=dt.datetime(2027, 12, 1, tzinfo=dt.UTC),
        )
        assert decision["decision"] == "unknown"
        assert decision["reason"] == "policy_expired"
        assert decision["allowed"] is False


class TestCommandLine:
    def test_digest_command_prints_the_example_digest(self, capsys):
        assert policy.main(["digest", "--policy", str(EXAMPLE)]) == 0
        assert capsys.readouterr().out.strip() == policy.digest(EXAMPLE.read_bytes())
