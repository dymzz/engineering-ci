# Trivy 0.76.1 filesystem vulnerability scan evidence

- Date: 2026-11-16 (Atlantic/Reykjavik)
- Code under test: `0871881` (`src/engineering_ci/trivy.py`, `tests/test_trivy.py`)
- Platform: Windows x64
- Trivy version: `0.76.1`
- Trivy Windows x64 binary SHA-256:
  `94618922c9eb324d7099bf217b01504bfc1f87a0d46d6008cb24ec89b1d1a4bb`
- Fixture: `fixture/package-lock.json`, lodash `4.17.15`
- Fixture SHA-256:
  `f465ff3e7e1c61a4e4701a935f9231eafa595ca3f93cbd61487cd4c2395d8a5b`

## Native command

```text
trivy fs docs/verification/trivy-fs/fixture \
  --scanners vuln \
  --format json \
  --output docs/verification/trivy-fs/trivy-vuln-report.json \
  --quiet --no-progress --exit-code 0
```

Observed: native exit `0`, one filesystem result, six `Vulnerabilities` for
`lodash`.

The archived native report is `trivy-vuln-report.json`:

- bytes: `32282`
- SHA-256:
  `d9e485f0acfaf1b9cb500563d89569b1a2d45458f4931d0e60a36f5702a23d93`

## Sanitized adapter result

`sanitized-result.json` is derived from that same archived native report via
the public `_sanitize` mapping and the adapter's target/coverage metadata. Its
`report_sha256` is therefore identical to the archived report SHA-256 above.

- `status`: `findings`
- `native_exit_code`: `0`
- findings: six, only hashed vulnerability/package identities
- target content digest:
  `sha256:5d874500846b120d47224420b0ea43a243eb193e57e538a8f2bb21bd753c91a7`
- sanitized result SHA-256:
  `3e9c85ade64c9a2d3fed6317ff3ea57c55484fadb408bb1471ac56a10b098a99`

First finding: vulnerability `sha256:f918aeba...`, severity `HIGH`, package
`sha256:94a755535d135f12850ccd5848d10cc7266bbcdb74ad22c7817228ce7abaa506`.

## DB condition

The scan downloaded/used the local Trivy vulnerability database at run time.
No DB snapshot identity was recorded, so `db.authority` remains `unknown`;
this evidence proves the native vulnerability path and finding/report identity,
not DB freshness or a Required/Release decision.

## Reproduction

With the fixed binary available:

```text
pytest -q tests/test_trivy.py -k real_vulnerability
```
