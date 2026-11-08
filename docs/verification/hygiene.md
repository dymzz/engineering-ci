# Repository hygiene — local verification

Scope: second slice of [ECI #66](https://github.com/dymzz/engineering-ci/issues/66).
Date: 2026-11-08; host: Windows 11, CPython 3.14.3, Git for Windows.

Contract: [hygiene checks](../architecture/hygiene.md).

## What ran

```sh
pytest -q tests/test_hygiene.py
# 13 passed

pytest -q tests
# 465 passed, 8 skipped, 187 subtests passed
# 1 known Windows-checkout failure: tests/test_security_policy.py
#   test_example_policy_is_a_valid_publish_input (CRLF vs canonical LF bytes)
```

The policy-fixture failure is a Windows `core.autocrlf` artifact unrelated to
this slice; the same suite passes with an LF policy fixture and on Linux CI.
It was not modified.

## Covered behaviours

Each hygiene case runs against a disposable Git repository copied from
`tests/fixtures/selection-app`; Git commands are real, external tools use a fake
runner so no Lychee/Vulture/Tach installation is required.

| Behaviour | Observed |
| --- | --- |
| Dirty whitespace | `git diff --check` nonzero → `failed` / `whitespace_errors` |
| Clean whitespace | exit 0 → `passed` |
| No document changes | `docs` is `not_applicable`; no Lychee invocation |
| Changed document | Lychee runs once with only the affected active document |
| Missing Lychee | exit 127 → `failed` / `tool_unavailable`, never a pass |
| Broken links | Lychee nonzero → `failed` / `lychee_findings` |
| Vulture findings | exit 3 → check `advisory`, run `passed`, finding retained |
| Vulture error | exit 1 → `failed` / `vulture_failed` |
| Tach config missing | `failed` / `tach_config_missing`, not skipped |
| Tach check failure | `failed` / `tach_check_failed` |
| Forwarding module | pure re-export over a substantive base → advisory finding, run passes |
| Report identity | base/head commit, tree, dirty flag and changed paths recorded |
| CLI | JSON report emitted, exit 1 preserved on failure |

## Limits

- Dagger/Actions execution of the same check selection is not implemented yet.
- Python source-manifest validation (path safety, size, SHA-256, Git attributes,
  registered/acquired semantics) is a follow-up slice; no Knowledge-specific
  manifest logic is in ECI.
- External tools are not installed in the test environment; their native
  positive/negative execution is exercised through the same `subprocess`
  contract in Dagger/Actions later.
