# Trusted security policy — local verification

Scope: [ECI-CICD-CORE-01A](https://github.com/dymzz/engineering-ci/issues/56).
Date: 2026-10-24; host: Windows 11, CPython 3.14.3.
Contract: [security policy snapshots](../architecture/security-policy.md).

No formal Release was performed. Publication is exercised against the existing
`release-manifest` *contract fixture* used by the release tests, not against a
real published Release or Tag. That is the required delivery boundary for this
task; #53/ECI release integration will produce the real Manifest and assets.

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
