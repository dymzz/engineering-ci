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
- `projection_source` is `check_scope` for Schema v1 and `effective_profile` for
  Schema v2. Free-form or unverified profile sources are rejected.
- CheckScope projection (Schema v1) is bidirectional: every source entry appears
  exactly once in Expected, and no Expected entry may come from outside the
  declared scope. Removed selected entries are rejected.
- EffectiveProfile projection (Schema v2) binds an authoritative
  `effective_profile` (`profile_ref`, `profile_revision`, `approval_source`,
  `approval_binding`, `effective_at`, `evaluated_at`, `domain_complete`,
  `authority_verified`, `declared_checks`, `required_obligations`,
  `advisory_checks`, `profile_sha256`) and cross-binds it to Plan
  `inputs.effective_profile` and `generator.effective_profile_sha256`.
- Schema v2 models M:N check obligations (`obligation_refs`,
  `required_by_obligations`, `advisory_for_obligations`), per-obligation
  inapplicability waivers (`allow_inapplicable` evaluated per
  `(obligation_ref, check_name)`), non-check obligations
  (`non_check_obligations`, including `ref_deletion`), and per-ref projections
  (`per_ref[]`) whose check applicability, inputs, conflicts, and status are
  independently recomputed and verified against `git.references` and the bound
  Hygiene/Selection rules.
- For `projection_source=check_scope`, applicability/reason/input refs are
  cross-checked with Plan `check_scope`; changed-path scope is cross-checked
  with `git.changed_paths` and `content_binding.paths`. Single-Ref baseline must
  equal `git.base_commit`. These checks never raise the claim to a full input
  closure.
- `source_ref.actual_execution_source` is `unknown` before a Native execution;
  Expected only declares the selection target, not observed native reads.
- `expected_set_ref` binds the declared Check-domain set (and
  `non_check_obligations` in Schema v2).
- `scope_id` binds consumer/Profile/event/source/rule scope (including
  `profile_sha256` and `evaluated_at` in Schema v2).
- `selection_rule_ref` is only the Selection applicability rule; the native
  Check `rule_ref` stays `null`/`rule_status=unknown` in Schema v1 until a real
  native rule source (`hygiene_rule_ref`, `sources_rule_ref`,
  `selection_rule_ref`, or obligation `rule_ref`) is resolved in Schema v2.
- `source_ref` uses the #52 Source proof fields (`verified_input_scope`,
  `scope_kind`, `binding_status`); this slice claims at most `identity_only` or
  `unknown`, never a full input closure.
- Schema v1 `status` is only `partial` or `unknown` and rejects `complete` and
  `consumer_completeness != not_claimed`. Schema v2 allows `status=complete`
  only when `effective_profile.domain_complete` is true, `declared_checks`
  covers the configured check domain, all check definitions and rules are
  resolved, applicability is not `unknown`/`incomplete`, `conflicts` is empty,
  and all `per_ref` projections are `complete`.
- Worktree Source identity includes the existing changed-path `content_digest`;
  same HEAD/paths but different file bytes changes Expected identity. This is
  not a full Check input closure.
- Schema v1 sets `policy_status=unknown`; Schema v2 resolves `policy_status` to
  `resolved`, `advisory_unmapped`, or `conflict`.

## Validation

`plan_contract.validate()` accepts the optional field only on Plan schema 2 and
rejects:

- unknown Expected schema/status;
- malformed `scope_id`/`expected_set_ref`/`expected_check_ref`;
- recomputed `expected_check_ref`, `expected_set_ref`, `scope_id` mismatches;
- tampered `effective_profile` governance fields, `approval_binding`,
  `profile_sha256`, or `inputs.effective_profile` mismatches;
- deleted `declared_checks` or omitted selected `check_scope` entries;
- unwaived `not_applicable` required obligations or unrecorded conflicts;
- tampered `per_ref` check applicability, inputs, conflicts, or status;
- duplicate expected identity;
- Entry Event/Source/Profile inconsistent with parent scope;
- Index target not equal to the effective Index tree (HEAD Tree is not used);
- Worktree content digest inconsistent with Plan `git.content_binding`;
- `complete` or unknown consumer-completeness claims without authority;
- `job_refs`/`job_ref` in Expected;
- source snapshot/tree or event/ref mismatch with Plan Git;
- missing application/policy fields.

## Limits

- No Routing Binding / Job activation; that is #66-C.
- No Native Result coverage/reuse; #52/#63/#61 remain responsible.
- Unknown Check definitions stay `definition_status=unknown` and `missing[]`;
  they are not silently dropped or converted to complete.
