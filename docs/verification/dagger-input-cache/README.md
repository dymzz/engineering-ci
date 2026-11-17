# Dagger input and cache observations

This is the online-Lychee slice of [#86 D.1](https://github.com/dymzz/engineering-ci/issues/86).
The cache rules belong to the [Dagger integration](../../architecture/integrations/dagger.md#plan-and-link-observation-cache-boundaries).
It does not complete the broader #86 B/D input audit, Module Include measurements
or all production dependency edges. Existing #90/#117 evidence is retained.

The final native implementation is commit
`5254bfccae80b9ebbd9233fb43342d7befe550ee`, based on main
`dd18b6a720c29b62c92f6b1dc4038f80fbd5fc20` after #117 merged. The
[receipt](receipt.json) records the actual implementation hashes and Source
digest. [Implementation sources](implementation-sources.tar.gz) match every
hash observed inside the final Module, including the shared mapper/validator.
These fingerprints are observations, not implementation approval.

## Actual native results

Three real `dagger api call fixture-link-cache` requests used the pinned
1.0.0-beta.16 CLI/Engine/SDK, the existing pinned Lychee 0.24.2 container,
a disposable HTTP Service and the same immutable caller Source. The Service
exposes `http://link-fixture:8080/target`; its response changes between 200 and
404 without changing Source. Its journal records actual target GETs, UTC
times and returned status. Configuration deliberately requests `--cache=true`
and also contains `cache=true` in `lychee.toml`; the final effective argv
records `--cache=false` for online observations.

| Observation | Actual outcome |
| --- | --- |
| Unrefreshed Exec control | After the Service changes to 404, both reads still return the original success/0, identical process timestamps and output. The journal shows no new target GET for the repeated read. |
| Online Plan, Service 200 | Real Git inventory succeeds; Lychee exits 0; Report passed/0; Enforce passes. |
| Same Source/Plan, Service 404 | A new target GET receives 404; Lychee exits 2 with its original rejection diagnostic; Report failed/2; Enforce raises `Plan failed: checks_failed (exit 2)`. |
| Repeated offline scan | Exit 0, identical saved process times and output; no target GET. |
| Offline Source with `docs/local.md` removed | Directory digest changes; a new Exec exits 2 with native `File not found`. Repeating this changed input retains the original failure and times. |
| Report/Artifact/Enforce on either saved PlanExecution | Report equals artifact bytes, Directory identity remains equal, and target-request journals remain unchanged. |
| A second independent final API request | Same Source digest and implementation hashes; two new target GETs observe 200 then 404. Offline success and local-file failure retain their earlier complete results. |

The initial request first populated the unrefreshed control cache and logged
three target GETs: control 200, Plan 200 and Plan 404. The two final requests
each logged only Plan 200 and Plan 404: the unrefreshed control already reused
the earlier result across requests. This is direct evidence that changing the
Function policy alone does not refresh an unchanged native Exec.

Exports returned CLI 0 because they delivered evidence, including failed
Reports. The compound consumer explicitly held each saved `PlanExecution`
and caught its Enforce exception to preserve the bundle. It does not convert
the failed Report to PASS or claim that a separate serialized Enforce call
was made in this experiment.

## Retained artifacts and replay inputs

| Request | Raw observations | Complete bundle | Original Reports |
| --- | --- | --- | --- |
| Initial working tree, before container-location annotation | [Observations](initial-observations.json) | [Bundle](initial-bundle.tar.gz) | [Passed](initial-passed-report.json), [failed](initial-failed-report.json) |
| Final implementation | [Observations](current-observations.json) | [Bundle](current-bundle.tar.gz) | [Passed](current-passed-report.json), [failed](current-failed-report.json) |
| Independent repeat of final implementation | [Observations](repeated-observations.json) | [Bundle](repeated-bundle.tar.gz) | [Passed](repeated-passed-report.json), [failed](repeated-failed-report.json) |

[Native CLI chunks](native-cli.json) retain the exact commands, tool-return
output and actual CLI exits. The existing managed-worktree Git-discovery
warning remains visible. These chunks are not a complete native Trace or
Span/Origin attribution.

[Source](source.tar.gz) contains the actual controlled Git repository,
tracked inventory, local document dependency and Lychee configuration.
The [Plan](source-plan.json) was produced by the existing
`selection.changeset → select → plan` APIs, with a real Git ODB and one changed
`docs/link.md`; no selected tests or fabricated inventory were supplied.
[Preparation source](prepare-source.txt) is the original temporary validation
script, retained as text. The fixture implementation is the committed
`link_cache_fixture.py`. The [initial source snapshot](initial-implementation.tar.gz)
retains the earlier working bytes; that first request is not relabelled as
the final implementation. [SHA256SUMS](SHA256SUMS) covers the archived evidence
and inputs; Git attributes preserve their bytes.

Replay the saved Source/Plan from within an authorized checkout:

```powershell
$env:FORCE_DARK_MODE = '1'
dagger api call fixture-link-cache --plan <plan.json> --source <source> export --path <bundle> --progress dots
```

The controlled Service is a fixture, not a production Check or a separate
execution runtime. Its POST reset/status actions are real disposable remote
mutations with their original responses and process times retained. They
are recorded separately from link GETs; no publishing or production remote
write was performed. The tool observation retains the actual binary SHA-256,
version, platform/machine and versioned asset URL. It does not claim that
this observed hash is a newly verified upstream release-asset pin.

## Limits and focused checks

The final Git/Lychee Native Results record their actual container location
(`dagger`) while retaining the outer `dagger_export` adapter context.
`origin_execution_attributed=false`, `source_binding_verified=unknown` and
`trusted=false` remain explicit. Cache reuse and original process times do
not prove #87 fresh Tool Origin or #63 Trusted historical reuse. This
experiment validates the configured local-document and online-link paths;
it does not establish caching safety for arbitrary custom preprocessors,
consumer side effects or every remaining Check configuration.

Focused local checks: the four affected suites completed with **168 passed,
1 skipped**. After the container-location repair, the link/Plan suites passed
**82 tests**, and the six affected Host docs/inventory contract tests passed.
The final nine authored Python files pass scoped Ruff check and format.
Unit doubles validate policy/metadata flow, not native cache behavior; the
bundles above provide the real Engine observations. Required current-head
CI is reported separately on the PR. No Weaver, OTLP capture or full local
engineering matrix was rerun.

During capability verification, a no-module API query returned the same
second-resolution `currentTimestamp` value for two aliases. The actual
online request therefore uses the more precise Module clock only at the
network-reading Exec input. Native Function cache policy and Exec cache
remain distinct, as described by the
[official Dagger cache-control article](https://dagger.io/blog/cache-control-for-modules/).
Lychee's [official cache documentation](https://lychee.cli.rs/recipes/caching/)
explains why refreshing the Exec also requires disabling its response-file
cache. Incidental SDK/documentation observations are recorded in the
[upstream observations](../../../.agent/upstream-observations.md).

Parent: [Verification index](../README.md).
