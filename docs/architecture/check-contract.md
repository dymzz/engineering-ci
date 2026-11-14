# Check identity and Native Result contract

Status: #52 Check/Native Result contract, first delivered by
[ECI #52-A](https://github.com/dymzz/engineering-ci/issues/52) (PR #93) and
extended by the Native Tool slice. Contract/result schema version 2;
implemented contract, not a Hook or Required/trusted delivery. Two versions are
independent:

- **Result data schema** (2) formats one Native Result; schema 1 results stay
  readable under their original rules.
- **Check definition version** describes when a Check can be satisfied. Checks
  that gained tool requirements are version 2; version 1 (pre-tool) definitions
  stay readable but cannot satisfy the upgraded Check.

The contract gives one stable `check_id` to a logical Check and a separate,
per-run `result_id`. One Check may require several native tools, each recorded
as its own Native Result. It does not grant Required/Optional or `trusted`
status and does not replace Runtime `tool_id`/`group_id`/`access_id`.

## Existing path inventory

| Path | Current role | Contract status |
| --- | --- | --- |
| `selection._check_scope()` | Emits local display names (`docs`, `tach`, `source`, ...) and applicability | Display name is **not** a Check ID; mapped through the frozen table below |
| `plan_contract.PLAN_VERSION = 2` | Selection/Plan dependency declarations | Unchanged |
| `execution.SCHEMA_VERSION = 1` | Execution Report (`engineering-ci-execution-report`) | Extended with `check_contract`, per-check `check_id`/`result_id`/`native_result`; old fields retained |
| `execution.execute()` | Host execution and native check report | Emits the contract for known built-in checks |
| Dagger `execute-plan` | Container plan execution | Calls the Host `execution.execute()` on the exported Source, so it emits this contract, but `execution_context=dagger_export` maps to `result_origin=unknown` and the Dagger tool pins are not recorded |
| Actions reusable selection job | Calls `select` then `execute` | Consumes the host report; no separate contract translation |
| Runtime `tool_id`/`group_id`/`access_id` | Tool/group/access identity | A declared alternative may name a verified `tool_id`; results record only observed evidence. Never inferred from a name and never used as `check_id` |

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
- `tool_id`/`access_id` are optional definition fields and are never inferred
  from a tool or Job name; per-Check tool requirements are declared separately
  below.
- `definition_version` describes the Check's satisfaction contract. The five
  tool-bound Checks are version 2; the pre-tool version 1 definitions remain
  readable but cannot satisfy a version 2 Check. This is independent of the
  Result data schema.
- A missing identity is `check_identity_missing`; a genuinely executed Check
  retains its original native status and diagnostics. The Host records
  `check_contract=identity_missing` and blocks the top-level Report as
  `blocked/check_contract_invalid`, rather than forging `not_executed`.
- `validate_result()` rejects mutated content, a mismatched `content_id` or
  `result_id`, unknown Check IDs, invalid source binding, and status/exit
  contradictions (`passed` with nonzero exit, `failed` with zero exit).
- `not_executed` never carries a native process exit. `not_applicable` may
  report no exit, or a successful process exit with diagnostic evidence; it
  cannot hide a nonzero native failure. `unknown` is never accepted as PASS.
- Execution kinds are explicit: process Checks (`docs`, `tach`, `tach_external`,
  `vulture`, `whitespace`) require a real native exit and evidence for
  passed/failed; internal Checks (`forwarding`, `source`) carry structured
  evidence without a process exit. Configuration failures are `not_started`,
  carry no Native Result, and cannot be reported as native failures.
- `baseline_ref` is part of the result digest when a comparison baseline exists;
  equal new Trees with different baselines produce different `content_id`s.

## Native tool requirements and observed evidence

A built-in Check may declare controlled `ToolRequirement` groups; a Check with
no requirements is an internal ECI analysis and carries no tool evidence:

| `check_id` | Requirement (`after`) | Declared entry | Purpose |
| --- | --- | --- | --- |
| `eci/hygiene/docs` | `inventory` | `git/ls-files` | tracked-file inventory fed to the scan |
| | `links` (`inventory`) | `lychee/docs` | link scan of that document set |
| `eci/hygiene/tach` | `module-graph` | `tach/check` | module-boundary check |
| `eci/hygiene/tach-external` | `external-modules` | `tach/check-external` | external-module check |
| `eci/hygiene/vulture` | `dead-code` | `vulture/dead-code` | dead-code scan |
| `eci/hygiene/whitespace` | `diff-hygiene` | `git/diff-check` | old→new whitespace check |
| `eci/hygiene/forwarding`, `eci/sources/reconcile` | none | — | internal analysis |

- Every requirement must be satisfied: `all-required` across groups,
  `allowed-alternative` within one group's `alternatives`.
- `after` is a real prerequisite. The satisfying result must reference the
  prerequisite `result_id` in `input_refs` (`kind=check_result`); mere presence
  is not evidence that the tool consumed it.
- A `ToolIdentity` records two independent facts: the invocation entry
  (`access_id` + `authority`) and the ToolAsset identity (`tool_id` +
  `tool_id_source`, optionally `version`/`artifact_sha256`).
  - `eci_local_entry` follows ECI's slash naming (`git/ls-files`); it is not a
    registered Runtime ToolAccess. A verified asset identity may still be
    recorded here (for example an ECI-pinned Lychee binary).
  - `runtime_tool_access` names a registered Runtime #27 ToolAccess in Runtime's
    own text (`trivy.scan`, `trivy.sbom`, ...), not ECI naming, and requires its
    ToolAsset `tool_id` with `tool_id_source="runtime_tool_asset"`.
  - A `tool_id` without a known `tool_id_source` (`runtime_tool_asset` or
    `eci_pinned_asset`) is self-reported and refused; format-only validation
    does not prove a Runtime registration.
  - A local name is never promoted to a Runtime identity by an executable, Hook
    Job or display name. An undeclared version/artifact may be recorded as
    observed evidence (for example a pinned Dagger tool).
- The current definitions (version 2) are the tool-bound ones: `docs`, `tach`,
  `tach_external`, `vulture`, `whitespace`. `forwarding`/`source` remain version
  1 because no tool requirement was added. Version 1 definitions keep the
  pre-tool rules and stay readable; a version 1 result cannot satisfy a version
  2 Check (`satisfaction_definition_version_mismatch`). Result schema 2 is
  independent of this definition version.
- Definition validation refuses unknown Check ids/versions, duplicate/unknown
  requirement ids, self references, cycles, empty or duplicate alternatives and
  invalid identities. A definition whose requirements differ from the
  registered definition cannot construct or validate a result.
- Alternative declarations are contract capability, not a production claim: no
  built-in Check currently declares more than one alternative.

A Native Result records the entry that actually ran in `tool`
(`authority`, `access_id`, optional `tool_id`/`tool_id_source`/`version`/
`artifact_sha256`, and `executable`). The record participates in
`content_id`/`result_id`, and the observed entry and asset facts must equal the
declared ones. A tool-bound Check may omit the observation (the native fact is
preserved), but then it cannot satisfy the Check; a tool-less Check must not
claim one. `direct_tool` results additionally require
`authority=runtime_tool_access` with a matching verified `tool_id`, so no
built-in Check currently accepts them.

`evaluate_check_satisfaction(definition, results, *, expected=None)` combines
the Native Results of one Check:

- `satisfied` only when every requirement has a native PASS result matching a
  declared alternative and every `after` edge is evidenced (and when `expected`
  is supplied, resolved `input_refs`, terminal/per-requirement `coverage`, and
  identity constraints hold on that **same** chosen witness tuple);
- otherwise `unsatisfied` with top-level `native_result_missing` when the
  candidate `results` set itself is empty, or per-requirement `result_missing`,
  `result_not_passed`, `prerequisite_unmet`, `prerequisite_unevidenced`, or
  Expected witness failures (`expected_inputs_uncovered`,
  `expected_coverage_unproven`) while keeping each requirement's
  `requirements[].reason` (`result_missing` when that requirement has no
  matching result);
- all supplied results must describe one composable Check instance: equal
  check ID, definition version, rule/profile/Source/baseline; a mixed bag is
  refused with `CheckContractError` (`satisfaction_check_mismatch`,
  `satisfaction_definition_version_mismatch`, `satisfaction_instance_mismatch`).
  Different run/retry instances with equal identity facts are allowed for
  deterministic `result_id` selection only; `#66-C` binds Run/Attempt/Job
  routing eligibility before supplying candidates, and cross-Run trusted reuse
  remains `#63` work;
- for a tool-less Check (`forwarding`, `source`, or historical `v1`),
  `expected=None` keeps `not_applicable/no_tool_requirements`, while an explicit
  `expected` selects a deterministic passing witness matching Expected
  constraints (supporting retries without forcing `len(results) == 1`);
- `selected_result_ids` holds ONLY the witness IDs that satisfied the complete
  Check (including `expected` when given), remaining strictly `[]` on
  `unsatisfied`; `requirements[].satisfied_by` remains local native tool
  satisfaction and does not prove Expected coverage on its own;
- `expected_failures`, `expected_failures_complete`, and deduplicated
  `failure_reasons` record every tool-valid combination (or tool-less
  attempt) rejected by Expected constraints;
- the decision always carries `consumer_scope=supplied_results_only` (or
  `supplied_expected_only` with `expected`), `required=false`, `trusted=false`.
  It is not an ExpectedSet, Evidence Admission or Required decision.

`evaluate_expected_satisfaction(results, expected)` and
`results_cover_expected(results, expected)` evaluate one resolved Expected
Check instance against a coherent candidate set:

- coherent results whose own check/definition/source/rule/baseline differs from
  the target `expected` return `status="unsatisfied"` with
  `reason="native_result_mismatched"` (whereas a heterogeneous mix inside
  `results` raises `CheckContractError`);
- tool-bound Checks (single- and multi-requirement) share
  `evaluate_check_satisfaction(..., expected=expected)`, including
  `coverage_by_requirement` and terminal requirement `coverage` checks;
- `result_covers_expected(result, expected)` shares those same single-tool,
  multi-tool, and tool-less semantics for one result without recursion.

## Public API

`src/engineering_ci/check_contract.py`:

```python
CONTRACT_VERSION = 2
SUPPORTED_RESULT_VERSIONS = frozenset({1, 2})
CHECK_CONTRACT_KIND = "engineering-ci-check-contract"
RESULT_KIND = "engineering-ci-native-check-result"
SATISFACTION_KIND = "engineering-ci-check-satisfaction"

ToolIdentity(access_id, tool_id=None, tool_id_source=None, version=None,
             artifact_sha256=None, authority="eci_local_entry")
ToolRequirement(requirement_id, alternatives, after=())
TOOL_AUTHORITIES = frozenset({"eci_local_entry", "runtime_tool_access"})
TOOL_ASSET_SOURCES = frozenset({"runtime_tool_asset", "eci_pinned_asset"})

definition_for(name) -> CheckDefinition | None   # current definition version
check_id_for(name) -> str | None
validate_definition(definition) -> CheckDefinition  # registered version + requirements
rule_ref(name, version, *, path=None, digest=None) -> dict
profile_ref(name, version) -> dict
source_ref(snapshot, *, declared_target_tree=None, actual_execution_source=None,
           head_commit=None, index_tree=None, content_digest=None,
           verified_scope=(), binding_status=None,
           scope_kind="changed_paths", materialization_ref=None) -> dict
tool_ref(access_id, *, tool_id=None, tool_id_source=None, version=None,
         artifact_sha256=None, authority="eci_local_entry",
         executable=None) -> dict  # observed evidence
native_result(definition, *, source, status, result_origin, result_instance, ..., tool=None) -> dict
validate_result(result) -> dict
validate_result_set(results) -> bool
evaluate_check_satisfaction(definition, results, *, expected=None) -> dict  # one Check (+ optional Expected)
evaluate_expected_satisfaction(results, expected) -> dict  # coherent candidates vs one resolved Expected
resolve_definitions(required=None) -> dict   # requested_names only
result_matches_expected(result, expected) -> bool  # candidate identity only
validate_expected_instance(expected) -> dict  # one supplied scoped Check, no completeness
result_covers_expected(result, expected) -> bool  # one result; unified requirement/Expected check
results_cover_expected(results, expected) -> bool  # boolean wrapper over evaluate_expected_satisfaction
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
  "schema_version": 2,
  "check": {
    "check_id": "eci/hygiene/docs",
    "definition_version": 2,
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
  "input_refs": [
    {"kind": "check_result", "result_id": "sha256:…", "path": "git/ls-files"},
    {"kind": "config", "path": "ci-hygiene.toml", "sha256": "…"}
  ],
  "status": "passed",
  "native_exit_code": 0,
  "result_origin": "host",
  "tool": {
    "access_id": "lychee/docs",
    "tool_id": null,
    "tool_id_source": null,
    "version": null,
    "artifact_sha256": null,
    "authority": "eci_local_entry",
    "executable": "lychee"
  },
  "runner_ref": "execution:host",
  "result_instance": "run-id:host:0:docs:lychee/docs",
  "artifact_refs": [],
  "coverage": null,
  "evidence": {"stdout": "", "stderr": "", "findings": []},
  "baseline_ref": null,
  "reason": null,
  "content_id": "sha256:…",
  "result_id": "sha256:…"
}
```

This is the `links` result of the real `docs` execution; the separate
`git/ls-files` result is referenced by `input_refs` and recorded under the
Check's `native_results`. `binding_status` is explicit: `byte_tree` (a
materializer verified the exact Tree and full input closure), `content_digest`
(a content digest over the listed scope), `identity_only` (commit/index/Tree
identity without byte verification), or `unknown`. `byte_tree` also requires
`scope_kind="full_input_closure"` and a `materialization_ref`; execution emits
only `identity_only` or changed-path `content_digest`.

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

`validate_expected_instance`, `result_covers_expected`, `results_cover_expected`,
and `evaluate_expected_satisfaction` remain **single-supplied-Check primitives**:
a scoped rule/source/definition/baseline can be structurally checked and an
actual native PASS tuple associated with that instance. They do **not**
establish the completeness or authenticity of the consumer's Expected
collection, input closure provenance, Run/Attempt routing qualification,
Evidence Admission or Required Gate. For a built-in comparison-dependent Check,
the supplied Expected instance must include the real baseline.
When an Expected instance explicitly declares `input_refs`, each declared
input must be represented by an identical Native Result input reference across
the selected witness tuple (for `results_cover_expected` /
`evaluate_expected_satisfaction`, on the selected successful candidates, never
completed by failed or unselected attempts). Additional real inputs are allowed;
a mismatched digest cannot be overlooked. An explicitly declared `coverage`
claim is checked against terminal requirements (or per requirement when
`coverage_by_requirement` is supplied, so an upstream `inventory` cannot stand
in for terminal `lychee/docs` link coverage). For a tool-bound Check, both
`result_covers_expected()` and `evaluate_expected_satisfaction()` route through
`evaluate_check_satisfaction()`; a multi-requirement Check can never be covered
by one result, so callers (`#66-C`) pass the instance's candidate list to
`evaluate_expected_satisfaction(results, expected)` and cite `selected_result_ids`.
These local comparisons do not prove consumer-wide Expected completeness or
trusted source provenance.

**No consumer-wide Expected/Required coverage API is produced by this #52
slice.** The earlier `validate_completeness_receipt` and
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
- `result_id` and the full `native_result` (the primary result);
- `native_results` — all Native Results of this Check. A Check whose declared
  chain has several tools (currently `docs`) emits one result per entry, each
  with its own `result_id`; the later result references the prerequisite
  `result_id` in `input_refs`;
- `native_satisfaction` — the local decision from
  `evaluate_check_satisfaction()` over `native_results`.

A Check that reports native `passed` while its declared composition is
`unsatisfied` blocks the Report with
`blocked/native_satisfaction_incomplete`. The same block applies to a
`not_applicable` verdict whose own Native Results contain a failed tool: a
failed `git ls-files` inventory cannot be used as proof that no document is
applicable. A genuinely empty document set with a successful inventory stays
`not_applicable` and does not become a failure.

In the Host `docs` path a failed inventory with no usable target is a
pre-execution failure for the Check verdict (`git_inventory_failed`,
`execution_started=false`), but the inventory tool itself already ran: its
Native Result is kept in `native_results` with the real exit/stderr, while no
Lychee result or exit is fabricated (`check_contract=not_started` refers only
to the link-scan stage). `native_satisfaction` is `unsatisfied`, and the Report
fails. When changed documents remain, Lychee still runs and the native
satisfaction gate blocks the PASS because the inventory requirement failed.

Unknown selected names remain `not_executed` with `check_identity_missing`. The
report top-level adds `check_contract` kind/version. The Host `docs` Check now
passes the document list to Lychee as arguments; real Lychee rejects an
invocation without inputs, so the previous stdin invocation could never produce
a valid native result.

## Verification

`tests/test_check_contract.py` covers the version-1 primitives and migration
compatibility; `tests/test_native_tool_contract.py` covers the Native Tool
extension:

- the real built-in Check tool requirements, entries and definition versions;
- duplicate requirement ids, unknown prerequisites, self references, cycles,
  empty/duplicate alternatives and invalid identities are refused;
- a forged definition with different tool requirements cannot construct a
  result; unknown Check ids/versions are refused by `validate_definition`;
- undeclared tool evidence, tool identity claims without a verified declaration
  and tool evidence on a tool-less Check are refused;
- ECI-local entries may record a verified ToolAsset identity with its source;
  a `tool_id` without a known `tool_id_source` is refused. A
  `runtime_tool_access` identity accepts Runtime's registered key text (for
  example `trivy.scan`) and requires its ToolAsset id with
  `tool_id_source=runtime_tool_asset`; `direct_tool` requires that authority;
- observed tool evidence participates in `content_id`/`result_id`;
- satisfaction requires every declared result, allows a declared alternative,
  and requires an evidenced prerequisite `result_id` reference;
- results from another Check are refused, a tool-less Check is
  `not_applicable`, and the decision never claims Required/trusted;
- `result_covers_expected()` and `evaluate_check_satisfaction()` agree for the
  same single result (bare PASS without the declared tool covers nothing;
  a valid Tool Observation covers; internal tool-less checks keep coverage);
- results of one Check from different rule/profile/Source/baseline instances
  are refused, while equal instances from different run ids are allowed;
- a schema 1 / definition version 1 result stays readable but cannot satisfy
  the version 2 Check, while it still covers its own version 1 expectation;
- one result cannot cover a multi-tool Check.

`tests/test_execution.py` additionally checks that the real `docs` path emits
`git/ls-files` and `lychee/docs` results for one `check_id` (version 2),
references the inventory `result_id` from the Lychee result, records
`satisfied` satisfaction, invokes Lychee with explicit document targets, blocks
a native PASS whose composition is unsatisfied, keeps a genuinely empty document
set `not_applicable` (not failed), and on a failed inventory with no target
keeps the real failed inventory Native Result (exit/stderr), fabricates no
Lychee result, reports `unsatisfied` and does not PASS.

The real Host `git` + Lychee execution (pass and broken-link cases, actual
exits and the resulting Report) is recorded in
[verification/native-tool-contract](../verification/native-tool-contract/README.md).

## Limits / next slices

- Execution does not emit `byte_tree`: immutable/Index snapshots are
  `identity_only`, and worktree content digests cover only changed paths. Exact
  full-input materialization remains #66 Source work.
- No verified Runtime ToolAsset identity exists for the Host `git`/Lychee/
  Vulture/Tach tools, so their results keep `authority=eci_local_entry`,
  `tool_id=null` and `tool_id_source=null`. The `runtime_tool_access` authority
  and `tool_id_source` mark where a registered Runtime #27 ToolAccess/ToolAsset
  may be used; this contract does not verify that registration, and no adapter
  supplies one yet.
- Definition version 1 remains the pre-tool contract for the five tool-bound
  Checks. Consumers must select the version they intend: a version 1 result or
  expectation never satisfies a version 2 Check. No second version catalogue is
  created; the controlled table lives in `check_contract`.
- Dagger `execute-plan` reuses the Host function and therefore emits this
  contract, but `dagger_export` maps to `result_origin=unknown` and the Dagger
  tool pins (`tach`, `lychee`, `weaver`) are not recorded as observations. The
  separate Dagger `tach-check`/`lychee-check` functions still return their own
  ad-hoc JSON. Three-entry-point parity is not claimed.
- The `all-required` relation is real only for `docs` (`git` + Lychee). No
  production `allowed-alternative` instance exists yet; the alternative path is
  covered by controlled contract tests, and `after` edges are declared only
  where a real consumption is wired.
- Only the frozen built-in checks have IDs; external native tools and the
  #52/Runtime Check/Profile interfaces still require the later contract work.
- `profile_ref` is pass-through metadata; it cannot grant Required/trusted.
- `resolve_definitions` resolves only the built-in scope; Expected Check
  enumeration, Job routing and Required aggregation remain #66/#63/#61 work.
- `result_matches_expected` is candidate identity association, not a
  satisfaction, trusted coverage, applicability or Required decision.
- If a native Check ran but its Result contract is invalid, its original
  native status and diagnostics remain in the Report; `execution.execute()`
  blocks with `check_contract_invalid`. Configuration/pre-tool failures,
  including a missing Tach config, are `not_started` without a Native Result
  and preserve the original failure reason; a prerequisite tool that already ran
  (the `docs` inventory) keeps its real Native Result even when the Check's own
  verdict process never started.

