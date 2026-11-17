# Required Assessment server protection observation (#61 G3/R3)

Status: additive #61 G3/R3 **read-only observation contract**
(`engineering_ci.required_assessment_protection`). It inspects
**branch-applicable** repository rules and Branch Protection for the stable
Required Assessment Check name (`engineering-ci / required-assessment`) and
records visibility, configuration and enforcement-probe state separately.

It does **not** mutate protection settings, publish the Check (#61 G2), re-run
Assessment (#61 R1), or authorize Release (#61 R4/R5).

## Observation shape

```text
inspect_server_protection(repository, branch=main, check_name=...)
        |
        +--> GET rules/branches/{branch} (paginated; applicability authority)
        +--> GET rulesets (+ detail)     (supplemental disabled/evaluate only)
        +--> GET branches/{branch}/protection
        |
        +--> visibility / configuration / enforcement observation
```

| Field | Meaning |
| --- | --- |
| `visibility.branch_rules` | `readable` / `forbidden` / `error` |
| `visibility.rulesets` | supplemental Ruleset list visibility |
| `visibility.branch_protection` | `readable` / `absent` / `forbidden` / `error` |
| `configuration.required_assessment_in_branch_rules` | `true` / `false` / `null` |
| `enforcement.status` | `configured` / `absent` / `not_verified` / `misconfigured` |
| `enforcement.probe` | always `not_performed` in this slice |

## Enforcement status rules

- **configured** — an applicable readable source lists the Check
  (`rules/branches/{branch}` required-status-checks or Branch Protection).
- **absent** — complete applicable reads lack the Check (and no supplemental
  disabled/evaluate Ruleset lists it).
- **not_verified** — applicable reads are forbidden, errored, or pagination /
  detail incomplete so a negative cannot be proven.
- **misconfigured** — applicable sources omit the Check, but a supplemental
  disabled/evaluate Ruleset still lists it. Unknown `enforcement=None` is never
  treated as active applicability.

A Ruleset that is active only for another branch pattern must not count as
`configured` for the queried branch. Configuration presence is **not**
push/merge rejection proof.

## Limits

- Read-only: never creates, updates or deletes Rulesets / Branch Protection /
  Apps / bypasses.
- Does not claim Source App ownership, bypass absence, or controlled rejection.
- Incomplete page or detail reads never yield `absent`.
- Does not replace Native Required Contexts that may still be independently
  required.

## Directed acceptance

`tests/test_required_assessment_protection.py` covers branch-applicable hits,
other-branch-only Rulesets, pagination / later-page hits, detail-404 races,
dual-403 `not_verified`, disabled/evaluate `misconfigured`, unknown
enforcement, digests / tamper rejection, and GET-only observation.
