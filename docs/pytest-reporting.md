# Pytest execution and CI report delivery

Reporting is opt-in. Existing consumers continue to run `dagger check` with no
additional pytest execution. OBridge requires no changes.

## One execution, several outputs

```text
python_test_artifacts → pytest (once, ReturnType.ANY)
                     → original exit-code.txt → CI execution gate
                     → JUnit XML → schema v1 JSON → summary.md
                                                  → GitHub Job Summary
                     → native GitHub artifact upload, including failed runs
```

`python_test_artifacts()` returns a Dagger Directory even when pytest exits
nonzero. The export function succeeding means evidence was exported; it does
not mean tests passed. The original exit code is obtained from Dagger before
reading or formatting reports, and is stored separately in `exit-code.txt`.
Without an enabled quality policy, the CI gate reads only this file, never
JSON status, Markdown or upload results. Optional v0.4.4 coverage gates also
validate raw coverage counts against the explicit policy; see
[test strategies and quality gates](test-quality-gates.md).

`python_test()` still raises on a nonzero original pytest exit code, with a
bounded JUnit-based error summary. `python_test_report()` still returns JSON.
Both convenience interfaces own a run: do not invoke them sequentially to
collect the same suite. Use `python_test_artifacts()` for CI delivery instead.
The existing real Dagger fixtures also consume one materialized execution.

## Opt-in reusable workflow

Expose a function in the caller Dagger module which returns the result bundle.
Use the same source, Python version and test path as the check it replaces:

```python
@function
def pytest_results(
    self,
    source: Annotated[dagger.Directory, DefaultPath("/")],
) -> dagger.Directory:
    return dag.engineering_ci().python_test_artifacts(
        source=source, python_version="3.14", test_path="tests",
    )
```

Configure the reusable workflow (example for the forthcoming reviewed release):

```yaml
jobs:
  ci:
    uses: dymzz/engineering-ci/.github/workflows/reusable-python-ci.yml@v0.4.3
    with:
      pytest_report_function: pytest-results
      pytest_check: engineering-ci-smoke:test
      pytest_artifact_name: pytest-report
    secrets:
      ENGINEERING_CI_READ_TOKEN: ${{ secrets.ENGINEERING_CI_READ_TOKEN }}
```

`pytest_check` is the exact module-qualified name from `dagger check -l`.
The workflow exports the bundle, verifies that the replaced check exists,
and runs every other listed check, including generators. It never runs the
replaced check a second time. A wrong name fails configuration rather than
silently running all checks again. If a run has several reporting jobs, give
each a unique `pytest_artifact_name`.

The caller's tests and business code do not need changes. Adding the report
wrapper is necessary only for consumers opting in. `pytest_report_scenario`
is reserved for the small engineering-ci fixture probes, not ordinary callers.

## Facts and reporting state

Schema v1 field names and meanings remain unchanged. No new JSON fields are
required. A real passing fixture produces:

```json
{"schema_version":1,"exit_code":0,"reason":"success","status":"passed","report_available":true,"counts":{"tests":1,"failures":0,"errors":0,"skipped":0,"passed":1},"failures":[],"omitted_failures":0}
```

An assertion failure has `exit_code=1`, `status="failed"`,
`report_available=true`, and `category="assertion_failure"`. The bundle exports
successfully, while the execution gate fails with the original exit code.

Missing or malformed XML has `status="error"`, `report_available=false`,
`counts=null` and an explicit diagnostic. Its original exit code is preserved.
When pytest exits zero but XML is unavailable, the execution gate passes and
the separate report-delivery step records a non-blocking warning. The summary explicitly distinguishes
test execution from report status. Reporting errors never manufacture a pytest
failure or turn a failing test into a success. If pytest preparation or export
fails before evidence is available, CI fails and marks evidence unavailable.

Other Dagger checks remain independently authoritative. For example, Ruff
can fail the job while the pytest report correctly says its tests passed.
Summary and artifact upload failures are visible non-blocking delivery warnings;
they cannot erase an earlier failed execution gate or turn passing checks red.

## Artifacts and bounds

The native artifact contains only `report.json`, `exit-code.txt`, `summary.md`
and optional `junit.xml`. Fixture probes additionally include a one-character
`fixture-invocations.txt` proving one pytest session. The exported stdlib
`ci.py` helper runs checks planning, summary publication and the gate; it is
not included in the uploaded artifact. No full workspace or environment dump
is uploaded.

JUnit XML is limited to 2 MiB; larger reports are explicitly unavailable.
JSON failure lists are limited to 10 by default (configurable 1..50), with
bounded names, messages, traces, diagnostics and console tail. Omitted failure
counts are retained. Summary output is limited to 24 KiB, escapes Markdown/
HTML text, and omits additional rows with an explicit count. Retention is
seven days. Reports can contain test-provided text; tests should not print
credentials in assertion messages or output.

Download using the standard GitHub API or:

```sh
gh run download RUN_ID -R dymzz/engineering-ci -n pytest-report
```

The overhead is report parsing and file export, not another complete test run.
There is no database, history service, download proxy or inference component.

## Verification and release boundary

Parser and delivery tests run independently of Dagger:

```sh
uv run --locked --group dev python -m unittest discover -s tests -p 'test_pytest*.py' -v
```

The real fixture report hook asserts a pytest session counter of one.
Engineering CI uses the opt-in path for its positive fixture. Manual
`workflow_dispatch` probes inject assertions or remove/corrupt XML after pytest
without committing intentionally failing tests. Their failed execution/report
steps still publish summaries and upload available evidence.

Prepare version v0.4.3 in this feature PR using the existing release tool.
Human review and merge are required before publishing. The existing Release
Workflow publishes only after successful main CI. Published-version Smoke
verification and its separate reviewed consumer PR follow the release; no
consumer PR is merged automatically.
