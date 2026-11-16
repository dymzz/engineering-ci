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

Observed: exit `0`, one filesystem result, six `Vulnerabilities` for `lodash`.
The report itself is committed as `trivy-vuln-report.json`; its SHA-256 is
`7a4f3c924b5007c6754db49c8ae13e424dfa74285d9293255014b16bd9eaf780`.

## Sanitized adapter result

`sanitized-result.json` was produced by:

```text
scan_filesystem(fixture, target=".", scanners=("vuln",))
```

It reports `status=findings`, `native_exit_code=0`, six findings, and only
hashed vulnerability/package identities. Target content digest:
`sha256:5d874500846b120d47224420b0ea43a243eb193e57e538a8f2bb21bd753c91a7`.

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
