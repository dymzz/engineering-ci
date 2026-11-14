# Expected Checks (#66-B first slice)

Status: additive Plan schema 2 field; **partial** by design until an
authoritative Consumer Profile/rule source is supplied. This is not a Required
policy engine and does not create Job routing.

## Position

`selection.plan()` derives `expected_checks` from the existing
`ChangeSet → select() → plan()` inputs, after Selection and before any Job,
native Check or Result exists. The field is scheduler-independent: it contains
no `job_refs`/scheduler activation.

## Shape

```json
{
  "kind": "engineering-ci-expected-checks",
  "schema_version": 1,
  "status": "partial",
  "reason": "consumer_profile_not_resolved",
  "scope_id": "sha256:…",
  "expected_set_ref": "sha256:…",
  "definitions_status": "requested_names_complete",
  "applicability_status": "requested_scope",
  "consumer": null,
  "profile_ref": null,
  "profile_source": null,
  "consumer_completeness": "not_claimed",
  "event_ref": {"base_ref": "…", "head_ref": "…", "base_commit": "…", "head_commit": "…", "references": []},
  "source_ref": {"snapshot": "worktree", "declared_target_tree": null, "actual_execution_source": "worktree", "verified_input_scope": [], "scope_kind": "changed_paths", "binding_status": "unknown"},
  "checks": [
    {
      "expected_check_ref": "sha256:…",
      "check_id": "eci/hygiene/docs",
      "definition_version": 1,
      "definition_status": "resolved",
      "name": "docs",
      "event_ref": {"base_ref": "…", "head_ref": "…", "base_commit": "…", "head_commit": "…", "references": []},
      "selection_rule_ref": {"name": "engineering-ci.selection", "version": "1", "sha256": "…"},
      "rule_ref": null,
      "rule_status": "unknown",
      "profile_ref": null,
      "applicability": {"status": "selected", "reason": "changed documents"},
      "policy_status": "unknown",
      "source_ref": {"snapshot": "worktree", "declared_target_tree": null, "actual_execution_source": "worktree", "verified_input_scope": [], "scope_kind": "changed_paths", "binding_status": "unknown"},
      "input_refs": [{"kind": "changed_path", "path": "docs/guide.md"}],
      "baseline_ref": null
    }
  ],
  "missing": [],
  "conflicts": []
}
```

- `expected_check_ref` binds Event/Ref (including normalized multi-ref), Check
  definition, Source identity, selection rule, applicability and input
  constraints. Local Index/config paths are excluded.
- `scope_id` binds consumer/Profile/Event/Source **and the selection rule
  identity**, so a rule change cannot keep the same scope. The rule SHA must
  match Plan `generator.config_sha256`; a merely self-consistent tamper is
  rejected.
- Resolved entries are checked against the published `check_contract`
  name↔ID↔historical definition-version mapping; unknown entries must match
  `missing[]`. Historical v1 remains readable; it does not satisfy a newer v2.
- For `projection_source=check_scope`, applicability/reason/input refs are
  cross-checked with Plan `check_scope`; changed-path scope is cross-checked
  with `git.changed_paths` and `content_binding.paths`. Single-Ref baseline must
  equal `git.base_commit`. These checks never raise the claim to a full input
  closure.
- `source_ref.actual_execution_source` is `unknown` before a Native execution;
  Expected only declares the selection target, not observed native reads.
- `expected_set_ref` binds the declared Check-domain set; it is not the full
  Required Obligations identity.
- `scope_id` binds consumer/Profile/event/source scope.
- `selection_rule_ref` is only the Selection applicability rule; the native
  Check `rule_ref` stays `null`/`rule_status=unknown` until a real native rule
  source is resolved. They are never conflated.
- `source_ref` uses the #52 Source proof fields (`verified_input_scope`,
  `scope_kind`, `binding_status`); this slice claims at most `identity_only` or
  `unknown`, never a full input closure.
- `status` is only `partial` or `unknown`; Schema v1 rejects `complete` and
  `consumer_completeness != not_claimed` at the public Plan validator. An empty
  selection is `unknown`.
- Worktree Source identity includes the existing changed-path `content_digest`;
  same HEAD/paths but different file bytes changes Expected identity. This is
  not a full Check input closure.
- `policy_status=unknown`; Required/Advisory is not inferred here.

## Validation

`plan_contract.validate()` accepts the optional field only on Plan schema 2 and
rejects:

- unknown Expected schema/status;
- malformed `scope_id`/`expected_set_ref`/`expected_check_ref`;
- recomputed `expected_check_ref`, `expected_set_ref`, `scope_id` mismatches;
- duplicate expected identity;
- Entry Event/Source/Profile inconsistent with parent scope;
- Index target not equal to the effective Index tree (HEAD Tree is not used);
- Worktree content digest inconsistent with Plan `git.content_binding`;
- `complete` or unknown consumer-completeness claims without authority;
- `job_refs`/`job_ref` in Expected;
- source snapshot/tree or event/ref mismatch with Plan Git;
- missing application/policy fields.

## Limits

- No Consumer Profile/ExpectedSet production or Required Policy.
- No Routing Binding / Job activation; that is #66-C.
- No Native Result coverage/reuse; #52/#63/#61 remain responsible.
- Unknown Check definitions stay `definition_status=unknown` and `missing[]`;
  they are not silently dropped or converted to complete.
