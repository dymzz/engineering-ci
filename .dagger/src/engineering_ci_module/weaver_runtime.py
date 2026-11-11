"""Container entrypoint for pinned Weaver checks and finite OTLP replay."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import subprocess
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import weaver
import yaml

OUT = Path("/out")
ROOT = Path("/workspace")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_path(root, value):
    if (
        not isinstance(value, str)
        or not value
        or Path(value).is_absolute()
        or "\\" in value
        or urllib.parse.urlsplit(value).scheme
    ):
        raise ValueError("input_path_invalid")
    path = root / value
    if ".." in Path(value).parts or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("input_path_escapes_source")
    if any(
        item.is_symlink() for item in [path, *path.parents] if item.is_relative_to(root)
    ):
        raise ValueError("symlink_input")
    return path


def registry_inputs(config, key="registry"):
    registry = local_path(ROOT, config[key])
    expected_path = local_path(ROOT, config[key + "_manifest"])
    expected = json.loads(expected_path.read_text())
    if not isinstance(expected, dict) or not expected:
        raise ValueError("registry_manifest_invalid")
    actual = {}
    for path in sorted(registry.rglob("*")):
        if path.is_symlink():
            raise ValueError("registry_symlink")
        if path.is_file():
            actual[path.relative_to(registry).as_posix()] = digest(path)
            if path.suffix in {".yaml", ".yml"}:
                manifest = yaml.safe_load(path.read_text())
                if not isinstance(manifest, dict):
                    continue
                if manifest.get("dependencies"):
                    raise ValueError(
                        "registry_dependencies_require_local_materialization"
                    )
                uri = manifest.get("resolved_registry_uri")
                if uri:
                    # Native local references resolve against /workspace, not the manifest.
                    resolved = local_path(ROOT, uri)
                    if not resolved.is_file() or not resolved.resolve().is_relative_to(
                        registry.resolve()
                    ):
                        raise ValueError("publication_not_in_registry_closure")
    if actual != expected:
        raise ValueError("registry_bytes_mismatch")
    return registry, {"manifest_sha256": digest(expected_path), "files": actual}


def command(report, name, argv):
    stdout, stderr = OUT / (name + ".stdout"), OUT / (name + ".stderr")
    entry = {
        "argv": argv,
        "exit_code": None,
        "stdout": stdout.name,
        "stderr": stderr.name,
    }
    report["commands"].append(entry)
    with stdout.open("wb") as out, stderr.open("wb") as err:
        result = subprocess.run(argv, stdout=out, stderr=err, timeout=180, check=False)
    entry["exit_code"] = result.returncode
    report["exit_code"] = result.returncode
    if result.returncode:
        raise ValueError(name + "_failed")


def package_registry(report, registry, name="registry"):
    target = OUT / (name + "-package")
    command(
        report,
        name + "-check",
        [
            "weaver",
            "registry",
            "check",
            "--v2",
            "--config",
            "/check/empty.toml",
            "-r",
            str(registry),
        ],
    )
    command(
        report,
        name + "-package",
        [
            "weaver",
            "registry",
            "package",
            "--v2",
            "--config",
            "/check/empty.toml",
            "-r",
            str(registry),
            "--output",
            str(target),
            "--resolved-registry-uri",
            str(target / "resolved.yaml"),
        ],
    )
    command(
        report,
        name + "-local-check",
        [
            "weaver",
            "registry",
            "check",
            "--v2",
            "--config",
            "/check/empty.toml",
            "-r",
            str(target),
        ],
    )
    report["inputs"][name]["resolved_sha256"] = digest(target / "resolved.yaml")
    return target


def validate_matcher_config(path):
    config = tomllib.loads(path.read_text())
    if set(config) - {"live-check", "policy"}:
        raise ValueError("matcher_config_external_inputs_unsupported")
    # Allow native search_all_attributes / finding_filters; reject other
    # live-check overrides (advice paths, exporters, unbound inputs).
    # Advice policy directories are declared on ci-weaver.toml and passed via
    # --advice-policies after Source-local materialization (PR #78 P1-1).
    if set(config.get("live-check", {})) - {
        "matchers",
        "search_all_attributes",
        "finding_filters",
    }:
        raise ValueError("live_config_override_unsupported")
    search_all = config.get("live-check", {}).get("search_all_attributes")
    if search_all is not None and type(search_all) is not bool:
        raise ValueError("live_config_override_unsupported")
    filters = config.get("live-check", {}).get("finding_filters")
    if filters is not None and type(filters) is not list:
        raise ValueError("live_config_override_unsupported")
    if set(config.get("policy", {})) - {"finding_filters"}:
        raise ValueError("policy_external_inputs_unsupported")


def materialize_advice_policies(config, report):
    """Copy Source-local Rego Advice into /out and record digests.

    ``--advice-policies`` replaces Weaver defaults, so the Source tree must
    include the pinned default ``otel.rego`` plus ECI extensions.
    """
    relative = config.get("advice_policies")
    if relative is None:
        return None
    source = local_path(ROOT, relative)
    if not source.is_dir():
        raise ValueError("advice_policies_missing")
    files = sorted(
        path
        for path in source.rglob("*.rego")
        if path.is_file() and not path.is_symlink()
    )
    if not files:
        raise ValueError("advice_policies_empty")
    target = OUT / "advice-policies"
    target.mkdir(parents=True, exist_ok=True)
    digests = {}
    for path in files:
        rel = path.relative_to(source).as_posix()
        if ".." in Path(rel).parts:
            raise ValueError("advice_policies_path_invalid")
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = path.read_bytes()
        dest.write_bytes(data)
        digests[rel] = hashlib.sha256(data).hexdigest()
    if "otel.rego" not in digests:
        raise ValueError("advice_policies_default_missing")
    if "cicd-error-type.rego" not in digests:
        raise ValueError("advice_policies_conditional_missing")
    report["inputs"]["advice_policies"] = {
        "path": relative,
        "sha256": hashlib.sha256(
            json.dumps(digests, sort_keys=True).encode()
        ).hexdigest(),
        "files": digests,
    }
    return target


def ports():
    with socket.socket() as grpc_listener, socket.socket() as admin_listener:
        grpc_listener.bind(("127.0.0.1", 0))
        admin_listener.bind(("127.0.0.1", 0))
        return grpc_listener.getsockname()[1], admin_listener.getsockname()[1]


def admin(address, method, endpoint):
    request = urllib.request.Request(
        address + endpoint, data=b"" if method == "POST" else None, method=method
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read()


def otlp_replay(report, argv, otlp):
    import grpc
    from google.protobuf import json_format
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2_grpc import (
        TraceServiceStub,
    )

    # OTLP JSON uses hexadecimal IDs, unlike protobuf JSON's base64 bytes.
    capture = json.loads(otlp.read_text())
    for resource in capture.get("resourceSpans", []):
        for scope in resource.get("scopeSpans", []):
            for span in scope.get("spans", []):
                for record in [span, *span.get("links", [])]:
                    for key, size in [
                        ("traceId", 16),
                        ("spanId", 8),
                        ("parentSpanId", 8),
                    ]:
                        value = record.get(key)
                        if value:
                            decoded = bytes.fromhex(value)
                            if len(decoded) != size:
                                raise ValueError("otlp_id_invalid")
                            record[key] = base64.b64encode(decoded).decode()
    try:
        request = json_format.ParseDict(capture, ExportTraceServiceRequest())
    except json_format.ParseError as error:
        raise ValueError("otlp_json_invalid: " + str(error)) from error
    grpc_port, admin_port = ports()
    address = f"http://127.0.0.1:{admin_port}"
    argv += [
        "--input-source",
        "otlp",
        "--output",
        "http",
        "--otlp-grpc-address",
        "127.0.0.1",
        "--otlp-grpc-port",
        str(grpc_port),
        "--admin-port",
        str(admin_port),
    ]
    with (
        (OUT / "live.stdout").open("wb") as out,
        (OUT / "live.stderr").open("wb") as err,
    ):
        process = subprocess.Popen(argv, stdout=out, stderr=err)
        entry = {
            "argv": argv,
            "exit_code": None,
            "stdout": "live.stdout",
            "stderr": "live.stderr",
        }
        report["commands"].append(entry)
        lifecycle = report["lifecycle"] = {
            "ready": False,
            "export_acknowledged": False,
            "stopped": False,
            "report_received": False,
            "shutdown": False,
        }
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise ValueError("receiver_exited_before_ready")
                try:
                    admin(address, "GET", "/health")
                    lifecycle["ready"] = True
                    break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.1)
            if not lifecycle["ready"]:
                raise ValueError("receiver_ready_timeout")
            with grpc.insecure_channel(f"127.0.0.1:{grpc_port}") as channel:
                grpc.channel_ready_future(channel).result(timeout=15)
                response = TraceServiceStub(channel).Export(request, timeout=30)
                if (
                    response.partial_success.rejected_spans
                    or response.partial_success.error_message
                ):
                    raise ValueError("otlp_partial_rejection")
                lifecycle["export_acknowledged"] = True
            stopped = json.loads(admin(address, "POST", "/stop"))
            if stopped.get("state") != "stopped" or stopped.get("report") is not True:
                raise ValueError("receiver_stop_incomplete")
            lifecycle["stopped"] = True
            raw = admin(address, "GET", "/report")
            (OUT / "live_check.json").write_bytes(raw)
            lifecycle["report_received"] = True
            admin(address, "POST", "/shutdown")
            lifecycle["shutdown"] = True
            code = process.wait(timeout=30)
            entry["exit_code"] = code
            report["exit_code"] = code
            return code, raw.decode()
        except (grpc.RpcError, grpc.FutureTimeoutError) as error:
            raise ValueError("otlp_transport_failed: " + str(error)) from error
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            entry["exit_code"] = process.returncode
            report["exit_code"] = process.returncode


def run(kind, config_path):
    report = {
        "name": "weaver_" + kind,
        "status": "failed",
        "reason": "not_executed",
        "exit_code": None,
        "gate_exit_code": 1,
        "tool": "weaver",
        "version": "0.28.0",
        "trusted": False,
        "commands": [],
        "inputs": {},
    }
    report["source_digest"] = os.environ.get("ECI_SOURCE_DIGEST")
    report["tool_asset_sha256"] = os.environ.get("ECI_WEAVER_ASSET_SHA256")
    try:
        command(report, "version", ["weaver", "--version"])
        if (OUT / "version.stdout").read_text().strip() != "weaver 0.28.0":
            raise ValueError("tool_version_mismatch")
        report["exit_code"] = None
        config_file = local_path(ROOT, config_path)
        config = tomllib.loads(config_file.read_text())
        if config.get("schema_version") != 1:
            raise ValueError("config_schema_invalid")
        report["inputs"]["config_sha256"] = digest(config_file)
        report["origin"] = config.get("origin", {})
        registry, identity = registry_inputs(config)
        report["inputs"]["registry"] = identity
        packaged = package_registry(report, registry)
        if kind == "registry":
            if "baseline" in config:
                baseline, baseline_identity = registry_inputs(config, "baseline")
                report["inputs"]["baseline"] = baseline_identity
                baseline_package = package_registry(report, baseline, "baseline")
                command(
                    report,
                    "registry-diff",
                    [
                        "weaver",
                        "registry",
                        "diff",
                        "--v2",
                        "--config",
                        "/check/empty.toml",
                        "-r",
                        str(packaged),
                        "--baseline-registry",
                        str(baseline_package),
                        "--format",
                        "json",
                        "--output",
                        str(OUT / "diff"),
                    ],
                )
            report.update(status="passed", reason=None, gate_exit_code=0)
            return report
        report["exit_code"] = None
        matcher = local_path(ROOT, config["matcher_config"])
        validate_matcher_config(matcher)
        report["inputs"]["matcher_sha256"] = digest(matcher)
        advice = materialize_advice_policies(config, report)
        argv = [
            "weaver",
            "registry",
            "live-check",
            "--v2",
            "-r",
            str(packaged),
            "--config",
            str(matcher),
            "--format",
            "json",
            "--no-stream",
            "--no-stats=false",
            "--fail-on",
            "violation",
            "--emit-otlp-logs=false",
        ]
        if advice is not None:
            argv.extend(["--advice-policies", str(advice)])
        if kind == "sample":
            sample = local_path(ROOT, config["sample"])
            report["inputs"]["sample_sha256"] = digest(sample)
            result = subprocess.run(
                argv + ["--input-source", str(sample), "--output", str(OUT)],
                capture_output=True,
                timeout=180,
                check=False,
            )
            (OUT / "live.stdout").write_bytes(result.stdout)
            (OUT / "live.stderr").write_bytes(result.stderr)
            report["commands"].append(
                {
                    "argv": argv
                    + ["--input-source", str(sample), "--output", str(OUT)],
                    "exit_code": result.returncode,
                    "stdout": "live.stdout",
                    "stderr": "live.stderr",
                }
            )
            code = result.returncode
            raw = (
                (OUT / "live_check.json").read_text()
                if (OUT / "live_check.json").exists()
                else ""
            )
            evaluation = weaver.live_result(code, raw, config["expected_matchers"])
        else:
            otlp = local_path(ROOT, config["otlp"])
            expected = local_path(ROOT, config["expectations"])
            report["inputs"].update(
                otlp_sha256=digest(otlp), expectations_sha256=digest(expected)
            )
            expectation = json.loads(expected.read_text())
            capture = json.loads(otlp.read_text())
            code, raw = otlp_replay(report, argv, otlp)
            evaluation = weaver.otlp_result(
                code,
                raw,
                expectation,
                terminal=kind == "terminal",
                capture=capture,
            )
        report.update(evaluation)
        advice_meta = (report.get("inputs") or {}).get("advice_policies")
        coverage = dict(report.get("coverage") or {})
        coverage.update(
            {
                "registry_version": (config.get("origin") or {}).get("version"),
                "registry_commit": (config.get("origin") or {}).get("commit"),
                "expected_matchers": list(config.get("expected_matchers") or []),
                "pipeline_task_required": "executed",
                "conditionally_required_advice": {
                    "policy_id": "cicd_conditional_error_type_required",
                    "loaded": bool(advice_meta),
                    "digest": (advice_meta or {}).get("sha256"),
                },
                "result_type_gate": "executed",
                "resource_required": "executed_via_pipeline_entity_association",
                "terminal_correlation": (
                    "executed" if kind == "terminal" else "not_applicable"
                ),
                "passed_means": (
                    "only executed applicable obligations above; not full SemConv"
                ),
            }
        )
        report["coverage"] = coverage
        report["gate_exit_code"] = 0 if report["status"] == "passed" else 1
        return report
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        yaml.YAMLError,
        subprocess.SubprocessError,
    ) as error:
        report["reason"] = str(error)
        return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["registry", "sample", "live", "terminal"])
    parser.add_argument("--config", default="ci-weaver.toml")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    report = run(args.kind, args.config)
    (OUT / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
