# CI/CD Telemetry Verification

Local verification for [architecture/cicd-telemetry.md](../architecture/cicd-telemetry.md)
and Issue [#75](https://github.com/dymzz/engineering-ci/issues/75).

## First slice (candidate-record producer)

| Case | Input | Expected |
| --- | --- | --- |
| Complete Run + Job + bounds | Separate run/job conclusions + timestamps + Task URL | `status=produced`, SERVER + INTERNAL spans, `evidence_class=candidate_record` |
| Job success / Run failure | Distinct conclusions | Pipeline `failure` (+ `error.type`), Task `success` |
| Job success / Run in progress | Job terminal only | `partial`, Task span only |
| Missing Run ID / Attempt | Incomplete facts | `incomplete`, no spans, exit 3 |
| Missing timestamps | Terminal conclusions without bounds | `incomplete` / no fabricated durations |
| Missing Task URL | Otherwise complete Job | `partial`, Pipeline only |
| Cancel / skip / timeout | GitHub conclusions | SemConv `cancellation` / `skip` / `timeout` |
| Two Jobs, one Run | Different `job_id` | Distinct Task span ids; stable Pipeline/trace ids |

Run:

```text
uv run pytest tests/test_cicd_telemetry.py -q
```

Live GitHub Actions Run → authenticated API → OTLP/gRPC receiver evidence and
Weaver sample handoff remain later slices; `produced` here means candidate
records only.
