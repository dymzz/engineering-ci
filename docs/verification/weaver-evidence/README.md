# Weaver checker evidence summaries (#74 / PR #78)

Unsigned local execution summaries extracted from `.tmp/weaver74/verified-*`
against earlier PR HEAD `bd3ebe17` (pre observed-Span terminal binding). They
are **not** GitHub-signed Artifacts and do not admit #63/#61 trust.

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
Run/result/root mismatches; Attempt correlation gate; extensible result typing).
Full Dagger/Weaver container replay uses the same pinned Weaver 0.28.0 Linux
amd64 asset and Registry file manifest described in [weaver.md](../weaver.md);
do not treat these summaries as a substitute for native `live_check.json` when
re-running the container path.

Current terminal acceptance (unit-tested on HEAD): Resource
`cicd.pipeline.run.id`, Span `cicd.pipeline.result` (capture/sample conflict
rejected), Root Span, and **verified** Attempt correlation for
`weaver-terminal` (extension present and matching declared Attempt). Cart
fixtures remain Weaver/matcher evidence only — not CI/CD SemConv Required
coverage. Refresh native Terminal container evidence after these gate changes
before claiming a new PASS bundle.
