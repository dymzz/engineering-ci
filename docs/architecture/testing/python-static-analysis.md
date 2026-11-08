# Python static-analysis scanners

Vulture, Pyright, deptry and Pylint are available as independent Dagger
functions. Every scanner runs from its own pinned environment and keeps the
consumer's project unchanged.

This issue delivers the tool capability and its targeted verification only.
Selection inputs, Report/Enforce gating and Artifact delivery in the reusable
Workflow belong to the integration issue and are not implemented here.

## Functions and defaults

| Function | Required | Optional, with default | Return |
| --- | --- | --- | --- |
| `python_vulture` | source, python-version | scan-path=`.`, min-confidence=`0`, exclude=``, whitelist-path=``, git-token | JSON report |
| `python_pyright` | source, python-version | scan-path=`.`, config=``, level=``, target-python-version=``, use-project-environment=`true`, git-token | JSON report |
| `python_deptry` | source, python-version | scan-path=`src`, config=``, extend-exclude=``, per-rule-ignores=``, git-token | JSON report |
| `python_pylint` | source, python-version | scan-paths=`["src"]`, min-similarity-lines=`4`, rcfile=``, ignore-paths=``, git-token | JSON report |

None of these functions gate CI. A caller declares its own native Check and
raises on `outcome`:

```python
@function
@check
async def vulture(src: dagger.Directory) -> None:
    report = json.loads(await engineering_ci.python_vulture(src, "3.14", "src"))
    if report["outcome"] != "clean":
        raise RuntimeError(report["reason"])
```

`outcome` is `clean`, `findings`, `not_executed` or `failure`. Treat anything
other than `clean` as a non-pass; there is no implicit clean state.

## Pinned versions and the check environment

`.dagger/src/engineering_ci_module/python_static.py` pins one version per scanner and
is the only place that states them. The scanners run through
`uvx --from <tool>==<version>`, so their environment is built separately from
the consumer's.

The consumer's `pyproject.toml`, `uv.lock` and source files are never written.
`uv sync --locked` still prepares the consumer environment for import
resolution, and the shared uv cache keeps both steps off the network on later
runs. Pyright additionally caches its Node runtime, which its published
distribution downloads on first use.

## Native exit-code semantics

A non-zero status is a finding only where the tool's own semantics say so.

All four tools were executed against their pinned versions and every code below
was observed directly rather than taken from documentation. Each table also
fails closed, so a code path that did not behave as documented becomes a
`failure` and never a pass. `fixture_static_analysis` re-asserts the same
outcomes and integers in CI.

| Tool | Exit | Meaning | `outcome` |
| --- | --- | --- | --- |
| vulture | `0` | no dead code | `clean` |
| vulture | `3` | dead code reported | `findings` |
| vulture | `1` | unparsable file or missing path | `failure` |
| pyright | `0` | no diagnostics | `clean` |
| pyright | `0` with `filesAnalyzed: 0` | nothing applicable was analysed | `not_executed` |
| pyright | `1` | type errors reported | `findings` |
| pyright | `2` | fatal error | `failure` |
| pyright | `3` | configuration could not be parsed | `failure` |
| pyright | `4` | path does not exist | `failure` |
| deptry | `0` | no dependency issues | `clean` |
| deptry | `1` with a structured report | dependency issues reported | `findings` |
| deptry | `1` without a structured report | uncaught exception | `failure` |
| deptry | `2` | invalid input or usage | `failure` |
| pylint | `0` | no duplicate code | `clean` |
| pylint | `4`/`8`/`16` bits | warning, refactor or convention messages | `findings` |
| pylint | `1`/`2`/`32` bits | fatal, error or usage failure | `failure` |

Three consequences are load-bearing:

- deptry and pylint also exit non-zero from an uncaught failure. Only their own
  structured report proves the run reached the point where issues are
  collected, so a missing report is a `failure` and never `findings`.
- Pylint reports a duplicate group as exactly one R0801 message naming both
  members inside its text, not one finding per file. Counting findings
  understates the scope; read the message for the participating files.
- Pyright's default exclude contains `**/.*`, so a scope under a dot-directory
  is analysed as zero files and exits `0`. That is reported as `not_executed`,
  not `clean`.

Pyright is invoked without `--warnings`, so its exit `3` keeps meaning
"configuration error" here rather than the tool's separate warnings-as-errors
mode.

## Report fields

`schema_version`, `tool`, `pinned_version`, `observed_version`, `policy`,
`exit_code`, `outcome`, `reason`, `finding_count`, `findings`,
`omitted_findings`, `limitations`, `native_json_available`, `native_json`,
`native_truncated`, `stdout` and `stderr`.

Native stdout, stderr, exit codes and structured output are preserved. Where a
tool only writes structured output to a file, the file is read after the same
execution; the scan is never repeated to produce a report. Streams are bounded
to the last 8 000 characters and structured output to 1 MiB; when the bound is
hit, `native_truncated` is `true` so the loss is never silent.

`observed_version` is filled only where the tool reports its own version inside
its normal output.

## Unresolved dependencies are a stated limit

`use_project_environment` points Pyright's import resolution at the environment
this check already synced, while the Pyright installation stays isolated.
Imports that cannot be resolved are kept as `reportMissingImports` diagnostics
and additionally summarised in `limitations`. Type results are incomplete in
that case, so the run is not presented as a clean one.

Pyright reads the consumer's own configuration by default. Passing `config`
selects an explicit `--project` file; passing `target_python_version` sets the
analysed interpreter version.

## Scope boundaries

- Vulture keeps its native confidence, exclude and whitelist handling.
- deptry keeps its native root, exclude, per-rule-ignore and config handling.
- Pylint enables only R0801 `duplicate-code`; Ruff already covers the rest for
  the same sources, and this must not run them twice.
- Hypothesis is not a scanner. Consumers needing property tests run them
  through the existing `python_test` entry point and keep its JUnit and
  coverage behaviour.
- Ruff continues through `python_lint` and `python_format_check`. B, SIM and PIE
  were evaluated against the current tree and produce zero findings there; they
  are not enabled by default, and enabling them is a separate explicit policy
  decision.

## Verification

`.dagger/src/engineering_ci_module/main.py` declares `fixture_static_analysis`, which
runs every scanner against committed negative and positive sources in
`tests/fixtures/static-app/`. It asserts the pinned version each `uvx` actually
resolved, the native exit code and outcome of each case, that Pyright honours
the consumer's configuration, that unresolved imports raise a stated
limitation, and that Pylint reports R0801 across two files.

Run it explicitly; it is excluded from the default repository checks:

```sh
dagger --env fixtures check --module validation --check=fixture-static-analysis
```

Unit tests for the policy validation, argument construction, exit-code
classification and report shaping run in the normal `tests` check and need no
scanner installed.