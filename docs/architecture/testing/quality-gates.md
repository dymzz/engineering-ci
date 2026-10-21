# Explicit test selection and quality gates

This is an opt-in extension of the v0.4.3 single-execution report contract.
Engineering CI does not choose tests from Git diffs, lower thresholds, or
require an LLM to decide whether a check passes.

## Parameters and defaults

The three functions `python_test`, `python_test_report` and
`python_test_artifacts` accept these additional string parameters:

| Parameter | Default | Contract |
| --- | --- | --- |
| `test_path` | `tests` | Explicit workspace-relative pytest path or node ID |
| `markers` | empty | Native pytest `-m` expression, passed as one argument |
| `coverage_source` | empty | Native pytest-cov source module or relative path |
| `min_coverage` | empty | Line percentage from 0 to 100, at most 12 decimal places |

`min_coverage` is a string to preserve decimal precision across the Dagger
API. Use `"80"` or `"80.5"`; it requires `coverage_source`. NaN, infinity,
exponents, negatives, values above 100 and out-of-workspace paths are rejected.
Native pytest validates marker syntax and path existence. Empty selection
retains pytest exit 5 and fails. There is no automatic selection policy.

Empty new parameters preserve the existing arguments and JSON fields;
coverage is not collected implicitly. Full regression means all tests in
the explicitly configured path. It does not claim that other repository
paths were included. Targeted checks specify their path and/or markers.

```sh
# Full regression, unchanged default behavior
dagger -m github.com/dymzz/engineering-ci@v0.4.4 call python-test \
  --source=. --python-version=3.12 --test-path=tests

# Explicit targeted developer feedback
dagger -m github.com/dymzz/engineering-ci@v0.4.4 call python-test \
  --source=. --python-version=3.12 --test-path=tests --markers='fast and not integration'

# Explicit full regression with a coverage gate
dagger -m github.com/dymzz/engineering-ci@v0.4.4 call python-test \
  --source=. --python-version=3.12 --test-path=tests \
  --coverage-source=app --min-coverage=80
```

Use these examples after the reviewed v0.4.4 release. PowerShell uses a
backtick rather than `\` for multiline commands; parameters are identical.
Register your marker names in `[tool.pytest.ini_options].markers`.

Add `pytest-cov` to the consumer's dev dependencies and update its `uv.lock`
before enabling coverage. The runner still uses `uv sync --locked`; it does
not install plugins or rewrite dependencies silently. The verification
fixture pins pytest-cov 7.0.0. Disabled consumers need no coverage dependency.

## One execution and independent gates

```text
explicit path / markers / source / threshold
    -> one native pytest execution
    -> original exit-code.txt                 -> pytest gate
    -> JUnit XML                              -> existing pytest result
    -> coverage.py XML + JSON counts + policy  -> coverage gate
    -> additive quality JSON + Markdown       -> presentation
    -> existing Job Summary and native artifact upload
```

In opt-in mode pytest-cov receives `--cov-fail-under=0`, so its threshold
does not change the original pytest execution status. Engineering CI applies
the explicit `min_coverage` separately, replacing any inherited plugin
threshold for that call. Without a minimum, coverage is collection-only;
valid coverage data is still required. Disabled calls retain inherited
consumer pytest configuration.

XML and JSON line counts must agree. The comparison uses integer counts and
the exact decimal threshold, never the rounded display percentage. Exactly
meeting the threshold passes. This is a line-only gate even if a consumer
also configures branch measurement.

`python_test()` raises for pytest failure or an enabled quality gate failure.
`python_test_report()` returns both results without claiming CI succeeded.
`python_test_artifacts()` exports evidence even when a gate fails. The CI
helper recomputes the gate from `exit-code.txt`, `quality-policy.json` and raw
coverage files; displayed JSON cannot override it. Delivery errors remain
warnings and cannot erase a failed test or coverage gate. Ruff and other
checks remain independently authoritative.

Missing plugins, missing/malformed coverage, inconsistent counts and zero
measurable source lines are errors, not normal 0% measurements. Each raw
coverage file is limited to 2 MiB; exceeding this limit makes it unavailable.
Invalid pytest arguments retain their actual error exit code and diagnostic.
Configuration validation before execution raises an explicit error without
inventing a pytest exit code.

## Additive JSON v1 contract

New parameters add an optional `quality` object. Existing `status`,
`exit_code`, `counts`, `failures`, `reason` and `report_available` retain
their v0.4.3 meanings. Default calls do not add this field. Agents inspect
`quality.gate` when present and otherwise use the original pytest result.

An abbreviated controlled-fixture result has two passing tests, but fails
a 100% threshold because only five of six lines were exercised:

```json
{
  "schema_version": 1,
  "exit_code": 0,
  "status": "passed",
  "counts": {"tests": 2, "passed": 2, "failures": 0, "errors": 0, "skipped": 0},
  "quality": {
    "pytest_exit_code": 0,
    "selection": {"test_path": "tests", "markers": null, "mode": "all_in_path"},
    "coverage": {
      "enabled": true, "source": "quality_sample", "available": true,
      "covered_lines": 5, "total_lines": 6, "line_percent": 83.33333333333333,
      "required_percent": "100", "status": "failed", "diagnostic": null
    },
    "gate": {"status": "failed", "passed": false, "failure_reasons": ["coverage_below_threshold"]}
  }
}
```

Gate status is `passed`, `failed` or `error`. Machine-readable failure
reasons are `pytest_failed`, `pytest_execution_error`,
`coverage_below_threshold` and `coverage_unavailable`; multiple reasons can
coexist. Coverage status also supports `disabled`. Unavailable counts and
percentages are null; the exact threshold is a string. Selection mode is
`all_in_path` or `markers`, without guessing whether a suite is “fast”.

The matching Summary shows pytest **PASSED**, 2 passed / 0 failed,
coverage **83.33%**, required **100%**, coverage gate **FAILED** and overall
test quality gate **FAILED**. It never claims two tests failed.

## Reusable workflow integration

Keep the same explicit policy in the check and export hook:

```python
POLICY = {
    "test_path": "tests",
    "markers": "",
    "coverage_source": "app",
    "min_coverage": "80",
}


@function
@check
async def test(self, source: Annotated[dagger.Directory, DefaultPath("/")]) -> str:
    return await dag.engineering_ci().python_test(
        source=source, python_version="3.12", **POLICY
    )


@function
async def pytest_results(
    self, source: Annotated[dagger.Directory, DefaultPath("/")]
) -> dagger.Directory:
    return await dag.engineering_ci().python_test_artifacts(
        source=source, python_version="3.12", **POLICY
    )
```

```yaml
jobs:
  ci:
    uses: dymzz/engineering-ci/.github/workflows/reusable-python-ci.yml@v0.4.4
    with:
      pytest_report_function: pytest-results
      pytest_check: my-consumer:test
```

The workflow exports once and replaces exactly the named check, preserving
all others. A missing check name is a configuration error. Do not call
`python_test()` and `python_test_report()` sequentially for the same suite:
each convenience API owns an execution.

The existing native artifact additionally includes `quality-policy.json`,
`coverage.xml` and `coverage.json`. Exported stdlib helpers are not uploaded.
Retention remains seven days and Summary is still bounded and escaped.
See [the reporting guide](pytest-reporting.md) for authentication and delivery.

## Validation and release boundary

```sh
uv run --locked --group dev python -m unittest discover -s tests -p 'test_pytest*.py' -v
```

Independent tests execute real pytest-cov. The mandatory Dagger fixture
matrix checks pass/low coverage/test failure/both failures, marker selection
and empty selection, bad paths/markers, missing plugins and missing/corrupt
reports. The previous non-coverage fixture remains unchanged.

Manual `quality_probe_scenario` exports a single controlled scenario. The
baseline and coverage-pass probes use identical source/dependencies, two
collected tests and one pytest session. Their `fixture-timing.json` records
the session interval, excluding engine startup and dependency installation.
Tracing and report generation add work; no required tests are omitted to
reduce it. Failures before session start record zero sessions honestly.

Prepare v0.4.4 in this feature PR after validation. Human review and merge
precede publishing by the existing Release Workflow. Smoke candidate tests
use an isolated consumer PR; formal upgrade acceptance requires the released
tag/pin. OBridge is unchanged.
