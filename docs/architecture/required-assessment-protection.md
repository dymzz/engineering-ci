# Required Assessment server protection observation (#61 G3/R3)

Status: additive #61 G3/R3 **read-only observation contract**
(`engineering_ci.required_assessment_protection`). It inspects
**branch-applicable** repository rules and Branch Protection for the stable
Required Assessment Check name (`engineering-ci / required-assessment`).

It does **not** mutate protection settings, publish the Check (#61 G2), re-run
Assessment (#61 R1), or authorize Release (#61 R4/R5).

## Observation shape

```text
inspect_server_protection(repository, branch=main, check_name=..., expected_source_app_id=...)
        |
        +--> GET rules/branches/{branch} (paginated; applicability authority)
        +--> GET rulesets (+ detail)     (scoped supplemental diagnostics)
        +--> GET branches/{branch}/protection
```

| Field | Meaning |
| --- | --- |
| `visibility.branch_rules` | `readable` / `forbidden` / `error` |
| `visibility.rulesets` | supplemental Ruleset list visibility |
| `visibility.branch_protection` | `readable` / `absent` / `forbidden` / `error` |
| `configuration.branch_rule_checks` / `branch_protection_checks` | Each context's source from Ruleset `integration_id` or Branch Protection `app_id`, and strictness when returned |
| `enforcement.source_app_status` | `matched` / `mismatch` / `unpinned` / `not_verified` for the selected context |
| `enforcement.status` | `configured` / `absent` / `not_verified` / `misconfigured` |
| `enforcement.probe` | always `not_performed` in this slice |
| `configuration.supplemental_ruleset_diagnostics` | non-authoritative disabled/evaluate hits |

## Enforcement status rules

- **configured** — applicable protection reads are complete and each
  occurrence of the selected context pins the approved `expected_source_app_id`.
  Supply the ID from Publisher approval. A v1 observation that only matched
  names validates with `enforcement_status=not_verified`.
- **absent** — complete authoritative applicable reads lack the Check. A
  missing *supplemental* Ruleset detail does not block this when branch rules
  and Branch Protection already prove absence.
- **not_verified** — applicable reads are incomplete or the selected context's
  Source App could not be compared with an approved ID.
- **misconfigured** — a selected context is unpinned or pins a different App
  when an approved ID is available. This also covers the case where
  authoritative sources omit the Check and a
  disabled/evaluate Ruleset is **proven applicable** to the queried branch via
  exact `refs/heads/{branch}` include (no wildcards). Release-only or patterned
  includes stay in supplemental diagnostics and do **not** mark main as
  misconfigured. Unknown `enforcement=None` is never treated as active.

## Permissions and server evidence

- Reading classic Branch Protection with an installation token requires
  repository `Administration: read`.
  [GitHub Branch Protection API](https://docs.github.com/en/rest/branches/branch-protection#get-branch-protection)
- Review admin enforcement and any visible Ruleset bypass actors alongside
  actual Check Runs and GitHub's native merge state for the same target SHA.
  Verify missing, failed and wrong-source checks against an authorized test PR.
- Keep Native Required Contexts independent. This observation uses GET only
  and leaves `enforcement.probe=not_performed`.

## Directed acceptance

`tests/test_required_assessment_protection.py` covers branch-applicable hits,
other-branch-only Rulesets, release-only disabled negatives, main-applicable
disabled/evaluate misconfiguration, pagination, detail-404 with authoritative
`absent`, dual-403 `not_verified`, and GET-only observation.
