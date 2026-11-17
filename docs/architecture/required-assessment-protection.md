# Required Assessment server protection observation (#61 G3/R3)

Status: additive #61 G3/R3 **read-only observation contract**
(`engineering_ci.required_assessment_protection`). It inspects repository
Rulesets and Branch Protection for the stable Required Assessment Check name
(`engineering-ci / required-assessment`) and records visibility,
configuration and enforcement-probe state separately.

It does **not** mutate protection settings, publish the Check (#61 G2), re-run
Assessment (#61 R1), or authorize Release (#61 R4/R5).

## Observation shape

```text
inspect_server_protection(repository, branch=main, check_name=...)
        |
        +--> GET rulesets (+ per-id detail)
        +--> GET branches/{branch}/protection
        |
        +--> visibility / configuration / enforcement observation
```

| Field | Meaning |
| --- | --- |
| `visibility.rulesets` | `readable` / `forbidden` / `error` |
| `visibility.branch_protection` | `readable` / `absent` / `forbidden` / `error` |
| `configuration.required_assessment_in_*` | `true` / `false` / `null` (null = unread) |
| `enforcement.status` | `configured` / `absent` / `not_verified` / `misconfigured` |
| `enforcement.probe` | always `not_performed` in this slice |

## Enforcement status rules

- **configured** — at least one readable source actively lists the Check name
  (active Ruleset required-status-checks or Branch Protection contexts).
- **absent** — every readable source lacks the Check name (e.g. empty Rulesets
  and unprotected branch).
- **not_verified** — no readable source, or only partial visibility without a
  configured hit (permission gaps must not be reported as “absent”).
- **misconfigured** — the Check appears only on disabled Rulesets while active
  readable sources omit it.

Configuration presence is **not** push/merge rejection proof. A controlled
enforcement probe remains follow-up evidence and is never implied by Workflow
PASS.

## Limits

- Read-only: never creates, updates or deletes Rulesets / Branch Protection /
  Apps / bypasses.
- Does not claim Source App ownership or admin-bypass absence.
- Does not replace Native Required Contexts that Rulesets may still require
  independently of the aggregated Required Assessment Check.
- Current credential scope may differ from Issue #61's historical 403
  observation; each run records the visibility it actually obtained.

## Directed acceptance

`tests/test_required_assessment_protection.py` covers absent configuration,
active Ruleset / Branch Protection configuration hits, dual-403
`not_verified`, partial-visibility `not_verified`, disabled-Ruleset
`misconfigured`, digests / tamper rejection, and GET-only observation.
