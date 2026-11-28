"""Small stdlib helpers shipped with one pytest result bundle, not a runner."""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

MAX_XML_BYTES = 2 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_SUMMARY_BYTES = 24 * 1024
# Inventory and ReportLog observations supply native identities for JUnit.
_INVOCATION_LIMITS = {
    "metadata.json": MAX_JSON_BYTES,
    "summary.json": 4 * MAX_JSON_BYTES,
    "pytest-reportlog.jsonl": 32 * MAX_JSON_BYTES,
    "junit.xml": MAX_XML_BYTES,
}
_PUBLIC_PRESENTATION = "public_pytest_diagnostics_without_captured_text"
_PUBLIC_NODE_SCOPE = "invocation_projection"
_XML_UNSAFE_NAME = re.compile(
    r"[^\t\n\r\x20-\x7e\x80-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]"
)
_NATIVE_OUTCOMES = {"passed", "failed", "skipped"}


def read_invocation(directory: Path) -> dict:
    files = []
    status = {}
    for name, limit in _INVOCATION_LIMITS.items():
        path = directory / name
        try:
            if directory.is_symlink() or path.is_symlink() or not path.is_file():
                status[name] = "unavailable"
            elif not 0 < path.stat().st_size <= limit:
                status[name] = "outside_delivery_bounds"
            else:
                files.append(name)
                status[name] = "available"
        except OSError:
            status[name] = "unreadable"
    return {"files": files, "file_status": status, "scope": "same_pytest_container"}


class _PublicNodes:
    """Maintain one set of opaque test-instance aliases for the public bundle."""

    def __init__(self):
        self.node_ids: dict[str, str] = {}
        self.junit_names: dict[tuple[str, str], str | None] = {}
        self.unmapped: dict[tuple[str, str], str] = {}
        self.parameter_count = 0

    def nodeid(self, raw: str) -> str:
        if raw in self.node_ids:
            return self.node_ids[raw]
        stem, bracket, parameter = raw.partition("[")
        alias = ""
        if bracket:
            self.parameter_count += 1
            alias = f"[case-{self.parameter_count}]"
        self.node_ids[raw] = stem + alias

        # Match pytest's JUnit classname/name encoding, including visual escapes
        # in the name. The hosted publisher deliberately runs with stdlib only.
        parts = stem.split("::")
        parts[0] = parts[0].replace("/", ".").removesuffix(".py")
        classname = ".".join(parts[:-1])
        name = _XML_UNSAFE_NAME.sub(
            lambda match: f"#x{ord(match[0]):02X}", parts[-1] + bracket + parameter
        )
        key = (classname, name)
        projected = parts[-1] + alias
        if self.junit_names.setdefault(key, projected) != projected:
            self.junit_names[key] = None
        return self.node_ids[raw]

    def inventory(self, value):
        if not isinstance(value, list) or not all(
            isinstance(node, str) for node in value
        ):
            return None
        return [self.nodeid(node) for node in value]

    def junit_case(self, classname: str, name: str) -> dict[str, str]:
        key = (classname, name)
        projected = self.junit_names.get(key)
        if projected is not None:
            return {"classname": classname, "name": projected}
        # JUnit permits overridden attributes. A case without a matching native
        # node still contributes its outcome and duration without exposing them.
        alias = self.unmapped.setdefault(
            key, f"[unmapped-case-{len(self.unmapped) + 1}]"
        )
        return {"name": alias}


def _public_inventory(record: dict, nodes: _PublicNodes, fields, node_fields) -> dict:
    return {key: record[key] for key in fields if key in record} | {
        key: nodes.inventory(record[key]) for key in node_fields if key in record
    }


def _public_phase_counts(counts: dict) -> dict:
    public = {}
    for phase in ("setup", "call", "teardown"):
        observed = counts.get(phase)
        if isinstance(observed, dict):
            projected = {}
            for outcome, count in observed.items():
                if type(count) is int and count >= 0:
                    key = outcome if outcome in _NATIVE_OUTCOMES else "other"
                    projected[key] = projected.get(key, 0) + count
            public[phase] = projected
    return public


def _public_invocation_record(record: dict, nodes: _PublicNodes) -> dict:
    fields = (
        "kind",
        "schema_version",
        "phase",
        "invocation_id",
        "collect_only",
        "run_mode",
        "pytest_version",
        "python_version",
        "platform",
        "plugin_versions",
        "wall_time_seconds",
        "selected_items",
        "deselected_items",
        "session_finished",
        "report_sealed",
        "exitstatus",
        "exitstatus_scope",
        "session_exitstatus",
    )
    public = {name: record[name] for name in fields if name in record}
    public["presentation"] = _PUBLIC_PRESENTATION
    public["node_id_scope"] = _PUBLIC_NODE_SCOPE
    if record.get("artifact_path_base") == "invocation_directory":
        public["artifact_path_base"] = "invocation_directory"
        for field, name in (
            ("report_log", "pytest-reportlog.jsonl"),
            ("junit_xml", "junit.xml"),
        ):
            if record.get(field) == name:
                public[field] = name
    selection = record.get("selection")
    if isinstance(selection, dict):
        public["selection"] = _public_inventory(
            selection,
            nodes,
            (
                "mode",
                "selected_items",
                "deselected_items",
                "collected_items",
                "complete",
                "deselection_observed",
                "unknown_reasons",
            ),
            ("selected_node_ids", "deselected_node_ids"),
        )
        workers = selection.get("worker_inventories")
        if isinstance(workers, dict):
            public["selection"]["worker_inventories"] = {
                f"worker-{index}": {
                    "count": inventory.get("count"),
                    "node_ids": nodes.inventory(inventory.get("node_ids")),
                }
                for index, inventory in enumerate(workers.values(), start=1)
                if isinstance(inventory, dict)
            }
    execution = record.get("execution")
    if isinstance(execution, dict):
        public["execution"] = _public_inventory(
            execution,
            nodes,
            (
                "mode",
                "status",
                "complete",
                "started_items",
                "completed_items",
                "unknown_reasons",
            ),
            (
                "started_node_ids",
                "completed_node_ids",
                "not_started_node_ids",
                "incomplete_node_ids",
                "duplicate_node_ids",
            ),
        )
        phases = execution.get("phase_counts")
        if isinstance(phases, dict):
            public["execution"]["phase_counts"] = _public_phase_counts(phases)
    collection = record.get("collection") or {}
    public["collection"] = {"complete": collection.get("complete")}
    native = record.get("native_exit_observation") or {}
    public["native_exit_observation"] = {
        key: native[key]
        for key in (
            "status",
            "source",
            "stage_exitstatus",
            "session_exitstatus",
            "outer_process_exit",
        )
        if key in native
    }
    for name in ("sessionfinish_hook_exception", "writer_exception"):
        if record.get(name) is not None:
            public[name] = {"observed": True}
    delivery = record.get("report_delivery") or {}
    artifacts = delivery.get("artifacts") or {}
    public["report_delivery"] = {
        "status": delivery.get("status"),
        "summary_seal": delivery.get("summary_seal"),
        "content_completeness": "unverified",
        "artifacts": {
            name: {"attribution": details.get("attribution")}
            for name, details in artifacts.items()
        },
    }
    return public


def _public_junit(source: Path, target: Path, nodes: _PublicNodes) -> None:
    root = ET.parse(source).getroot()
    structural = {"testsuites", "testsuite", "testcase", "failure", "error", "skipped"}
    if root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("Invalid public JUnit source")
    for element in root.iter():
        for child in list(element):
            if child.tag not in structural:
                element.remove(child)
        attributes = {}
        if (
            element.tag in {"testsuites", "testsuite", "testcase"}
            and "time" in element.attrib
        ):
            attributes["time"] = element.attrib["time"]
        if element.tag in {"testsuites", "testsuite"}:
            attributes.update(
                {
                    key: element.attrib[key]
                    for key in ("tests", "failures", "errors", "skipped", "disabled")
                    if key in element.attrib
                }
            )
        if element.tag == "testcase":
            attributes.update(
                nodes.junit_case(element.get("classname", ""), element.get("name", ""))
            )
        element.attrib = attributes
        element.text = element.tail = None
    ET.ElementTree(root).write(target, encoding="utf-8", xml_declaration=True)


def publish_public_diagnostics(source: Path, output: Path, summary: Path) -> None:
    """Copy only diagnostic facts to the hosted artifact; keep raw evidence in the bundle."""
    code = execution_exit_code(source)
    report_source = source / "report.json"
    if report_source.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("Pytest report exceeds the public diagnostic limit")
    report = json.loads(report_source.read_text(encoding="utf-8"))
    if report.get("schema_version") != 1 or report.get("exit_code") != code:
        raise ValueError("Report does not match the original pytest execution")
    invocation = source / "pytest-invocation"
    delivered = read_invocation(invocation)
    native_counts = report.get("counts") or {}
    counts = {
        key: native_counts[key]
        for key in ("tests", "passed", "failures", "errors", "skipped")
        if type(native_counts.get(key)) is int and native_counts[key] >= 0
    }
    output.mkdir()
    (output / "exit-code.txt").write_text(f"{code}\n", encoding="ascii")
    public_report = {
        "schema_version": 1,
        "presentation": _PUBLIC_PRESENTATION,
        "node_id_scope": _PUBLIC_NODE_SCOPE,
        "exit_code": code,
        "counts": counts or None,
        "report_available": report.get("report_available") is True,
        "invocation_delivery": delivered,
    }
    (output / "report.json").write_text(
        json.dumps(public_report) + "\n", encoding="utf-8"
    )
    if delivered["files"]:
        public_dir = output / "pytest-invocation"
        public_dir.mkdir()
        nodes = _PublicNodes()
        for name in delivered["files"]:
            original, target = invocation / name, public_dir / name
            if name.endswith(".json"):
                record = json.loads(original.read_text(encoding="utf-8"))
                target.write_text(
                    json.dumps(_public_invocation_record(record, nodes)) + "\n",
                    encoding="utf-8",
                )
            elif name.endswith(".jsonl"):
                fields = (
                    "$report_type",
                    "when",
                    "duration",
                    "exitstatus",
                    "pytest_version",
                )
                with (
                    original.open(encoding="utf-8") as entries,
                    target.open("w", encoding="utf-8") as stream,
                ):
                    for line in entries:
                        event = json.loads(line)
                        public_event = {
                            key: event[key] for key in fields if key in event
                        }
                        nodeid = event.get("nodeid")
                        if isinstance(nodeid, str):
                            public_event["nodeid"] = nodes.nodeid(nodeid)
                        outcome = event.get("outcome")
                        if isinstance(outcome, str):
                            public_event["outcome"] = (
                                outcome if outcome in _NATIVE_OUTCOMES else "other"
                            )
                        if event.get("wasxfail"):
                            public_event["wasxfail"] = True
                        stream.write(json.dumps(public_event) + "\n")
            else:
                _public_junit(original, target, nodes)
    with summary.open("a", encoding="utf-8") as stream:
        stream.write(f"### Core pytest\n\nOriginal pytest exit code: `{code}`\n\n")
        for name, value in counts.items():
            stream.write(f"- {name}: {value}\n")


def read_xml(path: Path) -> dict:
    if not path.is_file():
        return {"xml": "", "diagnostic": "pytest did not produce a JUnit XML report"}
    try:
        with path.open("rb") as stream:
            body = stream.read(MAX_XML_BYTES + 1)
        if len(body) > MAX_XML_BYTES:
            return {"xml": "", "diagnostic": "JUnit XML exceeds the 2 MiB report limit"}
        return {"xml": body.decode("utf-8"), "diagnostic": None}
    except (OSError, UnicodeError) as exc:
        return {
            "xml": "",
            "diagnostic": f"Cannot read JUnit XML ({type(exc).__name__})",
        }


def discovered_checks(listing: str) -> dict[str, str]:
    """Keep stable check identities and explicit native CLI selectors."""
    checks = {}
    for line in listing.splitlines():
        token = line.split()[0] if line.split() else ""
        modern = re.fullmatch(
            r"\s*--([a-zA-Z0-9_.-]+)\s+--check=([a-zA-Z0-9_.-]+)(?:\s+#.*)?\s*", line
        )
        if modern:
            module, name = modern.groups()
            identity = f"{module}:{name}"
            selector = f"dag+check://?module={module}&check={name}"
        elif re.fullmatch(r"[a-zA-Z0-9_.-]+:[a-zA-Z0-9_.:/-]+", token):
            identity = selector = token
        elif token.startswith("--"):
            raise ValueError("Invalid Dagger check listing")
        else:
            continue
        if identity in checks and checks[identity] != selector:
            raise ValueError("Conflicting Dagger check selectors")
        checks[identity] = selector
    return checks


def remaining_checks(listing: str, replaced: str) -> list[str]:
    names = discovered_checks(listing)
    if replaced not in names:
        raise ValueError(
            f"Configured pytest check is not in dagger check -l: {replaced}"
        )
    return [name for name in names if name != replaced]


def execution_exit_code(directory: Path) -> int:
    body = (directory / "exit-code.txt").read_text(encoding="ascii")
    if not re.fullmatch(r"\d{1,3}\n?", body) or not 0 <= int(body) <= 255:
        raise ValueError("Invalid or missing original pytest exit code")
    return int(body)


def publish_summary(directory: Path, destination: Path) -> None:
    if (directory / "matrix-policy.json").exists():
        from pytest_matrix import matrix_summary, read_json, verify_bundle

        verified = verify_bundle(directory)
        presentation = read_json(directory / "matrix.json", MAX_JSON_BYTES)
        # Presentation cannot hide a failing version or change its original exit.
        fields = (
            "requested_python",
            "actual_python",
            "status",
            "failure_category",
            "pytest_exit_code",
        )
        if (
            presentation.get("kind") != verified["kind"]
            or presentation.get("schema_version") != 1
            or presentation.get("overall_status") != verified["overall_status"]
            or [[r.get(f) for f in fields] for r in presentation["versions"]]
            != [[r.get(f) for f in fields] for r in verified["versions"]]
        ):
            raise ValueError(
                "Matrix presentation differs from original execution facts"
            )
        summary = matrix_summary(verified)
        if len(summary.encode("utf-8")) > MAX_SUMMARY_BYTES:
            raise ValueError("Matrix summary exceeds delivery limit")
        with destination.open("a", encoding="utf-8") as stream:
            stream.write(summary + "\n")
        return
    report_path, summary_path = directory / "report.json", directory / "summary.md"
    if (
        report_path.stat().st_size > MAX_JSON_BYTES
        or summary_path.stat().st_size > MAX_SUMMARY_BYTES
    ):
        raise ValueError("Pytest report exceeds delivery limits")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    code = execution_exit_code(directory)
    if report.get("schema_version") != 1 or report.get("exit_code") != code:
        raise ValueError("Report does not match the original pytest execution")
    with destination.open("a", encoding="utf-8") as stream:
        stream.write(summary_path.read_text(encoding="utf-8") + "\n")
    if not report.get("report_available"):
        raise ValueError(
            "Pytest report unavailable; see the summary diagnostic and original exit code"
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    read = sub.add_parser("read-xml")
    read.add_argument("path", type=Path)
    invocation = sub.add_parser("read-invocation")
    invocation.add_argument("directory", type=Path)
    runtime = sub.add_parser("read-runtime")
    runtime.add_argument("path", type=Path)
    coverage = sub.add_parser("read-coverage")
    coverage.add_argument("xml", type=Path)
    coverage.add_argument("json", type=Path)
    plan = sub.add_parser("plan")
    plan.add_argument("listing", type=Path)
    plan.add_argument("replaced")
    plan.add_argument("output", type=Path)
    plan.add_argument("--selectors", type=Path)
    summary = sub.add_parser("summary")
    summary.add_argument("directory", type=Path)
    summary.add_argument("destination", type=Path)
    public = sub.add_parser("publish-ci")
    public.add_argument("source", type=Path)
    public.add_argument("output", type=Path)
    public.add_argument("summary", type=Path)
    gate = sub.add_parser("gate")
    gate.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "read-xml":
            print(json.dumps(read_xml(args.path), ensure_ascii=False))
        elif args.command == "read-invocation":
            print(json.dumps(read_invocation(args.directory)))
        elif args.command == "read-runtime":
            try:
                with args.path.open("rb") as stream:
                    content = stream.read(8193)
                evidence = json.loads(content) if len(content) <= 8192 else {}
                if not isinstance(evidence, dict):
                    evidence = {}
            except (OSError, ValueError, UnicodeError):
                evidence = {}
            print(json.dumps(evidence))
        elif args.command == "read-coverage":
            from pytest_quality import read_coverage

            print(json.dumps(read_coverage(args.xml, args.json), ensure_ascii=False))
        elif args.command == "plan":
            listing = args.listing.read_text(encoding="utf-8")
            names = remaining_checks(listing, args.replaced)
            args.output.write_text(
                "".join(name + "\n" for name in names), encoding="utf-8"
            )
            if args.selectors:
                selectors = discovered_checks(listing)
                args.selectors.write_text(
                    "".join(selectors[name] + "\n" for name in names), encoding="utf-8"
                )
        elif args.command == "summary":
            publish_summary(args.directory, args.destination)
        elif args.command == "publish-ci":
            publish_public_diagnostics(args.source, args.output, args.summary)
        elif args.command == "gate":
            if (args.directory / "matrix-policy.json").exists():
                from pytest_matrix import verify_bundle

                result = verify_bundle(args.directory)
                if result["overall_status"] != "passed":
                    for row in result["versions"]:
                        if row["status"] != "passed":
                            print(
                                f"Python {row['requested_python']}: {row['failure_category']}; "
                                f"pytest exit {row['pytest_exit_code']}",
                                file=sys.stderr,
                            )
                    return 1
                return 0
            code = execution_exit_code(args.directory)
            print(f"Original pytest exit code: {code}")
            policy_path = args.directory / "quality-policy.json"
            if policy_path.exists():
                from pytest_quality import (
                    evaluate_quality,
                    quality_summary,
                    read_coverage,
                )

                if policy_path.stat().st_size > 4096:
                    raise ValueError("Quality policy exceeds delivery limits")
                policy = json.loads(policy_path.read_text(encoding="utf-8"))
                if policy.get("schema_version") != 1:
                    raise ValueError("Unsupported quality policy schema")
                evidence = read_coverage(
                    args.directory / "coverage.xml", args.directory / "coverage.json"
                )
                quality = evaluate_quality(policy, code, evidence)
                print(quality_summary(quality))
                # Recompute from raw coverage counts and explicit policy, never report.json.
                return code if code else 0 if quality["gate"]["passed"] else 1
            # Neither JSON presentation nor summary/artifact delivery can change this.
            return code
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Pytest report delivery error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
