# Dependency integrity and security gates

Lock consistency and vulnerability scanning answer different questions:

- `uv lock --check` checks whether project configuration and the lockfile agree.
- OSV-Scanner checks locked package/version pairs against known advisory records.

A lockfile can pass consistency checking and still contain known vulnerabilities.
Neither check proves exploitability in a running application.

## Modes and verdicts

The reusable workflow defaults to `dependency_security_mode: off`. No security
job, scanner installation or advisory query runs in that mode. Existing pytest,
coverage, matrix and Dagger checks retain their behavior.

| Mode | Security execution | CI behavior |
|---|---|---|
| `off` | Not applicable; no query | Existing checks only |
| `report` | Check lock, scan, publish facts | Findings/errors do not block; status remains failed/error |
| `enforce` | Same checks and evidence | Findings, integrity failures, scanner errors and coverage gaps block |

Report statuses are `passed`, `failed`, `error` and `not_applicable`.
`integrity.status` and `scanner.status` are separate. `gate.passed` records
policy compliance; `gate.blocking` and `gate.exit_code` record CI behavior.
Report-mode errors have `passed: false`, `blocking: false`, `exit_code: 0`;
they are never called safe. Off-mode `gate.passed` is null.

Enforce blocks every unexcepted advisory returned by the scanner. There is no
inferred severity threshold or exploitability judgment. Exit 1 is a finding
verdict only when valid JSON and complete package coverage agree. Exit 129 is
an advisory API failure; 127/128/130 and other unexpected exits are errors.
Missing/invalid JSON, incomplete coverage and exit/result disagreement are
errors, even if the process exits 0.

## Workflow usage

After v0.4.6 is published, explicitly enable the optional lightweight job:

```yaml
jobs:
  python-ci:
    uses: dymzz/engineering-ci/.github/workflows/reusable-python-ci.yml@v0.4.6
    with:
      dependency_security_mode: enforce
      dependency_security_project: .
      # Optional reviewed native OSV exceptions:
      # dependency_security_exceptions: config/osv-scanner.toml
    secrets:
      ENGINEERING_CI_READ_TOKEN: ${{ secrets.ENGINEERING_CI_READ_TOKEN }}
```

The independent security job does not start a Dagger Engine. It checks out the
helper matching `dependency_security_source_ref` (default `v0.4.6`).
Candidate validation must set this input to the candidate SHA. Core CI uses
its tested SHA and report mode on a controlled public fixture. It does not
change consumers' default policy.

The job uses uv 0.12.23 and official OSV-Scanner 2.6.0. Linux amd64 and Windows
amd64 assets have fixed SHA256 digests. Every use, including a cache hit or
explicitly supplied binary, verifies digest and runtime version. Unsupported
platforms are errors.

## Standalone use

Python 3.11+ and uv are required. The helper uses only the Python standard
library; it does not install the project's locked dependencies.

```sh
python scripts/dependency_security.py --project /path/to/consumer \
  --mode report --out /tmp/dependency-security \
  --cache /tmp/engineering-ci-security-bin
```

Use `--mode enforce` for blocking. `--scanner /path/to/osv-scanner` accepts only
the pinned, checksum-verified platform binary. `--timeout` defaults to 120
seconds; `--max-findings` defaults to 100. Workflow project and exception paths
must stay inside the caller repository.

## Scope, privacy and incomplete scans

The first version scans public PyPI package/version entries in uv.lock,
including direct/transitive dependencies and entries for different platforms,
Python versions and dependency groups. Findings concern the lockfile, not
installed packages. It does not filter by Matrix row; the workflow scans once.

By default only exact official PyPI registry URLs are accepted. A project can
explicitly declare trusted public PyPI mirrors using repeated --public-index
arguments or dependency_security_public_indexes (one exact HTTPS URL per line).
This is an auditable assertion that the source uses the public PyPI namespace,
not automatic detection of whether a registry is public. The accepted URLs
are recorded in public_registry_policy. Never declare private registries as
public. Credentials, query tokens, wildcards and insecure URLs are rejected.

Undeclared/custom private sources, VCS, direct URLs and third-party local
dependencies produce a coverage gap. No OSV query is sent for that lockfile;
enforce fails closed. Public package names/versions are sent only after the
explicit source policy is satisfied.

The native extractor excludes the project's own virtual node. For its own
editable root node, an ephemeral copy marks only that node virtual. The original
lockfile remains byte-for-byte unchanged; all third-party metadata is checked
for equality. Coverage is verified against native `--all-packages` JSON,
including packages without findings.

Online queries send public package names and locked versions to OSV. No
repository token, index credential or environment dump is an artifact.
Subprocess logs remain ephemeral; diagnostics contain fixed categories,
original exits and timings. Explicit validated config overrides automatic
OSV config discovery.

This version supports online scanning only. Binary caching is not an offline
advisory database or a cached security verdict. An outage is an error; the
helper never substitutes empty findings or a previous pass. uv checking can
itself need network/interpreter metadata; its timing is measured separately.

## Native finite exceptions

The commented [example](../config/osv-scanner.example.toml) enables nothing.
Native `[[IgnoredVulns]]` entries require an exact CVE/GHSA/PYSEC ID, a nonempty
reason and `ignoreUntil` as a UTC timestamp or date. Dates expire at 00:00 UTC.
Only unexpired entries are forwarded; OSV performs native alias handling.
Expired entries remain visible and cannot suppress a finding. Wildcards,
package overrides, missing expiry, duplicate IDs and other keys are rejected.

The report counts configured active exceptions, not presumed used exceptions.
Native JSON excludes advisories suppressed by OSV; the helper does not invent
records or claim an exception was used without tool evidence.

## Evidence and bounds

The job always attempts a separate Dependency Security Summary and native
GitHub Artifact (default `dependency-security`, retention 7 days):

| File | Evidence |
|---|---|
| `report.json` | Schema v1, scope, original exits, independent verdicts, gate, bounded findings, exceptions, coverage gaps, timings |
| `scanner.json` | Full native JSON with normalized source path, or explicit unavailable reason |
| `diagnostics.json` | Fixed categories, exits and timings; no raw stderr |
| `summary.md` | Bounded human view of the same result |

Findings identify package, locked version, advisory ID and only fixed versions
supplied for that package by OSV. Counts describe advisory records; aliases may
represent one issue. JSON/Markdown omission counts are explicit. Full native
results are retained up to 10 MiB; oversized inputs/results fail closed with a
size diagnostic. Lockfiles are bounded to 5 MiB / 10,000 package entries.

Delivery does not determine the verdict. Enforce exits nonzero before the
always-run Summary/Artifact steps; successful delivery cannot turn failure green.

## Validation and performance

`tests/test_dependency_security.py` uses injected processes and fixed data,
without network. It covers clean scans, findings, distinct integrity failures,
API errors, bad JSON, modes, finite/expired exceptions, private sources, missing
coverage, exit disagreement, checksum rejection and bounded output.

The public fixture `tests/fixtures/dependency-security` deliberately pins idna
3.11 for real advisory output. Scanning never installs/imports it. Do not copy
it into an application. Changing database results are not permanent unit-test
expectations.

Reports measure lock reading, uv checking, scanner initialization, advisory
query and total helper time. Binary cache hits are recorded. Warm reuse saves
installation cost; advisory queries still run. Actual CI job duration also
includes checkout/setup/delivery. Default-off extra security job cost is zero.
Parallel job duration does not necessarily add to the workflow critical path.

| Selection criterion | OSV-Scanner v2.6.0 | pip-audit (current documented interface) |
|---|---|---|
| uv.lock | Direct native extractor | Locked project flow documents pylock.*.toml; requires export/adaptation for uv.lock |
| Environment cost | Standalone binary, no package installation | Environment/requirements/project audits; exported locked input requires an extra step |
| Machine evidence | Native JSON and all-packages coverage | JSON available |
| Advisory service | OSV | PyPI or OSV available |
| Failures | Separate native findings, API, config and generic error exits | Separate findings verdict; execution errors still need handling |
| CI integration | Pinned verified platform binary | Python tool environment |
| Maintenance here | Native uv parser and finite exception config | Additional lock export and coverage mapping |

OSV is selected for direct uv.lock extraction, all-packages evidence, standalone
execution and native exceptions. Only this engine is integrated.

Local candidate measurements on Windows amd64 (2026-10-10, online database;
these are observations, not performance guarantees):

| Case | Lock integrity | Security | Total helper time |
|---|---|---|---|
| Public idna 3.15 | passed | passed, zero advisory records | 3.77 s |
| Public idna 3.11 | passed | failed, 2 records aliasing CVE-2026-45409 | 2.35 s (enforce) |
| Valid native CVE exception | passed | passed, aliases suppressed by OSV | 1.84 s |
| Expired exception | passed | failed, exception not forwarded | 2.09 s |
| Smoke candidate lock with explicit Aliyun public mirror | passed | passed, 9 locked package/version pairs | 1.85 s |
| Empty binary cache, same clean fixture | passed | passed | 66.15 s |
| Warm verified binary cache, same clean fixture | passed | passed | 1.66 s |

The cold initialization took 64.84 s versus 0.14 s warm; query time was
1.19 s versus 1.39 s. This cache stores the binary, not advisory answers.
Formal Smoke workflow integration remains a separate post-release PR.

Primary references: [OSV v2.6.0 release](https://github.com/google/osv-scanner/releases/tag/v2.6.0),
[configuration](https://github.com/google/osv-scanner/blob/v2.6.0/docs/configuration.md),
[result model](https://github.com/google/osv-scanner/blob/v2.6.0/pkg/models/results.go),
[pip-audit documentation](https://github.com/pypa/pip-audit/blob/main/README.md).
