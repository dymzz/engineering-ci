# CI/CD Telemetry Verification

Local verification for [architecture/cicd-telemetry.md](../architecture/cicd-telemetry.md)
and Issue [#75](https://github.com/dymzz/engineering-ci/issues/75).

## First slice (artifact producer)

| Case | Input | Expected |
| --- | --- | --- |
| Complete Run + Job + conclusion | Explicit facts JSON | `status=produced`, Pipeline + Task spans, exit 0 |
| Missing Run ID / Attempt | Incomplete facts | `status=incomplete`, `reason=missing_run_identity`, no spans, exit 3 |
| Missing terminal conclusion | Run identity only | `status=incomplete`, `reason=missing_terminal_result`, exit 3 |
| Run without Job identity | Pipeline-complete facts | `status=partial`, Pipeline span only, exit 0 |
| Selection statuses | N/A | Never appear as `cicd.pipeline.result` |

Run:

```text
uv run pytest tests/test_cicd_telemetry.py -q
```

Live GitHub Actions Run → OTLP/gRPC receiver evidence and Weaver sample
handoff remain later slices; this slice does not claim Weaver PASS.
