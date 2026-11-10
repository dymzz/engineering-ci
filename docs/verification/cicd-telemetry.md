# CI/CD Telemetry Verification

Local verification for [architecture/cicd-telemetry.md](../architecture/cicd-telemetry.md)
and Issue [#75](https://github.com/dymzz/engineering-ci/issues/75).

## Candidate records + API/OTLP JSON slice

| Case | Input | Expected |
| --- | --- | --- |
| Complete Run + Job + bounds | Separate run/job conclusions + timestamps + Task URL | `status=produced`, SERVER + INTERNAL spans, `evidence_class=candidate_record` |
| Job success / Run failure | Distinct conclusions | Pipeline `failure` (+ `error.type`), Task `success` |
| Job success / Run in progress | Job terminal only | `partial`, Task span only |
| Explicit null Run + Job success | Independent fields | No Pipeline terminal span |
| Legacy shared `conclusion` | Single field only | Ignored; no fabricated terminals |
| Missing Run ID / Attempt | Incomplete facts | `incomplete`, no spans, exit 3 |
| Missing timestamps | Terminal conclusions without bounds | `incomplete` / no fabricated durations |
| Reversed Run/Job bounds | `start > end` | `invalid_*_timestamps`; no affected span |
| Equal timestamps | Compatible `start == end` | Valid zero-duration span |
| Job after completed Run | Job start/end `> Run completed` | `partial` + `inconsistent_run_job_timestamps` |
| `neutral` / `action_required` / `stale` | Unmapped GitHub conclusions | `unsupported_*_conclusion`; native facts retained |
| API Run+Job mapping | Injected `gh api` payloads | Independent terminals; `github_api_fetched` |
| In-progress API Run | `status=in_progress` | No Run completed-at; `partial` |
| OTLP/JSON flush | `--otlp-json` writable path | File written; `otlp_json_exported`; `otlp_delivered=false` |
| OTLP/JSON flush failure | Unwritable path / no spans | exit 3; no delivery claim |

Run:

```text
uv run pytest tests/test_cicd_telemetry.py -q
```

Live OTLP/gRPC receiver delivery and Weaver sample handoff remain later slices.
Manual API check (optional, requires `gh` auth):

```text
engineering-ci cicd-telemetry --repository dymzz/engineering-ci --run-id <id> --job-id <id> --otlp-json otel.json
```
