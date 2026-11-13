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
  "event_ref": {"base_ref": "…", "head_ref": "…", "base_commit": "…", "head_commit": "…"},
  "source_ref": {"kind": "git", "snapshot": "worktree", "tree": null, "changed_paths": []},
  "checks": [
    {
      "expected_check_ref": "sha256:…",
      "check_id": "eci/hygiene/docs",
      "definition_version": 1,
      "definition_status": "resolved",
      "name": "docs",
      "rule_ref": {"name": "engineering-ci.selection", "version": "2", "path": "ci-selection.toml", "sha256": "…"},
      "profile_ref": null,
      "applicability": {"status": "selected", "reason": "changed documents"},
      "policy_status": "unknown",
      "source_ref": {"kind": "git", "snapshot": "worktree", "tree": null, "changed_paths": []},
      "input_refs": [{"kind": "changed_path", "path": "docs/guide.md"}],
      "baseline_ref": null
    }
  ],
  "missing": [],
  "conflicts": []
}
```

- `expected_check_ref` is the content identity of one Expected instance.
- `expected_set_ref` binds the declared Check-domain set; it is not the full
  Required Obligations identity.
- `scope_id` binds consumer/Profile/event/source scope.
- `status=partial` because no authoritative Profile/rules receipt was supplied
  (`consumer_completeness=not_claimed`).
- An empty selection is `status=unknown`, never a legal complete empty set.
- `policy_status=unknown`; Required/Advisory is not inferred here.

## Validation

`plan_contract.validate()` accepts the optional field only on Plan schema 2 and
rejects:

- unknown Expected schema/status;
- malformed `scope_id`/`expected_set_ref`/`expected_check_ref`;
- duplicate expected identity;
- `job_refs`/`job_ref` in Expected;
- source snapshot mismatch with Plan `git.snapshot`;
- missing application/policy fields.

## Limits

- No Consumer Profile/ExpectedSet production or Required Policy.
- No Routing Binding / Job activation; that is #66-C.
- No Native Result coverage/reuse; #52/#63/#61 remain responsible.
- Unknown Check definitions stay `definition_status=unknown` and `missing[]`;
  they are not silently dropped or converted to complete.
