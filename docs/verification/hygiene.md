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

## Host-native tool evidence

No image was pulled. The host already provides the tools:

```text
lychee --version   # 0.24.2
tach --version     # 0.35.3
vulture --version  # 2.16
```

A disposable fixture repository with a changed `docs/guide.md` ran the actual
host CLI:

```text
python -m eci_core.hygiene --root <fixture> --config ci-hygiene.toml --base HEAD --json
# status: passed; docs: passed (Lychee invoked)
```

The local Docker store contains only the Dagger engine and a socket proxy, so
the Dagger functions reuse the already cached uv image and install the tools
inside it:

```text
dagger call tach-check --source tests/fixtures/tach-ok
# {"status": "passed", "exit_code": 0, "version": "0.35.3"}

dagger call lychee-check --source . --files docs/architecture/selection.md
# {"status": "passed", "exit_code": 0, "version": "0.24.2"}
```

No image pull was issued for these checks.

## Limits

- Plan-driven Dagger execution (host change set, check list, aggregated native
  evidence) is not wired; `tach-check` / `lychee-check` are the native building
  blocks.
- Source-manifest validation now lives in `eci_core.sources`; see
  [source reconciliation](sources.md).
- The unit tests use a fake native runner; the host CLI and Dagger calls above
  are the real checks.
