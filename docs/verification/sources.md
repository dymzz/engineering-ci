# Source reconciliation — local verification

Scope: P1-2 slice of [ECI #66](https://github.com/dymzz/engineering-ci/issues/66).
Date: 2026-11-08; host: Windows 11, CPython 3.14.3, Git for Windows.

Contract: [source reconciliation](../architecture/sources.md).

## What ran

```sh
pytest -q tests/test_sources.py
# 15 passed

pytest -q tests \
  --deselect "tests/test_security_policy.py::TestPolicyDocument::test_example_policy_is_a_valid_publish_input"
# 489 passed, 8 skipped, 1 deselected, 187 subtests passed

dagger call sources-self-test
# engineering-ci-sources-report:passed:1
```

## Knowledge consumer acceptance

Real Knowledge checkout, using its own fields (`entries`, `local_path`,
`sha256`, `size_bytes`), `data/acquired` root, README exemption and
`.lake/packages/`/`.venv/` environment prefixes:

```text
engineering-ci sources --root ../engineering-knowledge --config ci-sources.toml --json
# status passed; expected=937 actual=937 checked=937; 0 failure
```

The old `check_acquired_catalog(set(), full=True)` on the same checkout produced
`0` failures. A temporary Knowledge-shaped repository was then used to compare
both implementations:

| Case | Old | New |
| --- | --- | --- |
| Valid acquired file | PASS | `passed` |
| Missing declared file | FAIL | `failed` (`missing`) |
| Unregistered acquired file | FAIL | `failed` (`unregistered`) |
| Conflicting digest for same path | FAIL | `failed` (`conflict`) |
| Wrong SHA-256 | FAIL | `failed` (`digest`) |
| `text=set` | FAIL | `failed` (`attributes`) |
| Exempt `README.md` | PASS | `passed` |

New checks are intentionally stricter for duplicate identical paths, size
mismatches and environment entries that claim byte evidence; those are review
requirements, not silent behavior changes.

The tests create a disposable tree with `data/acquired`, a manifest and a
consumer config. Git attributes are represented by a fake native runner so no
real Git repository is needed for the unit matrix.

| Behaviour | Observed |
| --- | --- |
| Valid declaration | `passed`; one checked entry |
| Missing file | `missing` |
| Extra acquired file | `unregistered` |
| Same path repeated, same digest | `duplicate` |
| Same path repeated, different digest | `conflict` |
| Digest mismatch | `digest` |
| Size mismatch | `size` |
| `../` path | `path_invalid` |
| Absent environment coordinate | allowed, no `missing` |
| Exempt documentation file | not `unregistered` |
| `text=set` | `attributes` |
| `git check-attr` failure | `attributes_unavailable` |
| Custom field names (`source_path`, `digest`, `bytes`) | `passed`; Knowledge-style projection works without ECI knowing the names |
| Environment entry claiming bytes | `environment_has_digest` |
| Manifest path outside acquired roots | `path_outside_roots` |
| Null local path without evidence | allowed |
| Null local path with digest | `path_missing_evidence` |
| CLI | JSON report, exit 1 preserved |

## Limits

- Knowledge's real manifest/config passed the normal-case comparison and the
  seven-case equivalence matrix; the production Knowledge workflow still needs
  to switch to this entry in its own change.
- The Dagger self-test sets `require_text_unset = false`; a container has no
  `.git`. Host attributes or a verifiable native result must be passed in the
  production check.
- Tach/Lychee acquisition and Dagger Source construction remain open.
