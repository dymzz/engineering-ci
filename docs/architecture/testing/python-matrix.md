# Python compatibility matrix

The matrix is opt-in. Existing `python_test`, `python_test_report` and
`python_test_artifacts` retain their defaults and schema. A consumer with one
Python version needs no changes and starts no additional environment.

## Choose one execution interface

```python
@function
@check
async def compatibility(self, source: dagger.Directory) -> str:
    return await dag.engineering_ci().python_test_matrix(
        source=source,
        python_versions=["3.12", "3.13", "3.14"],
        test_path="tests",
        max_concurrency=1,
    )
```

`python_test_matrix` fails the Dagger check if any requested version or enabled
quality gate fails. `python_test_matrix_report` returns JSON including failed
versions and does not gate CI. `python_test_matrix_artifacts` returns a Directory
with the same result and original evidence. Each interface runs pytest once per
compatible version. Calling multiple interfaces requests multiple executions;
do not add a report call after a strict test call merely to deliver reports.

For GitHub reporting, define one consumer wrapper returning
`python_test_matrix_artifacts`, and use the existing reusable workflow's
`pytest_report_function` and exact `pytest_check` replacement settings. The
wrapper replaces that selected check; other checks still run. The exported
`ci.py gate` enforces the original execution facts after reporting, including
when pytest fails. Summary generation and artifact upload are presentation and
do not alter execution status.

```sh
dagger call python-test-matrix-artifacts --source=. \
  --python-versions=3.12,3.13,3.14 --max-concurrency=1 \
  export --path=matrix-results
python matrix-results/ci.py summary matrix-results summary.md
python matrix-results/ci.py gate matrix-results
```

The CLI accepts comma-separated list flags. `markers`, `coverage_source`,
`min_coverage`, and `max_failures` use the existing explicit test policy. No
version or test is automatically omitted to obtain a passing matrix.

## Interpreter and isolation contract

Targets use the existing supported set: 3.10, 3.11, 3.12, 3.13, 3.14. Specify
1–5 unique minor versions; patch selectors, duplicates, and unknown versions
are configuration errors. Execution defaults to sequential. Explicit
`max_concurrency` values 1–3 bound active environments within one Dagger Engine.
Results retain requested order even when concurrent versions finish out of
order. A failed version does not cancel other required versions.

Each version receives its own container and `/workspace/.venv`. Source `.venv`
directories are excluded. uv uses the selected image interpreter and cannot
download a managed replacement. Dagger may share immutable image/download
cache layers; interpreter environments and test process state are separate.

Before dependency installation, the actual full interpreter version is checked
against the project's unchanged `requires-python` using `packaging.SpecifierSet`.
Patch constraints and exclusions are honored. An incompatible version has
`status: unsupported`, `failure_category: python_unsupported`, and no pytest
exit code; it still makes the overall matrix unsuccessful.

Actual version, executable, prefix, and pytest exit code are recorded by the
same Python process that calls pytest once. A minor or preflight patch mismatch
fails explicitly. An image name is not runtime proof.

## Results and failure delivery

Matrix JSON has `schema_version: 1` and
`kind: python_compatibility_matrix`; it is separate from single-version schema
1. `required_versions`, `max_concurrency`, `overall_status`, and `versions`
describe the aggregate. Each row includes requested/full actual Python,
execution phase, process exit code, nullable original pytest exit code,
runtime evidence, and the unchanged nested pytest report with optional quality
results. Absent execution has no invented pytest counts or exit code.

| Failure category | Meaning |
|---|---|
| `python_unsupported` | Project constraints reject this actual interpreter |
| `configuration_error` | Project metadata cannot be read or validated |
| `environment_initialization` | Image/runtime preparation failed |
| `dependency_installation` | Locked uv sync failed; pytest did not start |
| `interpreter_mismatch` | Actual interpreter differs from the requested/validated runtime |
| `collection_error` | pytest exited 2 with JUnit collection errors |
| `pytest_failure` | Original pytest exit 1 |
| `pytest_execution_error` | Other nonzero pytest exit, including no tests |
| `coverage_failure` | Tests can pass while the explicit coverage threshold fails |
| `coverage_unavailable` | Enabled coverage lacks valid matching evidence |
| `runtime_unavailable` / `report_unavailable` | Required execution/report evidence missing or inconsistent |

All required versions, original pytest executions, complete reports, and enabled
gates must pass. The exported gate reconstructs results from `execution.json`,
`runtime.json`, `exit-code.txt`, JUnit XML and raw coverage. It does not trust
`matrix.json`, per-version `report.json`, or terminal text to decide success.
Missing evidence blocks the matrix. Matrix report completeness is stricter than
the existing single-version missing-JUnit policy; the single-version policy is
unchanged. Reconstructing failures from JUnit cannot recover terminal-only
diagnostics when XML was never generated; the original nonzero exit remains.

The bundle stores `versions/<minor>/...`, `matrix-policy.json`, `matrix.json`,
and `summary.md`. The reusable workflow uploads only the explicit report and
evidence allowlist. Its `always()` reporting/upload steps preserve failed
results, while the original-fact gate fails CI. Helpers are exported for local
delivery and are not uploaded as test evidence.

## Verification and overhead

Parser/policy/delivery unit tests need no Dagger Engine:

```sh
uv run --locked --group dev python -m unittest discover -s tests -p 'test_pytest*.py'
```

`fixture-python-matrix` runs real 3.12/3.13/3.14 tests and verifies a targeted
3.14 failure, unchanged unsupported 3.11 constraints, targeted coverage failure,
locked dependency failure, retained results and original-fact gates. Fixture
counters verify one pytest session per executed version.

The manual CI `matrix_probe_scenario` selects pass, version-fail, unsupported,
coverage-low, dependency-error, or performance. Negative probes intentionally
fail their gate while preserving JSON/Summary/artifacts. Performance runs the
same fixture as single-version, three sequential versions, and concurrency 2,
in one Engine, with distinct source inputs to avoid reusing completed test
steps. `matrix-benchmark.json` records wall time and verified invocation counts.
Image/download caches may still be warm; one run is not a statistical benchmark.
Consumers incur this overhead only when they explicitly enable a matrix.
