# Source manifest reconciliation

Status: P1-2 slice of [ECI #66](https://github.com/dymzz/engineering-ci/issues/66).
The generic algorithm is delivered; loading Knowledge's real manifest and
comparing pre/post conclusions remains a consumer acceptance step.

`engineering-ci sources` enumerates declared source files, compares the manifest
with the actual file set, verifies size/SHA-256 and checks Git attributes on a
host that has `.git`. It does not know any Knowledge field names or directories;
those are configuration.

## Configuration

```toml
schema_version = 1
manifest = "data/catalog/manifest.json"
entries_key = ""                 # optional: key holding the entry list
path_field = "path"
sha256_field = "sha256"
size_field = "size_bytes"       # empty string disables size checks
acquired_roots = ["data/acquired"]
exempt_paths = ["data/acquired/README.md"]
environment_prefixes = ["sources/environment/"]
require_text_unset = true
require_acquired_roots = true
environment_requires_no_digest = true
require_under_acquired_roots = true
```

- `entries_key` supports a manifest that wraps the list; without it the JSON
  document itself must be a list.
- `path_field`/`sha256_field`/`size_field` are the consumer's Manifest field
  projection. Knowledge's names stay in Knowledge's config.
- `exempt_paths` and `environment_prefixes` are explicit allowances, not
  implicit "anything under data/" exemptions.
- `environment_prefixes` entries are skipped only when they claim no byte
  evidence (`sha256`/`size` both null); an environment path with a digest fails.
- `require_acquired_roots` requires each root directory to exist.
- `require_under_acquired_roots` rejects expected paths outside the declared
  roots.
- `require_text_unset = true` makes `git check-attr text` a required native
  check; `unset` is the expected value for acquired bytes.

## Algorithm and failures

| Code | Meaning |
| --- | --- |
| `manifest_missing` / `manifest_unreadable` | Manifest cannot be read |
| `manifest_invalid` / `entry_invalid` | Wrong JSON shape or non-object entry |
| `path_invalid` / `digest_invalid` / `size_invalid` | Unsafe path (`..`, absolute, backslash, drive), bad digest/size |
| `path_missing_evidence` | Entry has byte evidence but no local path |
| `path_outside_roots` | Path is outside every acquired root |
| `root_missing` | A required acquired root directory is absent |
| `environment_has_digest` | Environment-managed path claims byte evidence |
| `duplicate` | Same path repeated with identical digest/size |
| `conflict` | Same path repeated with different digest/size |
| `missing` | Declared, non-exempt, non-environment file absent |
| `unregistered` | Actual acquired file absent from the manifest |
| `size` / `digest` | Real bytes differ from the declaration |
| `unreadable` | File exists but cannot be read |
| `attributes` / `attributes_invalid` | `text` is not `unset`, or native output is malformed |
| `attributes_unavailable` | `git check-attr` could not run (for example no `.git`) |

Path normalization is POSIX-only and rejects absolute paths, drive prefixes,
backslashes and `..` segments. Duplicate and conflicting entries are distinct
failures; the first declaration is retained for later checks.

`environment_prefixes` entries are exempt from presence and enumeration.
`exempt_paths` are removed from both expected and actual sets. Every other file
under `acquired_roots` is `unregistered`, even when it is documentation.

Git attributes run in the caller's host checkout because Dagger source sets
exclude `.git`; the Dagger self-test disables the attribute requirement rather
than pretending it ran. A container-only call must therefore not report
attribute success.

## CLI and Dagger

```sh
engineering-ci sources --root . --config ci-sources.toml --json
```

The report is `engineering-ci-sources-report` schema 1 with `status`, `counts`
and `failures[]`; exit `1` on failure.

Dagger imports the same `engineering_ci.sources` implementation. Verified:

```text
dagger call sources-self-test
# engineering-ci-sources-report:passed:1
```

The self-test uses a disposable manifest/file layout and does not replace the
Knowledge consumer acceptance.

## Remaining P1-2 work

- Load Knowledge's actual Manifest projection, acquired roots and exemptions
  through this public entry and compare pre/post conclusions for the seven
  cases in the review.
- Wire the same reconciliation into a Dagger check that receives a host-produced
  attribute result or runs on a host with `.git`; a container must not claim
  attributes were checked.
- Add Tach/Lychee tool acquisition and Source construction (P1-3 remainder).
