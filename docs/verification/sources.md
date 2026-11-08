# Source reconciliation — local verification

Scope: P1-2 slice of [ECI #66](https://github.com/dymzz/engineering-ci/issues/66).
Date: 2026-11-08; host: Windows 11, CPython 3.14.3, Git for Windows.

Contract: [source reconciliation](../architecture/sources.md).

## What ran

```sh
pytest -q tests/test_sources.py
# 12 passed

pytest -q tests \
  --deselect "tests/test_security_policy.py::TestPolicyDocument::test_example_policy_is_a_valid_publish_input"
# 486 passed, 8 skipped, 1 deselected, 187 subtests passed

dagger call sources-self-test
# engineering-ci-sources-report:passed:1
```

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
| CLI | JSON report, exit 1 preserved |

## Limits

- Knowledge's real manifest/config is not loaded yet; the equivalence matrix
  from the review is the next acceptance step.
- The Dagger self-test sets `require_text_unset = false`; a container has no
  `.git`. Host attributes or a verifiable native result must be passed in the
  production check.
- Tach/Lychee acquisition and Dagger Source construction remain open.
