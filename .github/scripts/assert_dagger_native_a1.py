"""Independent CI acceptance of Issue #87 A.1's real native OTLP captures.

This asserts the *actual original* receiver payloads, not the recorder exit
code or its name-matched receipt alone. No Dagger check is rerun here.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def raw_spans(path: Path):
    payload = load(path)
    nodes = {}
    for resource in payload["resourceSpans"]:
        attrs = {
            entry["key"]: entry["value"].get("stringValue")
            for entry in resource["resource"].get("attributes", [])
        }
        session = attrs.get("dagger.io/session.id")
        engine = attrs.get("dagger.io/engine.instance.id")
        for scope in resource["scopeSpans"]:
            for native in scope["spans"]:
                key = native["traceId"], native["spanId"]
                require(all(key) and key not in nodes, f"invalid Span identity: {key}")
                nodes[key] = {
                    "name": native["name"],
                    "parent": native.get("parentSpanId", ""),
                    "session": session,
                    "engine": engine,
                }
    require(bool(nodes), f"empty native capture {path}")
    return nodes


def verify(kind: str, folder: Path, *, native_exit: int | None) -> None:
    suffix = {"pass": "", "fail": "-negative", "artifact": "-entrypoints"}[kind]
    capture = folder / f"capture{suffix}.json"
    receipt = load(folder / f"receipt{suffix}.json")
    nodes = raw_spans(capture)
    context = receipt["binding"]
    require(context["binding_status"] == "bound", f"{kind}: not bound")
    require(context["capture_isolated"] is True, f"{kind}: caller scope not declared")
    require(receipt["claims"]["source_associated"] is True,
            f"{kind}: trace-to-invocation not correlated")
    require(receipt["source_binding"]["source_binding_status"] == "unknown",
            f"{kind}: Git Source incorrectly verified")
    require(receipt["origin_execution"]["new_tool_execution"] is None,
            f"{kind}: Function cache incorrectly promoted to Tool Origin")
    require(
        hashlib.sha256(capture.read_bytes()).hexdigest()
        == receipt["raw_trace_ref"]["sha256"],
        f"{kind}: raw capture bytes disagree with receipt",
    )
    chain = context["verified_parent_chain"]
    require(bool(chain), f"{kind}: no claimed parent chain")
    for index, item in enumerate(chain):
        key = (item["trace_id"], item["span_id"])
        require(key in nodes, f"{kind}: invented native Span {key}")
        node = nodes[key]
        require(item["name"] == node["name"], f"{kind}: name mismatch")
        require(
            item["session_id"] == node["session"] and
            item["engine_instance_id"] == node["engine"] and
            all((node["session"], node["engine"])),
            f"{kind}: wrong resource identity",
        )
        require(item["parent_span_id"] == node["parent"],
                f"{kind}: invented Parent ID")
        if index:
            prev = chain[index - 1]
            require(
                item["parent_span_id"] == prev["span_id"]
                and item["trace_id"] == prev["trace_id"]
                and item["session_id"] == prev["session_id"]
                and item["engine_instance_id"] == prev["engine_instance_id"],
                f"{kind}: broken ancestry or cross-session link",
            )
    claimed_ids = receipt["observed_trace_span_ids"]
    require(claimed_ids == [s["span_id"] for s in chain],
            f"{kind}: bound Span selection differs from checked chain")

    if native_exit is not None:
        require(chain[0]["name"] == "dag://?check=lint",
                f"{kind}: wrong native Check selector")
        require(chain[-2]["name"] == "EngineeringCi.lint",
                f"{kind}: missing Check core Function")
        require(chain[-1]["name"] == "engineering-ci:EngineeringCi.lint",
                f"{kind}: wrong module Function")
        native = load(folder / f"native-result{suffix}.json")
        actual_code = int((folder / (
            "check.exit" if kind == "pass" else "negative-check.exit"
        )).read_text().strip())
        require(actual_code == native_exit == native["exit_code"],
                f"{kind}: native exit mismatch")
        require(native["check"] == "lint", f"{kind}: wrong native result")
        require(receipt["native_check_result"] == native,
                f"{kind}: original result changed by recorder")
        result_ref = receipt["native_result_or_bundle_ref"]
        require(result_ref and result_ref["sha256"]
                == hashlib.sha256((folder / f"native-result{suffix}.json")
                                  .read_bytes()).hexdigest(),
                f"{kind}: native result content identity not preserved")
    else:
        require(chain[-2]["name"] == "EngineeringCi.repositoryRuffTools"
                and chain[-1]["name"]
                == "engineering-ci:EngineeringCi.repositoryRuffTools",
                "artifact: wrong Function module lineage")
        require(receipt["native_check_result"] is None,
                "artifact: standalone Function was falsely labeled a Check")
        artifact = folder / "entrypoints" / "ruff-tools"
        require(int((folder / "entrypoints" / "artifact.exit")
                    .read_text().strip()) == 0,
                "artifact: native export failed")
        require(artifact.is_file() and bool(artifact.read_bytes()),
                "artifact: no native output")
        print(f"artifact_sha256={hashlib.sha256(artifact.read_bytes()).hexdigest()}")
    print(f"PASS A.1 {kind} native_span_chain={len(chain)}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit("usage: assert_dagger_native_a1.py <artifact-dir>")
    folder = Path(argv[1])
    verify("pass", folder, native_exit=0)
    verify("fail", folder, native_exit=1)
    verify("artifact", folder, native_exit=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
