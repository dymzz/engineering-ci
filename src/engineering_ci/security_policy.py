"""Trusted security-policy snapshots, publication records and per-asset decisions.

Engineering CI owns the publication and reception contract; Runtime consumes
the verified snapshot/decision documents without importing this package.

Trust is not established by the policy file. A policy release is trusted only
when its publication record matches a *published* release manifest that the
existing release verifier accepts (``release.manifest.validate`` with
``published=True``), and when the consumer's out-of-band anchor names the same
repository and release workflow. PR edits, a cached ``enabled`` flag, a
neighbouring hash or an unreviewed manifest never establish trust.

The receiver keeps one accepted revision in a local state document. A policy can
only advance that revision; an older revision is a rollback, and the same
revision with different bytes is a conflict. Missing or corrupt state is refused
for protected operations; initial provisioning requires an explicit bootstrap
against the trusted anchor. Local state and the system clock are ordinary files
and cannot be protected from a subject with the same host privileges.

The policy matcher uses Runtime #27's ``tool_id`` and ``access_id`` fields as
opaque values. Semantic validation of those identities belongs to Runtime #27;
this format does not introduce another capability or asset identity.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from .release import manifest as release_manifest

POLICY_KIND = "engineering-ci-security-policy"
PUBLICATION_KIND = "engineering-ci-security-policy-publication"
ANCHOR_KIND = "engineering-ci-security-policy-anchor"
STATE_KIND = "engineering-ci-security-policy-state"
SNAPSHOT_KIND = "engineering-ci-security-policy-snapshot"
DECISION_KIND = "engineering-ci-security-policy-decision"
SCHEMA_VERSION = 1

MAX_POLICY_BYTES = 1024 * 1024
MAX_DOCUMENT_BYTES = 256 * 1024
MAX_RULES = 1000
MAX_TEXT = 512

REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKFLOW = re.compile(r"^\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml$")
VERSION_TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
SHA = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
RELEASE_URL = re.compile(r"^https://api\.github\.com/repos/.+/releases/[1-9][0-9]*$")
EFFECTS = {"allow", "deny"}
ASSET_FIELDS = ("tool_id", "access_id", "version", "platform", "source", "digest")
PLATFORM_FIELDS = ("os", "arch")


class PolicyError(ValueError):
    """A policy document or trust decision was refused for a stable reason."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _text(name, value, limit=MAX_TEXT):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or any(character in value for character in "\0\r\n")
    ):
        raise PolicyError(f"{name}_invalid")
    return value


def _optional_text(value, limit=MAX_TEXT):
    return None if value is None else _text("policy_document", value, limit)


def _integer(name, value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise PolicyError(f"{name}_invalid")
    return value


def _timestamp(value, code="policy_document_invalid"):
    if not isinstance(value, str) or len(value) > 64:
        raise PolicyError(code)
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        raise PolicyError(code) from None
    if parsed.tzinfo is None:
        raise PolicyError(code)
    return parsed.astimezone(dt.UTC)


def _format(value):
    return value.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now(value=None):
    if value is None:
        return dt.datetime.now(dt.UTC)
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise PolicyError("clock_invalid")
    return value.astimezone(dt.UTC)


def _object(name, value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise PolicyError(f"{name}_invalid")
    return value


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise PolicyError("policy_document_invalid")
        value[key] = item
    return value


def _load_json(raw, code, limit=MAX_DOCUMENT_BYTES):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise PolicyError(code) from None
    if not isinstance(raw, str) or not raw.strip():
        raise PolicyError(code)
    if len(raw.encode("utf-8")) > limit:
        raise PolicyError("policy_size_exceeded")
    try:
        value = json.loads(raw, object_pairs_hook=_unique)
    except PolicyError:
        raise
    except ValueError:
        raise PolicyError(code) from None
    if not isinstance(value, dict):
        raise PolicyError(code)
    return value


def encode(value):
    """Canonical JSON used for digests and local records."""
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def digest(raw):
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, bytes):
        raise TypeError("digest requires text or bytes")
    return hashlib.sha256(raw).hexdigest()


def _bytes(value):
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, dict):
        return encode(value).encode("utf-8")
    raise PolicyError("policy_document_invalid")


def _platform(value):
    if value is None:
        return None
    _object("policy_document", value, PLATFORM_FIELDS)
    return {
        "os": _text("policy_document", value["os"], 32),
        "arch": _text("policy_document", value["arch"], 32),
    }


def _asset(value):
    if not isinstance(value, dict) or not set(value) <= set(ASSET_FIELDS):
        raise PolicyError("policy_document_invalid")
    if set(value) != set(ASSET_FIELDS):
        # The canonical form always carries every selector field.
        value = {**{key: None for key in ASSET_FIELDS}, **value}
    if "tool_id" not in value or value["tool_id"] is None:
        raise PolicyError("policy_document_invalid")
    return {
        "tool_id": _text("policy_document", value["tool_id"], 128),
        "access_id": _optional_text(value["access_id"], 128),
        "version": _optional_text(value["version"], 128),
        "platform": _platform(value["platform"]),
        "source": _optional_text(value["source"], 2048),
        "digest": _optional_text(value["digest"], 128),
    }


def _rule(value):
    _object("policy_document", value, ("rule_id", "effect", "reason", "asset"))
    if value["effect"] not in EFFECTS:
        raise PolicyError("policy_document_invalid")
    return {
        "rule_id": _text("policy_document", value["rule_id"], 128),
        "effect": value["effect"],
        "reason": _text("policy_document", value["reason"], MAX_TEXT),
        "asset": _asset(value["asset"]),
    }


def load_policy(raw):
    """Validate one policy document; it is data, not a trust anchor."""
    value = _load_json(raw, "policy_document_invalid", MAX_POLICY_BYTES)
    _object(
        "policy_document",
        value,
        (
            "schema_version",
            "kind",
            "revision",
            "minimum_revision",
            "issued_at",
            "not_before",
            "expires_at",
            "publisher",
            "protected_profiles",
            "rules",
        ),
    )
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != SCHEMA_VERSION
    ):
        raise PolicyError("policy_schema_unsupported")
    if value["kind"] != POLICY_KIND:
        raise PolicyError("policy_document_invalid")
    revision = _integer("policy_revision", value["revision"], 1, 2_147_483_647)
    minimum = _integer(
        "policy_minimum_revision", value["minimum_revision"], 1, revision
    )
    issued_at = _timestamp(value["issued_at"])
    not_before = _timestamp(value["not_before"])
    expires_at = _timestamp(value["expires_at"])
    if not_before >= expires_at or issued_at > expires_at:
        raise PolicyError("policy_window_invalid")
    _object("policy_document", value["publisher"], ("repository", "workflow"))
    if not REPOSITORY.fullmatch(value["publisher"]["repository"]):
        raise PolicyError("policy_document_invalid")
    if not WORKFLOW.fullmatch(value["publisher"]["workflow"]):
        raise PolicyError("policy_document_invalid")
    profiles = value["protected_profiles"]
    if (
        not isinstance(profiles, list)
        or not 1 <= len(profiles) <= 32
        or len(set(profiles)) != len(profiles)
    ):
        raise PolicyError("policy_document_invalid")
    profiles = [_text("policy_document", item, 128) for item in profiles]
    rules = value["rules"]
    if not isinstance(rules, list) or not 1 <= len(rules) <= MAX_RULES:
        raise PolicyError("policy_document_invalid")
    rules = [_rule(item) for item in rules]
    if len({item["rule_id"] for item in rules}) != len(rules):
        raise PolicyError("policy_document_invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": POLICY_KIND,
        "revision": revision,
        "minimum_revision": minimum,
        "issued_at": _format(issued_at),
        "not_before": _format(not_before),
        "expires_at": _format(expires_at),
        "publisher": {
            "repository": value["publisher"]["repository"],
            "workflow": value["publisher"]["workflow"],
        },
        "protected_profiles": profiles,
        "rules": rules,
    }


def load_publication(raw):
    value = _load_json(raw, "publication_invalid")
    _object(
        "publication",
        value,
        (
            "schema_version",
            "kind",
            "published_at",
            "policy",
            "release",
            "release_manifest_sha256",
        ),
    )
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != SCHEMA_VERSION
        or value["kind"] != PUBLICATION_KIND
    ):
        raise PolicyError("publication_invalid")
    policy = _object(
        "publication", value["policy"], ("name", "sha256", "size", "revision")
    )
    release = _object(
        "publication",
        value["release"],
        ("repository", "tag", "commit_sha", "release_url", "run_id", "run_attempt"),
    )
    if (
        policy["name"] != "security-policy.json"
        or not isinstance(policy["sha256"], str)
        or not HEX64.fullmatch(policy["sha256"])
        or type(policy["size"]) is not int
        or not 0 < policy["size"] <= MAX_POLICY_BYTES
        or type(policy["revision"]) is not int
        or policy["revision"] < 1
        or not REPOSITORY.fullmatch(str(release["repository"]))
        or not VERSION_TAG.fullmatch(str(release["tag"]))
        or not SHA.fullmatch(str(release["commit_sha"]))
        or not RELEASE_URL.fullmatch(str(release["release_url"]))
        or type(release["run_id"]) is not int
        or release["run_id"] < 1
        or type(release["run_attempt"]) is not int
        or release["run_attempt"] < 1
        or not HEX64.fullmatch(str(value["release_manifest_sha256"]))
    ):
        raise PolicyError("publication_invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": PUBLICATION_KIND,
        "published_at": _format(
            _timestamp(value["published_at"], "publication_invalid")
        ),
        "policy": {
            "name": policy["name"],
            "sha256": policy["sha256"],
            "size": policy["size"],
            "revision": policy["revision"],
        },
        "release": {
            "repository": release["repository"],
            "tag": release["tag"],
            "commit_sha": release["commit_sha"],
            "release_url": release["release_url"],
            "run_id": release["run_id"],
            "run_attempt": release["run_attempt"],
        },
        "release_manifest_sha256": value["release_manifest_sha256"],
    }


def load_anchor(raw):
    value = _load_json(raw, "trust_anchor_invalid")
    _object(
        "anchor",
        value,
        (
            "schema_version",
            "kind",
            "repository",
            "release_workflow",
            "minimum_policy_revision",
        ),
    )
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != SCHEMA_VERSION
        or value["kind"] != ANCHOR_KIND
        or not REPOSITORY.fullmatch(str(value["repository"]))
        or not WORKFLOW.fullmatch(str(value["release_workflow"]))
        or type(value["minimum_policy_revision"]) is not int
        or value["minimum_policy_revision"] < 1
    ):
        raise PolicyError("trust_anchor_invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": ANCHOR_KIND,
        "repository": value["repository"],
        "release_workflow": value["release_workflow"],
        "minimum_policy_revision": value["minimum_policy_revision"],
    }


def _load_state(raw):
    value = _load_json(raw, "policy_state_corrupt", MAX_DOCUMENT_BYTES)
    _object(
        "state",
        value,
        (
            "schema_version",
            "kind",
            "revision",
            "policy_sha256",
            "publication_sha256",
            "accepted_at",
            "release",
        ),
    )
    release = _object(
        "state",
        value["release"],
        ("repository", "tag", "commit_sha", "release_url", "run_id", "run_attempt"),
    )
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != SCHEMA_VERSION
        or value["kind"] != STATE_KIND
        or type(value["revision"]) is not int
        or value["revision"] < 1
        or not HEX64.fullmatch(str(value["policy_sha256"]))
        or not HEX64.fullmatch(str(value["publication_sha256"]))
        or not isinstance(release, dict)
        or not REPOSITORY.fullmatch(str(release.get("repository")))
        or not VERSION_TAG.fullmatch(str(release.get("tag")))
        or not SHA.fullmatch(str(release.get("commit_sha")))
        or not RELEASE_URL.fullmatch(str(release.get("release_url")))
        or type(release.get("run_id")) is not int
        or type(release.get("run_attempt")) is not int
    ):
        raise PolicyError("policy_state_corrupt")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": STATE_KIND,
        "revision": value["revision"],
        "policy_sha256": value["policy_sha256"],
        "publication_sha256": value["publication_sha256"],
        "accepted_at": _format(
            _timestamp(value["accepted_at"], "policy_state_corrupt")
        ),
        "release": dict(release),
    }


def load_state(raw):
    try:
        return _load_state(raw)
    except PolicyError as error:
        if error.code == "policy_state_corrupt":
            raise
        raise PolicyError("policy_state_corrupt") from None


def _verify_release(manifest):
    """Reuse the existing publication contract; never trust a bare JSON file."""
    try:
        release_manifest.validate(
            manifest,
            manifest["repository"],
            manifest["release"]["tag"],
            manifest["release"]["commit_sha"],
            published=True,
        )
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise PolicyError("release_manifest_invalid") from error
    if manifest["result"] != "passed":
        raise PolicyError("release_manifest_invalid")
    return manifest


def _release_identity(manifest):
    return {
        "repository": manifest["repository"],
        "tag": manifest["release"]["tag"],
        "commit_sha": manifest["release"]["commit_sha"],
        "release_url": manifest["release"]["release_url"],
        "run_id": manifest["workflow"]["run_id"],
        "run_attempt": manifest["workflow"]["run_attempt"],
    }


def publish(policy_raw, manifest, *, now=None):
    """Build the publication record for a policy and a published release."""
    raw = _bytes(policy_raw)
    document = load_policy(raw)
    _verify_release(manifest)
    if document["publisher"]["repository"] != manifest["repository"]:
        raise PolicyError("policy_publisher_mismatch")
    if document["publisher"]["workflow"] != manifest["workflow"]["path"]:
        raise PolicyError("policy_publisher_mismatch")
    value = {
        "schema_version": SCHEMA_VERSION,
        "kind": PUBLICATION_KIND,
        "published_at": _format(_now(now)),
        "policy": {
            "name": "security-policy.json",
            "sha256": digest(raw),
            "size": len(raw),
            "revision": document["revision"],
        },
        "release": _release_identity(manifest),
        "release_manifest_sha256": digest(encode(manifest)),
    }
    load_publication(encode(value))
    return value


def _write_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(encode(state), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _snapshot(document, publication, publication_sha256, now, initialized, previous):
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": SNAPSHOT_KIND,
        "policy": {
            "revision": document["revision"],
            "minimum_revision": document["minimum_revision"],
            "issued_at": document["issued_at"],
            "not_before": document["not_before"],
            "expires_at": document["expires_at"],
            "publisher": document["publisher"],
            "protected_profiles": document["protected_profiles"],
            "rules": document["rules"],
            "sha256": publication["policy"]["sha256"],
            "size": publication["policy"]["size"],
        },
        "publication": {
            "sha256": publication_sha256,
            "published_at": publication["published_at"],
        },
        "release": publication["release"],
        "verified_at": _format(now),
        "state": {"initialized": initialized, "previous_revision": previous},
    }


def receive(
    policy_raw,
    publication_raw,
    manifest,
    anchor_raw,
    state_path,
    *,
    now=None,
    bootstrap=False,
):
    """Verify a publication and atomically advance the accepted policy revision."""
    now = _now(now)
    raw = _bytes(policy_raw)
    publication_bytes = _bytes(publication_raw)
    document = load_policy(raw)
    publication = load_publication(publication_bytes)
    trust = load_anchor(anchor_raw)
    _verify_release(manifest)
    if (
        trust["repository"] != manifest["repository"]
        or trust["release_workflow"] != manifest["workflow"]["path"]
    ):
        raise PolicyError("publication_publisher_untrusted")
    if (
        document["publisher"]["repository"] != manifest["repository"]
        or document["publisher"]["workflow"] != manifest["workflow"]["path"]
    ):
        raise PolicyError("policy_publisher_mismatch")
    if publication["release"] != _release_identity(manifest):
        raise PolicyError("publication_manifest_mismatch")
    if publication["release_manifest_sha256"] != digest(encode(manifest)):
        raise PolicyError("publication_manifest_mismatch")
    if publication["policy"]["sha256"] != digest(raw):
        raise PolicyError("policy_digest_mismatch")
    if publication["policy"]["size"] != len(raw):
        raise PolicyError("policy_size_mismatch")
    if publication["policy"]["revision"] != document["revision"]:
        raise PolicyError("policy_revision_mismatch")
    if now < _timestamp(document["not_before"]):
        raise PolicyError("policy_not_yet_valid")
    if now > _timestamp(document["expires_at"]):
        raise PolicyError("policy_expired")
    if document["revision"] < trust["minimum_policy_revision"]:
        raise PolicyError("policy_below_minimum")

    state_file = Path(state_path)
    state = None
    if state_file.exists():
        state = load_state(state_file.read_bytes())
    if state is None:
        if not bootstrap:
            raise PolicyError("policy_state_missing")
        initialized, previous = True, None
    else:
        publication_sha = digest(publication_bytes)
        if (
            document["revision"] == state["revision"]
            and publication["policy"]["sha256"] == state["policy_sha256"]
        ):
            if publication_sha != state["publication_sha256"]:
                raise PolicyError("policy_publication_conflict")
            return _snapshot(
                document,
                publication,
                publication_sha,
                now,
                initialized=False,
                previous=state["revision"],
            )
        if document["revision"] < state["revision"]:
            raise PolicyError("policy_rollback")
        if document["revision"] == state["revision"]:
            raise PolicyError("policy_digest_conflict")
        initialized, previous = False, state["revision"]

    _write_state(
        state_file,
        {
            "schema_version": SCHEMA_VERSION,
            "kind": STATE_KIND,
            "revision": document["revision"],
            "policy_sha256": publication["policy"]["sha256"],
            "publication_sha256": digest(publication_bytes),
            "accepted_at": _format(now),
            "release": publication["release"],
        },
    )
    return _snapshot(
        document,
        publication,
        digest(publication_bytes),
        now,
        initialized=initialized,
        previous=previous,
    )


def _asset_input(value):
    if not isinstance(value, dict) or not set(value) <= set(ASSET_FIELDS):
        raise PolicyError("asset_invalid")
    for key in ("tool_id", "version", "platform"):
        if key not in value:
            raise PolicyError("asset_invalid")
    return {
        "tool_id": _text("asset", value["tool_id"], 128),
        "access_id": _optional_text(value.get("access_id"), 128),
        "version": _text("asset", value["version"], 128),
        "platform": _platform(value["platform"]),
        "source": _optional_text(value.get("source"), 2048),
        "digest": _optional_text(value.get("digest"), 128),
    }


def _matches(selector, asset):
    for field in ("tool_id", "access_id", "version", "source", "digest"):
        if selector[field] is not None and selector[field] != asset[field]:
            return False
    return not (
        selector["platform"] is not None and selector["platform"] != asset["platform"]
    )


def decide(
    snapshot,
    asset,
    *,
    selection_id,
    selection_policy_revision=None,
    protected_profile=None,
    now=None,
):
    """Return a structured decision for one asset against a verified snapshot.

    Deny rules win over allow rules. No matching rule is ``unknown`` and is not
    allowed; a protected caller must not treat ``unknown`` as permission.
    """
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("kind") != SNAPSHOT_KIND
        or snapshot.get("schema_version") != SCHEMA_VERSION
        or not isinstance(snapshot.get("policy"), dict)
        or not isinstance(snapshot["policy"].get("rules"), list)
    ):
        raise PolicyError("policy_snapshot_invalid")
    now = _now(now)
    policy_document = snapshot["policy"]
    if now > _timestamp(policy_document["expires_at"], "policy_snapshot_invalid"):
        decision, reason, rule_id = "unknown", "policy_expired", None
    elif now < _timestamp(policy_document["not_before"], "policy_snapshot_invalid"):
        decision, reason, rule_id = "unknown", "policy_not_yet_valid", None
    elif (
        protected_profile is not None
        and protected_profile not in policy_document["protected_profiles"]
    ):
        decision, reason, rule_id = "unknown", "protected_profile_not_covered", None
    else:
        selected = _asset_input(asset)
        decision, reason, rule_id = "unknown", "no_matching_rule", None
        for rule in policy_document["rules"]:
            if rule["effect"] == "deny" and _matches(rule["asset"], selected):
                decision, reason, rule_id = "deny", "matched_deny", rule["rule_id"]
                break
        else:
            for rule in policy_document["rules"]:
                if rule["effect"] == "allow" and _matches(rule["asset"], selected):
                    decision, reason, rule_id = (
                        "allow",
                        "matched_allow",
                        rule["rule_id"],
                    )
                    break
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": DECISION_KIND,
        "decision": decision,
        "allowed": decision == "allow",
        "reason": reason,
        "rule_id": rule_id,
        "policy": {
            "revision": policy_document.get("revision"),
            "sha256": policy_document.get("sha256"),
            "release": snapshot.get("release"),
        },
        "selection": {
            "selection_id": _text("selection", selection_id, 256),
            "policy_revision": _optional_text(selection_policy_revision, 256),
        },
        "asset": asset,
        "protected_profile": protected_profile,
        "evaluated_at": _format(now),
    }


def _print(value):
    sys.stdout.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="engineering-ci policy",
        description="Publish, receive and evaluate trusted security-policy snapshots.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    digest_parser = commands.add_parser("digest")
    digest_parser.add_argument("--policy", type=Path, required=True)
    publish_parser = commands.add_parser("publish")
    publish_parser.add_argument("--policy", type=Path, required=True)
    publish_parser.add_argument("--release-manifest", type=Path, required=True)
    publish_parser.add_argument("--out", type=Path, required=True)
    receive_parser = commands.add_parser("receive")
    receive_parser.add_argument("--policy", type=Path, required=True)
    receive_parser.add_argument("--publication", type=Path, required=True)
    receive_parser.add_argument("--release-manifest", type=Path, required=True)
    receive_parser.add_argument("--anchor", type=Path, required=True)
    receive_parser.add_argument("--state", type=Path, required=True)
    receive_parser.add_argument("--bootstrap", action="store_true")
    decide_parser = commands.add_parser("decide")
    decide_parser.add_argument("--snapshot", type=Path, required=True)
    decide_parser.add_argument("--asset", type=Path, required=True)
    decide_parser.add_argument("--selection-id", required=True)
    decide_parser.add_argument("--selection-policy-revision")
    decide_parser.add_argument("--protected-profile")
    state_parser = commands.add_parser("state")
    state_parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "digest":
            sys.stdout.write(digest(args.policy.read_bytes()) + "\n")
            return 0
        if args.command == "publish":
            value = publish(
                args.policy.read_bytes(),
                json.loads(args.release_manifest.read_text(encoding="utf-8")),
            )
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(encode(value), encoding="utf-8")
            _print(value)
            return 0
        if args.command == "receive":
            snapshot = receive(
                args.policy.read_bytes(),
                args.publication.read_bytes(),
                json.loads(args.release_manifest.read_text(encoding="utf-8")),
                args.anchor.read_bytes(),
                args.state,
                bootstrap=args.bootstrap,
            )
            _print(snapshot)
            return 0
        if args.command == "decide":
            snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
            asset = json.loads(args.asset.read_text(encoding="utf-8"))
            _print(
                decide(
                    snapshot,
                    asset,
                    selection_id=args.selection_id,
                    selection_policy_revision=args.selection_policy_revision,
                    protected_profile=args.protected_profile,
                )
            )
            return 0
        _print(load_state(args.state.read_bytes()))
        return 0
    except PolicyError as error:
        parser.exit(1, f"policy rejected: {error.code}\n")


if __name__ == "__main__":
    raise SystemExit(main())
