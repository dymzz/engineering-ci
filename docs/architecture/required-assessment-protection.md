# Required Assessment server protection observation (#61 G3/R3)

Status: additive #61 G3/R3 **read-only observation contract**
(`engineering_ci.required_assessment_protection`). It inspects
**branch-applicable** repository rules and Branch Protection for the stable
Required Assessment Check name (`engineering-ci / required-assessment`).

It does **not** mutate protection settings, publish the Check (#61 G2), re-run
Assessment (#61 R1), or authorize Release (#61 R4/R5).

## Observation shape

```text
inspect_server_protection(repository, branch=main, check_name=...)
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
| `enforcement.status` | `configured` / `absent` / `not_verified` / `misconfigured` |
| `enforcement.probe` | always `not_performed` in this slice |
| `configuration.supplemental_ruleset_diagnostics` | non-authoritative disabled/evaluate hits |

## Enforcement status rules

- **configured** — branch-applicable `rules/branches/{branch}` or Branch
  Protection lists the Check.
- **absent** — complete authoritative applicable reads lack the Check. A
  missing *supplemental* Ruleset detail does not block this when branch rules
  and Branch Protection already prove absence.
- **not_verified** — authoritative applicable reads are forbidden, errored, or
  pagination-incomplete so a negative cannot be proven.
- **misconfigured** — authoritative sources omit the Check, and a
  disabled/evaluate Ruleset is **proven applicable** to the queried branch via
  exact `refs/heads/{branch}` include (no wildcards). Release-only or patterned
  includes stay in supplemental diagnostics and do **not** mark main as
  misconfigured. Unknown `enforcement=None` is never treated as active.

## Limits

- Read-only; never mutates Rulesets / Branch Protection / Apps / bypasses.
- Does not claim Source App ownership, bypass absence, or controlled rejection.
- Incomplete authoritative page reads never yield `absent`.
- Does not replace Native Required Contexts.

## Directed acceptance

`tests/test_required_assessment_protection.py` covers branch-applicable hits,
other-branch-only Rulesets, release-only disabled negatives, main-applicable
disabled/evaluate misconfiguration, pagination, detail-404 with authoritative
`absent`, dual-403 `not_verified`, and GET-only observation.
