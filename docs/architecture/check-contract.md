# Check identity and Native Result contract

Status: first #52 slice for [ECI #52-A](https://github.com/dymzz/engineering-ci/issues/52)
and the #66 S1-A handoff. Schema version 1; implemented contract, not a Hook or
Required/trusted delivery.

The contract gives one stable `check_id` to a logical Check and a separate,
per-run `result_id`. It does not grant Required/Optional or `trusted` status and
does not replace Runtime `tool_id`/`group_id`/`access_id`.

## Existing path inventory

| Path | Current role | Contract status |
| --- | --- | --- |
| `selection._check_scope()` | Emits local display names (`docs`, `tach`, `source`, ...) and applicability | Display name is **not** a Check ID; mapped through the frozen table below |
| `plan_contract.PLAN_VERSION = 2` | Selection/Plan dependency declarations | Unchanged |
| `execution.SCHEMA_VERSION = 1` | Execution Report (`engineering-ci-execution-report`) | Extended with `check_contract`, per-check `check_id`/`result_id`/`native_result`; old fields retained |
| `execution.execute()` | Host execution and native check report | Emits the contract for known built-in checks |
| Dagger `execute-plan` | Container plan execution | **Gap:** does not yet emit this contract |
| Actions reusable selection job | Calls `select` then `execute` | Consumes the host report; no separate contract translation |
| Runtime `tool_id`/`group_id`/`access_id` | Tool/group/access identity | Optional `tool_id`/`access_id` fields in a Check definition; never used as `check_id` |

## Stable identities

Frozen built-in mapping (not a runtime registry; unknown names are refused, not
reinterpreted):

| Display name | `check_id` |
| --- | --- |
| `whitespace` | `eci/hygiene/whitespace` |
| `forwarding` | `eci/hygiene/forwarding` |
| `docs` | `eci/hygiene/docs` |
| `vulture` | `eci/hygiene/vulture` |
| `tach` | `eci/hygiene/tach` |
| `tach_external` | `eci/hygiene/tach-external` |
| `source` | `eci/sources/reconcile` |

- `check_id` is stable across Hook Job, Host, Dagger and Actions.
- `definition_version` and `rule_ref` describe the Check definition/rule
  version; a different rule version is a different result identity.
- `result_instance` distinguishes runs; `result_id` is a SHA-256 of the
  canonical Native Result payload.
- `tool_id`/`access_id` are optional and never inferred from a tool or Job name.
- A missing identity is `check_identity_missing`; a check that would otherwise
  pass is downgraded to `not_executed`, never passed by display name.

## Public API

`src/engineering_ci/check_contract.py`:

```python
CONTRACT_VERSION = 1
CHECK_CONTRACT_KIND = "engineering-ci-check-contract"
RESULT_KIND = "engineering-ci-native-check-result"

definition_for(name) -> CheckDefinition | None
check_id_for(name) -> str | None
rule_ref(name, version, *, path=None, digest=None) -> dict
profile_ref(name, version) -> dict
source_ref(snapshot, *, tree=None, head_commit=None, index_tree=None, content_digest=None) -> dict
native_result(definition, *, source, status, result_origin, result_instance, ...) -> dict
validate_result(result) -> dict
validate_result_set(results) -> bool
```

Result origins: `host`, `dagger`, `actions`, `direct_tool`, `unknown`. Result
statuses: `passed`, `failed`, `advisory`, `not_applicable`, `not_executed`,
`unknown`.

## Representative result

```json
{
  "kind": "engineering-ci-native-check-result",
  "schema_version": 1,
  "check": {
    "check_id": "eci/hygiene/docs",
    "definition_version": 1,
    "tool_id": null,
    "access_id": null,
    "rule_ref": {
      "name": "engineering-ci.hygiene",
      "version": "1",
      "path": "ci-hygiene.toml",
      "sha256": "…"
    },
    "profile_ref": null
  },
  "source_ref": {
    "snapshot": "worktree",
    "head_commit": "…",
    "binding_status": "identity_only"
  },
  "input_refs": [{"kind": "config", "path": "ci-hygiene.toml", "sha256": "…"}],
  "status": "passed",
  "native_exit_code": 0,
  "result_origin": "host",
  "runner_ref": "execution:host",
  "result_instance": "run-id:host:0:docs",
  "artifact_refs": [],
  "coverage": null,
  "reason": null,
  "result_id": "sha256:…"
}
```

`binding_status` is explicit: `byte_tree` (immutable Tree), `content_digest`
(content-bound worktree), `identity_only` (commit/index identity only), or
`unknown`. An `identity_only` result does not prove the exact bytes read.

## Execution Report integration

`execution.execute()` keeps its existing fields and status (`blocked` remains an
execution-layer refusal). For every selected check it adds:

- `check_id`, `check_version`;
- `check_contract` status (`valid`, `identity_missing`, or a contract error);
- `source_ref`, `result_origin`;
- `result_id` and the full `native_result`.

Unknown selected names remain `not_executed` with `check_identity_missing`.
The report top-level adds `check_contract` kind/version.

## Verification

`tests/test_check_contract.py`:

- same Check across two Job instances keeps `check_id` but has different
  `result_id`;
- one Job can carry multiple distinct Check IDs;
- missing/invalid Check ID is refused, not inferred from a name;
- native failure/exit is preserved;
- rule/origin changes produce a new result and conflicting definitions are
  refused;
- duplicate `result_id` is refused.

`tests/test_execution.py` additionally checks that a real docs execution emits
`eci/hygiene/docs`, a rule digest, `source_ref`, and `result_id`.

## Limits / next slices

- Dagger `execute-plan` and Actions do not yet emit the same contract.
- Only the frozen built-in checks have IDs; external native tools and #52
  Runtime Check/Profile interfaces still require the #52/Runtime contract.
- `profile_ref` is pass-through metadata; it cannot grant Required/trusted.
- This slice does not define Expected Check enumeration for #66 or Hook routing;
  it defines the result/identity primitive those consumers will use.
