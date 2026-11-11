# Unified Python package verification

Date: 2026-11-11. Worktree: `eci-package-review`; implementation branch:
`codex/unify-python-package`. The requested integration base is the unmerged
[PR #78](https://github.com/dymzz/engineering-ci/pull/78) for #74 at
`cc8017653380b0f467e3e35a93ae98d48deddc2b`, rather than main alone.

Contract: [shared implementation and Dagger imports](../architecture/selection.md#shared-implementation-and-dagger-import-p1-3).

## Scope and compatibility

The five shared modules move from `eci_core` to `engineering_ci` without
changing their selection, execution, hygiene, source or telemetry algorithms.
The old import namespace is removed. Python callers update imports and
`python -m` module paths. Existing CLI subcommands, Plan/Report schemas and
Dagger function names retain their contracts. The persisted telemetry
producer label remains `eci_core.cicd_telemetry`; it is not an import alias.

Dagger keeps the separate `engineering_ci_module` package and SDK environment,
declares the root project as a local dependency, and imports its installed
package without injecting a checkout directory into `sys.path`. The root
package still has no third-party runtime requirements. The
`src/engineering_ci/hooks` link to an external shared hook directory is removed.
The repository has no `hooks.json` or Codex/Claude Code/Antigravity automatic
hook registration files; the external target was not accessed.

The new local dependency adds a first-party version entry in `.dagger/uv.lock`.
Local version checks and remote release metadata verification require the
current Dagger module to declare `engineering-ci` as a dependency, use
`[tool.uv.sources]` with `path = ".."`, and lock the package from
`directory = "../"` with matching dependency metadata and versions. The
remote reader still accepts historical release metadata in which the module
itself was named `engineering-ci`; this does not authorize the current
module to drop its declared root-project dependency.

## Executed checks

| Check | Observed result |
| --- | --- |
| Root `uv lock --check --offline` | Passed |
| Module `uv lock --project .dagger --check --offline --default-index https://mirrors.aliyun.com/pypi/simple` | Passed |
| `uv build --wheel --offline` | Passed; one `engineering_ci` Python root, no `eci_core/`, hook assets or runtime requirements |
| Separate wheel-only environment, `python -I` | All five modules imported from that environment's `site-packages`; old namespace absent; distribution version `0.7.0` |
| Installed CLI, cleared `PYTHONPATH`, separate consumer directory | `select --help` and `execute --help` exited 0 |
| Ruff lint / format | Passed on authored source, tests and Dagger module |
| Complete Windows pytest suite | 574 passed, 9 skipped, 187 subtests passed |
| Native SDK/context generation API | Passed; module lock regenerated with the existing package versions and index |
| `dagger api call shared-identity` | `0.7.0:1:engineering-ci-selection-plan` |
| `dagger api call selection-self-test` | `engineering-ci-selection-plan:targeted:tests.test_pkg` |
| `dagger api call sources-self-test` | `engineering-ci-sources-report:passed:1` |
| `dagger check --engineering-ci` | All five passed: lint, format-check, tests, version and lock; tests completed in about 2m55s |

The full-suite and Dagger Check results above were recorded at `1fba419`.
The subsequent removal of the external hook link and its exclusion settings
was verified with a fresh wheel build/content audit, both lock checks and
`git diff --check`.

The wheel-only import probe ran with Python isolated mode, so checkout paths
and `PYTHONPATH` could not satisfy the shared imports. The CLI check used the
new environment's console script rather than the editable development install.
Temporary inputs and the environment are retained under ignored `.test-tools/`.

The first Windows suite run exposed the existing CRLF policy-example checkout
issue. Its committed LF bytes were restored after confirming the only difference
was CRLF conversion, with SHA-256
`fbed0d93f015cc3418d5008443c22963d65c1321a7539f65f183b4df7cda5514`.
The example has no PR diff; the targeted test and subsequent complete suite
passed. The base PR already supplies the relevant Git attribute.

Dagger still emits the base PR's documented `worktreeconfig` warning; the API
calls above completed successfully without changing Git configuration. No
agent hooks were invoked; Git writes and host test Git subprocesses disabled
hooks for their invocation. These are package-integration results, not a new
acceptance claim for #74's producer, Required status or release admission.
