# Vesryn Extraction Verification

## Scope

engineering-ci v0.6.6 consumes Vesryn v0.1.0 at immutable candidate
`0dfc8f5a695f647651485d90943121314fef179e`. Vesryn CI consumes the existing
engineering-ci v0.6.5 at `804d791c60d34468166b6910c88fbc8149fdbf26`.
Neither version is published by this task. Ownership and compatibility constraints
are defined in the [extraction contract](../architecture/workflow-optimization/repository-split.md).

## Independent Vesryn

- Its own uv environment: 305 pytest tests passed, zero skipped; Ruff and lock checks passed.
- A built wheel installed outside the source import path: core, generic domain lifecycle
  and packaged SQL import without engineering-ci or optional tracing SDKs.
- [Draft PR #1](https://github.com/dymzz/vesryn/pull/1).
- [Actual CI Run 38501585711](https://github.com/dymzz/vesryn/actions/runs/38501585711),
  head `0dfc8f5a695f647651485d90943121314fef179e`: package and harness/checks passed.
  Artifact `pytest-report` (ID 11865747755) contains 305 passed, zero failures/errors/skips,
  original exit code 0, JUnit, JSON and Summary from the same execution.
- Optional dependency-security Job was skipped because it was not enabled. It is not
  included in the passed checks above.

The preceding Run 38500610682 failed on formatter target inference. The shared formatter
ran under Python 3.14 while the local isolated check used py311. Vesryn now explicitly
declares py311; the actual rerun passed. A beta Dagger `@check` originally returned str;
the consumer now uses Void functions and preserves strict failure propagation.
These were integration errors, not confirmed upstream defects.

## Host compatibility

- Focused pipeline/checks/workflow-consistency/boundary tests: 263 passed.
- Dagger evidence and credential transport regression tests: 38 passed.
- Domain adapter/capability/query/summary/review tests: 176 passed before fixture relocation;
  subsequent boundary/binding/comparison tests: 61 passed.
- Shared native Ruff lint/format passed for the final adapter and credential changes.
- Base-only test environment has no Vesryn distribution: existing source-checkout
  Release/Download help and CI/CD imports work; optional workflow command reports missing
  Harness explicitly. Built base wheel execution-core import also succeeds without Vesryn.
  The host CLI remains source-checkout based; standalone wheel repository configuration
  was not added.
- Read-only host PostgreSQL check through `vesryn.tracing.store`: schema 3, historical
  `engineering_ci_trace` ownership/migration digest valid. No database migration or copy.
- Installed explicit engineering-ci adapter descriptors equal the compatibility CLI
  descriptors. Tool IDs and domain approval/feedback identities are retained.
- Existing explicit PostgreSQL/OTLP integration files stay in the host as compatibility
  acceptance, outside default pytest collection. Portable unit tests moved to Vesryn,
  rather than maintaining two independent implementations/test suites.

An earlier full native Windows run returned 1044 passed, 3 failed, 8 skipped and
103 subtests. Three negative Workflow tests targeted numeric Step positions changed by
the authentication bootstrap; they now target the shared named check Step and still
assert failure. The focused rerun passed. Required Bash skips on native Windows remain
incomplete verification, not a current full pass.

## Private installation boundary

Existing gh authentication is captured in memory and passed by Dagger Secret reference
only to the pinned private dependency installation. It is never embedded in arguments,
source/config files or check JSON. Only the approved repository and immutable revision
are accepted. The container removes the Secret before tools/tests and asserts absence.

A real container assertion caught use of `without_env_variable`: Dagger has a separate
`without_secret_variable` API. The implementation uses that SDK method; failed preparation
did not execute tests. The source is the
[official Container API](https://docs.dagger.io/reference/api/container/).

## Remaining acceptance

Actual local Dagger/Linux PR profile passed: 1060 pytest tests and 105 subtests, zero
failures/errors/skips; Ruff lint/format, version and lock checks also passed. JSON and
original JUnit are in `.test-tools/vesryn-split/linux-checks-current.json` and its immutable
evidence bundle. A preceding run passed its tools but became stale when a Workflow input
changed during execution; it was not reused as a current pass.

Subsequent Release integration fixes forward the private install Secret to `core-tests`
and keep prepare/verify/publish on base dependencies with `--no-default-groups`.
The focused current regression passed 133 tests; version/Workflow binding passed.
The immutable host PR CI is recorded below once completed. These later source changes
invalidate the earlier full local receipt; its raw execution remains historical evidence.

The existing formal Release Security Gate only admits public PyPI lock entries.
The pinned private Vesryn Git source is a real unsupported dependency, yielding
`scan_coverage_gap`; integrity of the uv lock does not prove vulnerability scan coverage.
No exception, ignored dependency or fabricated pass was introduced. Formal v0.6.6
publication remains blocked until an explicitly reviewed distribution/scanning contract
can cover that source. This extraction does not initiate formal validation or publication.

No simulated result substitutes for actual Runs. Historical receipts are not admitted for
changed inputs. Human merge/release is required for formal versions.

No automatic optimization loop, Contextual Bandit, capability approval/activation or
database movement was performed. Capability discovery returned an empty default catalog,
so existing verification tools were used directly.
