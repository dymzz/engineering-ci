# no-samples

Actual #52 Task-only acceptance input, fixed #74/#75 candidates.
Native exit/gate `0/1`;
Consumer exit `1`, status `rejected`.
Consumer reason: `expected_task_missing`.

- [Native Report](report.json), [original stderr](live.stderr).
- [Complete raw Artifact](artifacts.tar.gz), including native `live_check.json`.
- [Consumer result](consumer-result.json), [hash and execution receipt](receipt.json).
- Reconstruct Source from [shared base](../../source.tar.gz) and this case's
  `source-delta.tar.gz` when present. Every resulting file matches actual input bytes.

Native and complete-source decisions have different responsibilities. See
[acceptance scope and explanation](../../README.md) and [case index](../README.md).
