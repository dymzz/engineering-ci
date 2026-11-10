# Weaver checker evidence summaries (#74 / PR #78)

Unsigned local execution summaries extracted from `.tmp/weaver74/verified-*`
against PR HEAD `bd3ebe17` (pre observed-Span terminal binding). They are
**not** GitHub-signed Artifacts and do not admit #63/#61 trust.

| File | Case | Result |
| --- | --- | --- |
| `positive-live.summary.json` | Completed OTLP capture / live | passed |
| `terminal-passed-pre-bind.summary.json` | Terminal under prior assertion-only bind | passed |
| `terminal-missing-evidence.summary.json` | Terminal without completed evidence | failed |
| `negative-non-otlp.summary.json` | Candidate-record JSON as OTLP | failed |

Checksums: `SHA256SUMS`.

## Replay (deterministic host fixtures)

Committed controlled fixtures remain under `tests/fixtures/weaver/`. Host unit
coverage for the strengthened terminal bind is `tests/test_weaver.py` (observed
Run/Attempt/result/root mismatches). Full Dagger/Weaver container replay uses
the same pinned Weaver 0.28.0 Linux amd64 asset and Registry file manifest
described in [weaver.md](../weaver.md); do not treat these summaries as a
substitute for native `live_check.json` when re-running the container path.

After the observed-Span binding fix, terminal acceptance additionally requires
`cicd.pipeline.run.id`, `github.actions.run_attempt`, `cicd.pipeline.result`,
and a Root Span on the matched Pipeline sample / capture. Re-run terminal
cases on the new HEAD before claiming refreshed container evidence.
