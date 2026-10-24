# Trusted security policy snapshots

Status: implemented contract; formal policy publication is not performed here.
Scope: [ECI-CICD-CORE-01A](https://github.com/dymzz/engineering-ci/issues/56).

The security policy says which tool assets may execute and which are denied. It
is not a Runtime `AssetPolicy` revision: `AssetPolicy(revision, enabled,
specifier)` is a caller-supplied selection value. This page defines how a policy
is published, how a consumer establishes trust in it, how rollback is refused,
and what Runtime and ECI #57 consume.

Tool identity is the Runtime #27 field `tool_id`, with `access_id` for an
explicit entry. This repository keeps both values opaque: format v1 does not
introduce a second capability/asset identity or validate Runtime's registry
semantics. Until Runtime #27 delivers its records, policies can carry the agreed
field names but cannot claim the identity model is complete.

## Documents

All documents are bounded JSON, reject duplicate keys, and carry
`schema_version = 1`. Stable kinds let a consumer reject a document used in the
wrong role.

| Kind | Role | Produced by |
| --- | --- | --- |
| `engineering-ci-security-policy` | Rules, revision, window, publisher, protected profiles | Policy authoring (`config/security-policy.json` in #53) |
| `engineering-ci-security-policy-publication` | Binds policy bytes to a published Release and its Manifest | `engineering-ci policy publish` |
| `engineering-ci-security-policy-anchor` | Collector's out-of-band trust root | Protected host/Profile provisioning |
| `engineering-ci-security-policy-state` | Last accepted revision for one consumer | `engineering-ci policy receive` |
| `engineering-ci-security-policy-snapshot` | Verified policy + release identity for Runtime/#57 | `receive` output |
| `engineering-ci-security-policy-decision` | Per-asset allow/deny/unknown decision | `decide` output |

### Policy

```json
{
  "schema_version": 1,
  "kind": "engineering-ci-security-policy",
  "revision": 7,
  "minimum_revision": 1,
  "issued_at": "2026-10-24T00:00:00Z",
  "not_before": "2026-10-24T00:00:00Z",
  "expires_at": "2027-10-24T00:00:00Z",
  "publisher": {"repository": "dymzz/engineering-ci",
                "workflow": ".github/workflows/release.yml"},
  "protected_profiles": ["ci-security"],
  "rules": [
    {
      "rule_id": "allow-osv-2.6.0",
      "effect": "allow",
      "reason": "Reviewed stable OSV scanner payload.",
      "asset": {
        "tool_id": "osv-scanner",
        "access_id": null,
        "version": "2.6.0",
        "platform": {"os": "linux", "arch": "x86_64"},
        "source": null,
        "digest": "ca69b3d3cd08f889a49dc0a383122f71cc528b83803671df5fd874d97485b108"
      }
    }
  ]
}
```

- `revision` is the only ordering. It is a positive integer compared
  numerically; Git SHAs, tag names and file hashes never decide which policy is
  newer. `minimum_revision` is the policy's own floor (for example after a
  revocation).
- `not_before`/`expires_at` are explicit UTC windows. Expiry refuses protected
  use; it never becomes an implicit allow.
- `asset` selector fields are all optional except `tool_id`; an absent field is
  a wildcard. A rule with `platform` requires both `os` and `arch`. `deny` wins
  over `allow`, and a complete/cached asset cannot bypass a matching deny.
- `protected_profiles` names the caller Profiles this policy covers. A caller
  that presents an uncovered protected profile gets `unknown`, not allow.
- A new revision may explicitly allow an older tool version. That is a tool
  version decision and is independent of policy revision rollback.

The committed example is [config/security-policy.example.json](../../config/security-policy.example.json).

### Publication

```json
{
  "schema_version": 1,
  "kind": "engineering-ci-security-policy-publication",
  "published_at": "2026-10-24T12:00:00Z",
  "policy": {"name": "security-policy.json", "sha256": "<64 hex>",
             "size": 1234, "revision": 7},
  "release": {"repository": "dymzz/engineering-ci", "tag": "v0.7.0",
              "commit_sha": "<40 hex>", "release_url": "<GitHub API release URL>",
              "run_id": 123, "run_attempt": 1},
  "release_manifest_sha256": "<64 hex of the canonical release Manifest>"
}
```

`publish` builds the publication record; it does not authenticate files or make
a Release public. The supplied Manifest must pass the existing structural
`release.manifest.validate(..., published=True)` checks and contain the actual
Draft Release URL. This check alone does **not** prove a published Release.
The policy publisher must name the same repository and release workflow.

### Anchor and state

The anchor is provisioned by the protected host or Profile, never read from the
policy:

```json
{
  "schema_version": 1,
  "kind": "engineering-ci-security-policy-anchor",
  "repository": "dymzz/engineering-ci",
  "release_workflow": ".github/workflows/release.yml",
  "minimum_policy_revision": 1
}
```

The state records `revision`, `policy_sha256`, `publication_sha256`,
`snapshot_sha256`, `accepted_at` and the release identity. The snapshot digest
binds its security-bearing contents (including rules and release), excluding
receipt-time/transition metadata. Store this authoritative state in a protected
persistent location, **outside `.cache/`, tool caches and temporary outputs**.
The caller provisions the path; there is no implicit cache or default allow.

A native process lock covers read/compare/replace; a competing receiver gets
`policy_state_busy` and must inspect state before retrying. A unique temporary
file is flushed and atomically replaced; rejected or failed updates leave the
previous state intact. An older unreleased state without `snapshot_sha256` is
refused as corrupt; no historical state or policy is silently reinterpreted.

## Trust and reception rules

1. **Publisher.** Reception uses native `gh` and the existing
   `release.verification.published` path to read the actual Release, Tag,
   commit metadata, Manifest, original Run Attempt/checks and Artifacts.
   The two policy assets must be uploaded on that same Release; their exact
   bytes, sizes and available GitHub digests must equal the supplied files.
   Locally fabricated publication/Manifest files or a real Manifest paired with
   an unpublished policy are `publication_untrusted`. Unavailable GitHub/API
   evidence is `publication_unavailable`; it cannot update state.
   Anchor repository and workflow must equal the verified Manifest's publisher.
   A Manifest for another repository,
   another workflow, a failed result or `validation` mode is refused
   (`release_manifest_invalid`, `publication_publisher_untrusted`).
2. **Integrity.** Policy SHA-256 and size must equal the publication record and
   the actual bytes; the publication's Manifest digest and release identity must
   equal the verified Manifest. A damaged policy or record is refused
   (`policy_digest_mismatch`, `policy_size_mismatch`,
   `publication_manifest_mismatch`).
3. **Window.** Refused before `not_before` and after `expires_at`
   (`policy_not_yet_valid`, `policy_expired`). `decide` re-checks the window
   because a previously received snapshot can expire later.
4. **First receipt.** With no state, `receive` refuses unless `--bootstrap` is
   explicit and the revision is at least the anchor's `minimum_policy_revision`.
   A PR, neighbouring hash or cached `enabled=true` never creates a baseline.
5. **Advance only.** An accepted revision can only be replaced by a higher one.
   A lower revision is `policy_rollback`; the same revision with different bytes
   is `policy_digest_conflict`; the same revision with a different publication
   record is `policy_publication_conflict`. Re-receiving the identical
   publication is idempotent and does not rewrite state.
6. **Corruption.** Invalid state is `policy_state_corrupt`; it is not silently
   replaced and does not fall back to allow. A refused receive leaves the
   previous state untouched.
7. **Recovery.** Restoring trust after deleting state requires re-provisioning
   the anchor (raising `minimum_policy_revision` if older revisions must stay
   refused) and an explicit bootstrap. Ordinary cache cleanup is not a trusted
   rollback baseline.
8. **Decisions.** `decide` requires the same persistent state. A previously
   received snapshot superseded by a newer accepted revision yields
   `unknown` / `policy_superseded`. Changed snapshot contents cannot reuse the
   old policy digest (`policy_snapshot_invalid`); missing/corrupt state refuses
   decisions. It does not contact GitHub, allowing offline evaluation of a
   currently accepted snapshot. #57 owns admission immediately before execution.

### Stable refusal codes

`policy_document_invalid`, `policy_schema_unsupported`, `policy_window_invalid`,
`policy_size_exceeded`, `policy_publisher_mismatch`, `release_manifest_invalid`,
`publication_invalid`, `publication_publisher_untrusted`,
`publication_manifest_mismatch`, `policy_digest_mismatch`,
`policy_size_mismatch`, `policy_revision_mismatch`, `policy_expired`,
`policy_not_yet_valid`, `policy_below_minimum`, `policy_state_missing`,
`policy_state_corrupt`, `policy_rollback`, `policy_digest_conflict`,
`policy_publication_conflict`, `trust_anchor_invalid`, `clock_invalid`,
`policy_snapshot_invalid`, `asset_invalid`, `publication_untrusted`,
`publication_unavailable`, `policy_state_busy`, `policy_input_unavailable`.

A refusal is a value (`PolicyError.code`); no refusal produces a snapshot,
state update or allow decision.

## Decision contract for Runtime and ECI #57

```json
{
  "schema_version": 1,
  "kind": "engineering-ci-security-policy-decision",
  "decision": "allow",
  "allowed": true,
  "reason": "matched_allow",
  "rule_id": "allow-osv-2.6.0",
  "policy": {"revision": 7, "sha256": "<64 hex>", "release": {"tag": "v0.7.0"}},
  "selection": {"selection_id": "<Runtime selection id>",
                "policy_revision": "<original AssetPolicy revision>"},
  "asset": {"tool_id": "osv-scanner", "access_id": "dependency-security",
            "version": "2.6.0", "platform": {"os": "linux", "arch": "x86_64"},
            "source": "...", "digest": "..."},
  "protected_profile": "ci-security",
  "evaluated_at": "2026-10-24T12:00:00Z"
}
```

- `decision` is `allow`, `deny` or `unknown`. `allowed` is true only for
  `allow`; protected callers must treat `unknown` as not permitted. Unprotected
  callers keep their existing explicit authorization behavior.
- Reasons: `matched_allow`, `matched_deny`, `no_matching_rule`,
  `policy_expired`, `policy_not_yet_valid`, `protected_profile_not_covered`,
  `policy_superseded`.
- `policy` is the current verified snapshot; `selection` keeps the original
  Runtime selection and its own policy revision. They are never merged or
  rewritten, and the decision never claims the old selection policy is current.
- The asset echo is the exact input that was evaluated. A cached installation,
  a previously allowed selection or a successful Dagger transfer does not
  replace this decision.
- Runtime does not import this Python package; it consumes the snapshot and
  decision JSON and preserves the same codes/reasons.

## Commands

```sh
engineering-ci policy digest --policy config/security-policy.example.json
engineering-ci policy publish --policy security-policy.json \
  --release-manifest release-manifest.json --out security-policy.publication.json
engineering-ci policy receive --policy security-policy.json \
  --publication security-policy.publication.json \
  --release-manifest release-manifest.json --anchor policy-anchor.json \
  --state protected-policy/state.json --bootstrap
engineering-ci policy decide --snapshot snapshot.json --asset asset.json \
  --state protected-policy/state.json \
  --selection-id <selection-id> --selection-policy-revision <old-revision> \
  --protected-profile ci-security
```

`receive` needs a configured native `gh` account with access to the anchored
repository and prints the verified snapshot. `publish` only generates the
publication record. Provision `protected-policy/` persistently outside cache
cleanup and untrusted PR inputs. Use `--bootstrap` only for deliberate initial
provisioning; ordinary updates omit it. Losing the protected state requires
explicit recovery of its revision floor, not repeating bootstrap from a stale
minimum. Neither reception nor decisions publish a Release or execute tools.

`decide` exits successfully when it produces a decision document, including
`deny` or `unknown`. That process exit status is not execution permission:
protected consumers must require `decision == "allow"` and `allowed == true`.

## Release integration point (not applied here)

The existing `publish` job downloads a Manifest whose `release_url` is still
null. Calling `policy publish` at that point is invalid, and there is no Draft
yet. #53 must connect inside the existing `publish_release` operation, after
Draft creation/resumption and preparation of the actual Release-bound Manifest,
before its final `draft=false` API call:

1. Generate the publication from the reviewed `config/security-policy.json` and
   the Manifest containing the actual Draft URL. The record builder does not
   require that Draft to be publicly released yet.
2. Upload `security-policy.json` and `security-policy.publication.json` to the
   Draft Release together with `release-manifest.json`, then run the existing
   final publication step.
3. On upload failure, keep the Draft/tag recovery behavior described in
   [release workflow](release/workflow.md); do not publish a final Release
   without its policy assets. Recovery reads and preserves already-uploaded
   records, including `published_at`, instead of regenerating a conflicting
   record. Changed policy contents require a higher `revision`.

#53 authors the policy and calls the same commands; #57 consumes snapshots and
decisions. This task does not publish a formal Release or change the Workflow.

## Limits

- No signing, PKI or remote policy service is introduced. Trust is the existing
  controlled GitHub Release/Manifest path plus the host-provided anchor.
- Local state and the system clock are ordinary files. A subject with the same
  host privileges can modify or delete them; the receiver can refuse missing or
  corrupt state and require re-provisioning, but cannot prove clock honesty or
  offline awareness of an unpublished revocation.
- The anchor covers one repository/workflow. Multi-publisher trust and key
  rotation are not implemented.
- Format v1 is the only supported format. An unsupported `schema_version` is
  refused; no historical signed policy is reinterpreted or silently migrated.
- The matcher is exact-string based on the fields above. Semantic identity
  validation, discovery and group admission remain Runtime #14/#27 work.
