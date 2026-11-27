# /// script
# requires-python = ">=3.11"
# dependencies = ["jsonschema==4.26.0"]
# ///
"""Opt-in review experiment consumer; not an ECI check or approval gate."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

VIEWS = {"specification": "S", "structure": "T", "behavior": "B", "verification": "V"}
POLICY = Path(__file__).resolve().parents[2] / "architecture"


def read_json(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(result, schema, policy, subject, head, base, views, task_kind):
    """Check result against independently selected scope and exact policy bytes."""
    errors = [
        f"{error.json_path}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(result)
    ]
    if errors:
        return errors
    actual = result["baseline"]
    if (actual["subject"], actual["head"], actual["base"]) != (subject, head, base):
        errors.append("baseline differs from actual request")
    if actual["task_kind"] != task_kind:
        errors.append("task kind differs from actual request")
    if result["policy"] != policy:
        errors.append("policy digest differs from loaded policy")
    if set(result["requested_viewpoints"]) != views:
        errors.append("requested viewpoints differ from actual request")
    active = set(result["centers"])
    complete = result["result_kind"] == "complete_request"
    if not active <= views or (complete and active != views):
        errors.append("center coverage differs from requested scope")

    def index(records, label):
        by_id = {}
        for record in records:
            key = record["id"]
            if key in by_id:
                errors.append(f"duplicate {label} id: {key}")
            by_id[key] = record
        return by_id

    evidence = index(result["evidence"], "evidence")
    findings = index(result["findings"], "finding")
    cited_findings = set()
    incomplete = []
    active_rules = set()

    def check_refs(refs, registry, label):
        for key in set(refs) - registry.keys():
            errors.append(f"{label}: unresolved reference {key}")

    for view, rules in result["centers"].items():
        expected = {f"ECI-{VIEWS[view]}{i:02}" for i in range(1, 9)}
        if complete and rules.keys() != expected:
            errors.append(f"{view}: missing rule families")
        for key, rule in rules.items():
            active_rules.add(key)
            check_refs(rule["evidence_refs"], evidence, key)
            if rule["coverage"] not in {"reviewed", "not_applicable"} or rule[
                "discovery"
            ] not in {"bounded_complete", "not_applicable"}:
                incomplete.append(key)
            for check in rule["checks"]:
                check_refs(check["evidence_refs"], evidence, key)
                check_refs(check["finding_refs"], findings, key)
                cited_findings.update(check["finding_refs"])
                if check["assessment"] == "violated" and not any(
                    findings.get(f, {}).get("status") in {"blocking", "nonblocking"}
                    for f in check["finding_refs"]
                ):
                    errors.append(f"{key}: violation requires an open finding")
                if check["assessment"] == "unknown":
                    incomplete.append(key)
    for item in result["unresolved"]:
        if item["rule_id"] not in active_rules:
            errors.append(f"unresolved item outside reported rules: {item['rule_id']}")
    for key, finding in findings.items():
        check_refs(finding["evidence_refs"], evidence, key)
        if key not in cited_findings:
            errors.append(f"finding not linked to a rule check: {key}")
    if result["disposition"]["recommendation"] == "accept" and (
        not complete or incomplete or result["unresolved"]
        or any(f["status"] == "blocking" for f in findings.values())
    ):
        errors.append("accept requires complete requested coverage without blockers")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("--subject", required=True, help="actual target, from review host")
    parser.add_argument("--head", required=True, help="actual target revision")
    parser.add_argument("--base", help="actual base revision for PR review")
    parser.add_argument("--viewpoints", nargs="+", choices=VIEWS, default=list(VIEWS))
    parser.add_argument("--task-kind", required=True, choices=["design", "implementation", "acceptance", "audit", "re_review"])
    args = parser.parse_args()
    try:
        schema_path = POLICY / "eci-review-result.schema.json"
        schema = read_json(schema_path)
        Draft202012Validator.check_schema(schema)
        policy = {
            "requirements_sha256": digest(POLICY / "eci-review-requirements.md"),
            "schema_sha256": digest(schema_path),
        }
        result = read_json(args.result)
        errors = validate(result, schema, policy, args.subject, args.head, args.base, set(args.viewpoints), args.task_kind)
    except (OSError, ValueError) as error:
        print(f"review validation error: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"valid": not errors, "policy": policy, "errors": errors,
                      "scope": "shape, request binding, local references and declared rule coverage only"}, ensure_ascii=False))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
