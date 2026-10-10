"""Small stdlib helpers shipped with one pytest result bundle, not a runner."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MAX_XML_BYTES = 2 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_SUMMARY_BYTES = 24 * 1024


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
        return {"xml": "", "diagnostic": f"Cannot read JUnit XML ({type(exc).__name__})"}


def remaining_checks(listing: str, replaced: str) -> list[str]:
    # Dagger 0.21.10 `check -l` prints Name/Description rows; module-qualified
    # names are also the positional selectors accepted by `dagger check`.
    names = []
    for line in listing.splitlines():
        token = line.split()[0] if line.split() else ""
        if re.fullmatch(r"[a-zA-Z0-9_.-]+:[a-zA-Z0-9_.:/-]+", token):
            if token not in names:
                names.append(token)
    if replaced not in names:
        raise ValueError(f"Configured pytest check is not in dagger check -l: {replaced}")
    return [name for name in names if name != replaced]


def execution_exit_code(directory: Path) -> int:
    body = (directory / "exit-code.txt").read_text(encoding="ascii")
    if not re.fullmatch(r"\d{1,3}\n?", body) or not 0 <= int(body) <= 255:
        raise ValueError("Invalid or missing original pytest exit code")
    return int(body)


def publish_summary(directory: Path, destination: Path) -> None:
    report_path, summary_path = directory / "report.json", directory / "summary.md"
    if report_path.stat().st_size > MAX_JSON_BYTES or summary_path.stat().st_size > MAX_SUMMARY_BYTES:
        raise ValueError("Pytest report exceeds delivery limits")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    code = execution_exit_code(directory)
    if report.get("schema_version") != 1 or report.get("exit_code") != code:
        raise ValueError("Report does not match the original pytest execution")
    with destination.open("a", encoding="utf-8") as stream:
        stream.write(summary_path.read_text(encoding="utf-8") + "\n")
    if not report.get("report_available"):
        raise ValueError("Pytest report unavailable; see the summary diagnostic and original exit code")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    read = sub.add_parser("read-xml")
    read.add_argument("path", type=Path)
    coverage = sub.add_parser("read-coverage")
    coverage.add_argument("xml", type=Path)
    coverage.add_argument("json", type=Path)
    plan = sub.add_parser("plan")
    plan.add_argument("listing", type=Path)
    plan.add_argument("replaced")
    plan.add_argument("output", type=Path)
    summary = sub.add_parser("summary")
    summary.add_argument("directory", type=Path)
    summary.add_argument("destination", type=Path)
    gate = sub.add_parser("gate")
    gate.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "read-xml":
            print(json.dumps(read_xml(args.path), ensure_ascii=False))
        elif args.command == "read-coverage":
            from pytest_quality import read_coverage
            print(json.dumps(read_coverage(args.xml, args.json), ensure_ascii=False))
        elif args.command == "plan":
            names = remaining_checks(args.listing.read_text(encoding="utf-8"), args.replaced)
            args.output.write_text("".join(name + "\n" for name in names), encoding="utf-8")
        elif args.command == "summary":
            publish_summary(args.directory, args.destination)
        elif args.command == "gate":
            code = execution_exit_code(args.directory)
            print(f"Original pytest exit code: {code}")
            policy_path = args.directory / "quality-policy.json"
            if policy_path.exists():
                from pytest_quality import evaluate_quality, quality_summary, read_coverage
                if policy_path.stat().st_size > 4096:
                    raise ValueError("Quality policy exceeds delivery limits")
                policy = json.loads(policy_path.read_text(encoding="utf-8"))
                if policy.get("schema_version") != 1:
                    raise ValueError("Unsupported quality policy schema")
                evidence = read_coverage(args.directory / "coverage.xml", args.directory / "coverage.json")
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
