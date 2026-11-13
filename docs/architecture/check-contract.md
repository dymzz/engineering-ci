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
  version; a different rule version is a different result identity. Constructor
  and validator both reject unsupported built-in definition versions.
- `requires_baseline` is controlled per built-in Check: `whitespace` and
  `forwarding` actually read the old source. Their native verdicts require
  resolved `base_commit` in `baseline_ref`; a caller cannot omit it to
  make different comparisons match.
- `result_instance` distinguishes runs; `content_id` is the hash of canonical
  result content without the instance, and `result_id` binds both content and
  instance. Validation recomputes both.
- `declared_target_tree` records the plan's intended Tree; it is **not** proof
  that a tool read those bytes. `actual_execution_source`, `verified_input_scope`
  and `binding_status` record the execution level separately.
- `byte_tree` is accepted only when a materializer supplies both a declared
  target Tree and a verified input scope; a Tree field alone yields
  `identity_only`, and a missing scope yields `unknown`.
- `tool_id`/`access_id` are optional and never inferred from a tool or Job name.
- A missing identity is `check_identity_missing`; a check that would otherwise
  pass is downgraded to `not_executed`, never passed by display name.
- `validate_result()` rejects mutated content, a mismatched `content_id` or
  `result_id`, unknown Check IDs, invalid source binding, and status/exit
  contradictions (`passed` with nonzero exit, `failed` with zero exit).
- Execution kinds are explicit: process Checks (`docs`, `tach`, `tach_external`,
  `vulture`, `whitespace`) require a real native exit and evidence for
  passed/failed; internal Checks (`forwarding`, `source`) carry structured
  evidence without a process exit. Configuration failures are `not_started`,
  carry no Native Result, and cannot be reported as native failures.
- `baseline_ref` is part of the result digest when a comparison baseline exists;
  equal new Trees with different baselines produce different `content_id`s.

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
source_ref(snapshot, *, declared_target_tree=None, actual_execution_source=None,
           head_commit=None, index_tree=None, content_digest=None,
           verified_scope=(), binding_status=None,
           scope_kind="changed_paths", materialization_ref=None) -> dict
native_result(definition, *, source, status, result_origin, result_instance, ...) -> dict
validate_result(result) -> dict
validate_result_set(results) -> bool
resolve_definitions(required=None) -> dict   # requested_names only
result_matches_expected(result, expected) -> bool  # candidate identity only
validate_expected_instance(expected) -> dict  # one supplied scoped Check, no completeness
result_covers_expected(result, expected) -> bool  # one native Check PASS, not Required
```

Result origins: `host`, `dagger`, `actions`, `direct_tool`, `unknown`. Result
statuses: `passed`, `failed`, `advisory`, `not_applicable`, `not_executed`,
`unknown`.

## Representative result

Illustrative payload (hashes/Tree values shown as placeholders, not a
copy-pastable validated Result):

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
    "declared_target_tree": "...",
    "actual_execution_source": "worktree",
    "head_commit": "...",
    "verified_input_scope": ["docs/guide.md"],
    "scope_kind": "changed_paths",
    "binding_status": "content_digest",
    "content_digest": "..."
  },
  "input_refs": [{"kind": "config", "path": "ci-hygiene.toml", "sha256": "…"}],
  "status": "passed",
  "native_exit_code": 0,
  "result_origin": "host",
  "runner_ref": "execution:host",
  "result_instance": "run-id:host:0:docs",
  "artifact_refs": [],
  "coverage": null,
  "evidence": {"stdout": "", "stderr": "", "findings": []},
  "baseline_ref": null,
  "reason": null,
  "content_id": "sha256:…",
  "result_id": "sha256:…"
}
```

`binding_status` is explicit: `byte_tree` (a materializer verified the exact
Tree and full input closure), `content_digest` (a content digest over the listed
scope), `identity_only` (commit/index/Tree identity without byte verification),
or `unknown`. `byte_tree` also requires `scope_kind="full_input_closure"` and a
`materialization_ref`; execution emits only `identity_only` or changed-path
`content_digest`.

## Definition scope

`resolve_definitions(required)` resolves an **explicit request list** only; it
never claims the consumer-wide set:

- all requested known → `complete`, reason `requested_names_complete`;
- some known, some missing → `partial` with `missing[]`;
- only unknown names → `unknown`;
- empty list or omitted input → `unknown`, reason `empty_request_not_proof` /
  `required_scope_missing`; neither is a legal "0 Expected" proof.

`result_matches_expected` is **candidate identity association only**. It does
not assert native PASS, input-equivalence coverage, an ExpectedSet-complete
scope, Evidence Admission or Required Gate. For comparison-dependent built-in
Checks, a missing or mismatched expected `baseline_ref` does not match.

`validate_expected_instance` and `result_covers_expected` remain
**single-supplied-Check primitives**: a scoped rule/source/definition/baseline
can be structurally checked and an actual native PASS associated with that
instance. They do **not** establish the completeness or authenticity of the
consumer's Expected collection, input closure provenance, Evidence Admission
or Required Gate. For a built-in comparison-dependent Check, the supplied
Expected instance must include the real baseline.

**No consumer-wide Expected/Required coverage API is produced by this first
#52-A slice.** The earlier `validate_completeness_receipt` and
`validate_expected_coverage` were withdrawn before release because
structural receipt strings cannot prove a Profile's ExpectedSet is complete,
including a legitimate empty set. Follow the existing
[Profile → Expected Requirements → Plan → Assessment](../../design/execution-graph/provider-and-gates.md)
design: #66-B derives the scoped Expected instances from the effective Profile,
#66-C reconciles routing and Native Results, #63 admits trusted coverage and
#61 makes the Required Gate decision. An empty Required subset does not imply
an empty set of applicable Checks.

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

- Execution does not emit `byte_tree`: immutable/Index snapshots are
  `identity_only`, and worktree content digests cover only changed paths. Exact
  full-input materialization remains #66 Source work.
- Dagger `execute-plan` and Actions do not yet emit the same contract; the Host
  report cannot be presented as three-entry-point parity.
- Only the frozen built-in checks have IDs; external native tools and #52
  Runtime Check/Profile interfaces still require the #52/Runtime contract.
- `profile_ref` is pass-through metadata; it cannot grant Required/trusted.
- `resolve_definitions` resolves only the built-in scope; Expected Check
  enumeration, Job routing and Required aggregation remain #66/#63/#61 work.
- `result_matches_expected` is candidate identity association, not a
  satisfaction, trusted coverage, applicability or Required decision.
- If a native Check ran but its Result contract is invalid, its original
  native status and diagnostics remain in the Report; `execution.execute()`
  blocks with `check_contract_invalid`. Configuration/pre-tool failures,
  including a missing Tach config, are `not_started` without a Native Result
  and preserve the original failure reason.
