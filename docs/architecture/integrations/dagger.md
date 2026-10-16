# Dagger Integrations

Inventory: [technology](../technology.md). Up: [Integrations](README.md).
Rules: [.dagger/AGENTS.md](../../../.dagger/AGENTS.md). Contracts: [Dagger configuration](../../configuration/dagger.md).

## Dagger engine and module

- **Status:** adopted
- **Pin:** `1.0.0-beta.16` — `dagger-module.toml` (`engineVersion`) and
  `DAGGER_VERSION` in `.github/workflows/reusable-python-ci.yml`. `dagger.lock` pins workspace
  images and Git references; it does not select the Engine version.
- **Used by:** `dagger.toml` installs the local entrypoint; `dagger-module.toml` retains the
  `.dagger` source. `.dagger/src/engineering_ci/main.py` exposes reusable functions and fixture
  Checks. `pr_checks.py` composes shared preparation, independent single-check containers and
  an evidence sink through Dagger directory dependencies. `src/engineering_ci/checks_container.py`
  runs a single registered check per branch and validates collected facts;
  `src/engineering_ci/dagger_checks.py` binds them to host inputs and admits reuse.
  The existing pytest, quality and matrix modules preserve original failures and one-execution
  reporting. PR branches do not add default fixture Checks or replace GitHub's remote Job DAG.
  `src/engineering_ci/tools/bootstrap_flow.py` explicitly invokes native consumer SDK generation,
  check discovery and one `dagger check`; Dagger owns execution and cache reuse.
- **Contract:** [Dagger configuration](../../configuration/dagger.md),
  [local checks](../local-checks.md), [pytest reporting](../testing/pytest-reporting.md)
- **Verify:** module / workflow Engine pins agree; `tests/test_pipeline.py`,
  `tests/test_dagger_checks.py`, `tests/test_pr_check_graph.py`; real Engine execution and reuse
  evidence in [PR graph validation](../../verification/pr-check-graph.md).
  `tests/test_bootstrap_flow.py` substitutes the consumer CLI; real consumer setup is a separate acceptance.
- **Verified:** 2026-10-15 @ `04388dbdcf7a`

## Dagger Python SDK and packaging

- **Status:** adopted
- **Pin:** `dagger-io` — Engine-generated editable source `.dagger/sdk` in
  `.dagger/pyproject.toml`; generation is bound to the module's pinned Engine, not an inferred
  PyPI SDK version. `packaging>=24.2` — `.dagger/pyproject.toml` and `.dagger/uv.lock`.
- **Used by:** `.dagger/src/engineering_ci/main.py` and `pr_checks.py` import the SDK to
  construct module APIs and container dependencies. `pytest_matrix.py` uses `packaging`
  specifiers / versions. Native module loading includes the generated SDK source;
  regeneration uses the existing ModuleSource API. Python workspace SDK generation has a
  separate compatibility limit documented in the configuration contract.
- **Contract:** [Dagger configuration](../../configuration/dagger.md)
- **Verify:** `uv lock --project .dagger --check`; SDK / packaging imports in the paths above;
  [native configuration validation](../../verification/dagger-migration.md).
- **Verified:** 2026-10-14 @ `e1f649f559f1`

## Docker

- **Status:** adopted
- **Pin:** unpinned host / runner-provided Docker Engine; runner image `ubuntu-24.04` in
  `.github/workflows/ci.yml` and `reusable-python-ci.yml`. Dagger Engine image version follows
  the Engine pin above, independently of Docker's host version.
- **Used by:** `.github/workflows/reusable-python-ci.yml` pulls and starts the Engine;
  `src/engineering_ci/dagger_checks.py` selects / probes the local Engine through Dagger CLI.
  `.dagger/src/engineering_ci/pr_checks.py` defines the Linux check containers.
  `tests/integration/trace_jaeger.py` invokes Docker for opt-in Jaeger / Collector fixtures.
- **Contract:** [local checks](../local-checks.md), [Dagger configuration](../../configuration/dagger.md)
- **Verify:** Docker invocations / runner bindings in the paths above and actual Dagger/Linux
  evidence in [PR graph validation](../../verification/pr-check-graph.md). Jaeger / Collector
  acceptance is separate and was not rerun by this inventory update.
- **Verified:** 2026-10-14 @ `e1f649f559f1`

## Vesryn task observation provider

- **Status:** removed
- **Pin:** none — the previous observe-task binding is no longer used.
- **Used by:** none. Installed vesryn-log Sinks replace default task log delivery;
  ordinary logging does not start a Dagger execution. Existing tool calls still cross their
  declared observation boundary.
- **Contract:** [task recording](../cli-boundaries.md#默认任务采集与日志记录),
  [vesryn-log](tracing.md#vesryn-log).
- **Verify:** `tests/test_task_observation.py` proves real installed file writes and no
  extra Dagger/Vesryn invocation; no observe-task binding in runtime source.
- **Verified:** 2026-10-16 @ `742bfc4f833d`
