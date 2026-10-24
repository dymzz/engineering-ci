# Trusted security policy — local verification

Scope: [ECI-CICD-CORE-01A](https://github.com/dymzz/engineering-ci/issues/56).
Date: 2026-10-24; host: Windows 11, CPython 3.14.3.
Contract: [security policy snapshots](../architecture/security-policy.md).

No formal Release was performed. Publication is exercised against the existing
`release-manifest` *contract fixture* used by the release tests, not against a
new published policy Release. Positive controlled-API tests now exercise the
actual existing Release verifier; #53 still owns publishing the real policy
assets, and #57 owns connecting Runtime/execution admission. A real read-only
negative check against the existing v0.7.0 Release is recorded below. These
evidence categories are separate.

## Real policy input

[`config/security-policy.example.json`](../../config/security-policy.example.json)
is a complete, schema-valid publish input:

| Field | Value |
| --- | --- |
| revision / minimum_revision | `7` / `1` |
| window | `2026-10-24T00:00:00Z` … `2027-10-24T00:00:00Z` |
| publisher | `dymzz/engineering-ci` / `.github/workflows/release.yml` |
| protected profiles | `ci-security` |
| rules | `allow-osv-2.6.0` (tool/platform/version/digest) and `deny-osv-3` |
| SHA-256 | `fbed0d93f015cc3418d5008443c22963d65c1321a7539f65f183b4df7cda5514` |

The digest is the exact file digest and also the canonical re-encoding digest;
`tests/test_security_policy.py::TestPolicyDocument` asserts both are equal, so
a consumer cannot get a different identity by normalising the document.

```sh
python -m engineering_ci.security_policy digest \
  --policy config/security-policy.example.json
# fbed0d93f015cc3418d5008443c22963d65c1321a7539f65f183b4df7cda5514
```

## Exercised contract

`tests/test_security_policy.py` and the unified-CLI delegation case ran locally:

```sh
python -m pytest -q tests/test_security_policy.py tests/test_unified_cli.py
# 45 passed
```

Covered behaviours, each observed directly:

| Behaviour | Observed |
| --- | --- |
| Publication binding | Policy digest/size/revision, release identity and Manifest SHA-256 are bound; a failed or `validation` Manifest is refused |
| Publisher trust | Anchor repository/workflow mismatch is `publication_publisher_untrusted`; a policy claiming another workflow is `policy_publisher_mismatch` |
| First receipt | No state without `--bootstrap` is `policy_state_missing`; bootstrap writes the state and returns a snapshot with `initialized: true` |
| Bootstrap floor | Revision below the anchor minimum is `policy_below_minimum` and writes nothing |
| Window | Before `not_before` is `policy_not_yet_valid`; after `expires_at` is `policy_expired` |
| Rollback | A lower revision is `policy_rollback`; same revision with changed bytes is `policy_digest_conflict` |
| Numeric order | Revision 10 advances from 9; revision 9 is then refused |
| Idempotency | Re-receiving the identical publication returns `initialized: false` and rewrites nothing |
| Corruption | Invalid state is `policy_state_corrupt` and is left untouched |
| Atomicity | A refused receive leaves the previous state byte-identical; no `*.tmp` remains |
| Deny precedence | A complete `3.0.0` asset matching the deny rule is denied even though a cached installation may exist |
| Unknown is not allow | No matching rule or uncovered protected profile yields `allowed: false` |
| Older tool version | A new revision (`8`) explicitly allows scanner `2.5.0`; policy rollback remains separate |
| Decision-time expiry | A snapshot received earlier becomes `unknown`/`policy_expired` when evaluated after its window |
| Identity separation | Decision carries the original `selection_id`/Runtime policy revision and the current policy revision/hash separately |

Expected snapshots are asserted structurally in the tests: policy revision,
SHA-256, release identity, protected profiles, verified time and state transition
(`initialized`, `previous_revision`). They are not committed as fixtures because
the release identity comes from the test Manifest and must not be mistaken for a
published record.

## Limits

- The Manifest is a contract fixture (`example/ci`); publication against the
  real repository Release remains #53 integration work.
- Runtime #27 has not delivered `tool_id`/`access_id` records. The matcher
  treats both as opaque strings and does not claim the identity model is final.
- Clock and local state are ordinary host files; the contract refuses missing or
  corrupt state and requires explicit re-provisioning, but does not claim
  protection from a subject with equal host privileges.

## PR #62 review corrections — 2026-10-24

Reviewed initial head `9949e22dc03e4361bb46540ca5a4d51c1cefd3ae`. Its three green
PR jobs and 45 local targeted tests did not cover these reproduced defects:

| Defect at that head | Observed result | Correction |
| --- | --- | --- |
| Fabricated local Manifest/publication | Accepted and allowed with no network calls | Reception authenticates the real Release through existing verification, and compares exact same-Release policy/publication Asset bytes |
| Previously accepted allow snapshot after revision 8 revoked it | Saved revision 7 still allowed | Decisions require persistent state and its security snapshot digest; superseded snapshots are unknown/not allowed; modified contents are refused |
| Concurrent receivers | Revision 9 was overwritten by revision 8 | Native interprocess lock around the full read/compare/replace transaction; contention is explicit; unique flushed temporary files |

Regression tests were first run on the defective implementation: all three
failed for the corresponding behavior. After the fixes, the targeted policy,
CLI and native Release Asset transport tests pass: **67 passed, 3.99s** on
Windows CPython 3.14.3. The expanded affected suite (policy, CLI, native transport,
Manifest, manual Release, verification and Run behavior) passed **205 tests and
19 subtests in 5.15s**. Ruff and format checks passed. Final current-head CI is
recorded on [PR #62](https://github.com/dymzz/engineering-ci/pull/62).

Additional cases cover changed/missing/partially uploaded policy assets, Draft
Release rejection, unavailable GitHub preserving accepted state, tampered or
missing-state snapshots, noncanonical raw policy bytes, failed atomic replace,
tool-cache cleanup preserving an independent persistent baseline, a real
second-process lock contention, and CLI receive/decide on the same state.
The Windows CLI fixture preserves raw bytes explicitly; newline translation is
not allowed to redefine a publication digest.

### Actual GitHub read-only check

The existing [v0.7.0 Release](https://github.com/dymzz/engineering-ci/releases/tag/v0.7.0)
and its attached Manifest were queried through the production reception path:

- Tag/Manifest commit: `9d03ade4f2af89538c9a0362beae55c9a0835e18`.
- Release Manifest Run: [39614205390](https://github.com/dymzz/engineering-ci/actions/runs/39614205390), attempt 1.
- Input: the real example policy, paired with a newly constructed publication
  naming that genuine Release. The Release does **not** contain those policy assets.
- Result: `publication_untrusted`; no receiver state was created.

This proves that a genuine Manifest no longer authenticates an unpublished
policy. It does **not** prove successful production policy publication or
successful live receipt. No Tag, Release, Asset or Workflow was created or
modified by this check. The positive path remains controlled-API evidence until
#53 publishes the assets using the documented existing Draft operation.

### Handoff and acceptance scope

The delivered boundary is policy input/record/snapshot/decision contracts and
the actual trusted receiver implementation. Runtime imports no ECI package.
Tool identity semantics await Runtime #27; full execution-time and offline
admission await #57/Runtime #13. These integration tasks must reuse the same
contracts and cannot substitute cached enabled flags for current decisions.

State examples now use an explicitly provisioned persistent `protected-policy/`
location, outside clearable caches. Deliberately deleting that authority is a
recovery operation requiring its trusted revision floor, not routine bootstrap.
The release handoff also identifies the real point inside `publish_release`
after Draft creation and Release URL binding; a downloaded pre-publication
Manifest alone cannot be passed to `policy publish`.
