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


def registry_tree_digest(files):
    """Canonical digest over a Registry-relative path -> file SHA-256 map.

    Must stay identical to ``engineering_ci.semconv_registry.registry_tree_digest``:
    the pinned official tree digest is compared against this value.
    """
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def official_source_pin():
    """Read the pinned official upstream identity injected by the Dagger module."""
    raw = os.environ.get("ECI_SEMCONV_OFFICIAL_SOURCE")
    if not raw:
        return None
    try:
        pin = json.loads(raw)
    except ValueError:
        return None
    if (
        not isinstance(pin, dict)
        or not pin.get("repository")
        or not pin.get("commit")
        or not pin.get("version")
        or not pin.get("tree_sha256")
    ):
        return None
    return pin


def official_source_status(*, files, origin, pin):
    """Decide whether the Registry bytes match the pinned official upstream source.

    The pin lives outside the Source Directory, so editing the Registry and its
    manifest together cannot satisfy it. ``fatal`` marks a claimed official
    identity that could not be verified; the caller must refuse to continue.
    """
    if not pin:
        return {
            "verified": False,
            "fatal": True,
            "reason": "official_source_pin_unavailable",
        }
    claimed = (origin or {}).get("repository")
    if not claimed:
        return {
            "verified": False,
            "fatal": False,
            "reason": "origin_repository_undeclared",
        }
    if claimed != pin["repository"]:
        return {
            "verified": False,
            "fatal": False,
            "reason": "origin_not_pinned_official_source",
        }
    if (origin or {}).get("commit") != pin["commit"]:
        return {
            "verified": False,
            "fatal": True,
            "reason": "official_source_commit_unverified",
        }
    # The declared Source version is part of the claimed official identity.
    # A Source may not present a version the pinned upstream release does not
    # carry, so the bytes and the version are verified together.
    if (origin or {}).get("version") != pin["version"]:
        return {
            "verified": False,
            "fatal": True,
            "reason": "official_source_version_mismatch",
            "declared_version": (origin or {}).get("version"),
            "pinned_version": pin["version"],
        }
    actual = registry_tree_digest(files)
    if actual != pin["tree_sha256"]:
        return {
            "verified": False,
            "fatal": True,
            "reason": "official_registry_bytes_mismatch",
            "actual_tree_sha256": actual,
        }
    return {
        "verified": True,
        "fatal": False,
        "reason": None,
        "repository": pin["repository"],
        "commit": pin["commit"],
        "version": pin.get("version"),
        "tree_sha256": actual,
        # The upstream tree object the pinned digest corresponds to. The
        # runtime recomputes the canonical SHA-256 map, not a git tree object,
        # so this is the pinned upstream identity and not a local measurement.
        "pinned_upstream_git_tree_sha1": pin.get("git_tree_sha1"),
    }


def _collect_entity_associations(manifest, declared):
    """Record the entity associations a Registry file declares, keyed by signal.

    Binding a Span Group to an entity is what makes a Resource attribute
    obligation enforceable, so the coverage matrix is derived from the
    Registry that was read rather than assumed for every Source.
    """
    for group in manifest.get("groups") or []:
        if not isinstance(group, dict):
            continue
        associations = group.get("entity_associations")
        group_id = group.get("id")
        if (
            not isinstance(associations, list)
            or not associations
            or not isinstance(group_id, str)
            or not group_id
        ):
            continue
        # Native packaging resolves ``span.<signal>`` to the v2 signal type
        # ``<signal>``.
        signal = group_id.removeprefix("span.")
        declared.setdefault(signal, set()).update(str(item) for item in associations)


# Native advice id Weaver emits when an Entity Required attribute is absent.
_NATIVE_ENTITY_REQUIRED_FINDING = "entity_required_attribute_not_present"

# The CI/CD Pipeline span group the Resource Required coverage reasons about.
PIPELINE_SIGNAL = "cicd.pipeline.run.server"


def native_entity_rule_evidence(native_report, entities, expected_signal):
    """Native findings proving a declared Entity Required rule actually ran.

    Weaver reports an Entity Required rule only when it is violated, so that
    finding is the native evidence that the rule was applied. Its absence
    proves nothing: the rule may have run and passed.

    A finding must belong to the target signal itself. A finding on another
    matched signal says nothing about this rule, even when both signals carry
    the same entity. This never re-implements the SemConv Required judgement.
    """
    found = []
    for sample in (native_report or {}).get("samples") or []:
        body = sample.get("span") if isinstance(sample, dict) else None
        if not isinstance(body, dict):
            continue
        result = body.get("live_check_result") or {}
        match = result.get("match_info") or {}
        signal = match.get("signal")
        if not isinstance(signal, str) or signal != expected_signal:
            continue
        for advice in result.get("all_advice") or []:
            if not isinstance(advice, dict):
                continue
            if advice.get("id") != _NATIVE_ENTITY_REQUIRED_FINDING:
                continue
            context = advice.get("context") or {}
            entity = context.get("entity_type")
            if entity not in entities:
                continue
            found.append(
                {
                    "signal": signal,
                    "entity_type": entity,
                    "attribute_key": context.get("attribute_key"),
                    "level": advice.get("level"),
                    "message": advice.get("message"),
                }
            )
    return found


def registry_inputs(config, key="registry", *, verify_official=True):
    registry = local_path(ROOT, config[key])
    expected_path = local_path(ROOT, config[key + "_manifest"])
    expected = json.loads(expected_path.read_text())
    if not isinstance(expected, dict) or not expected:
        raise ValueError("registry_manifest_invalid")
    actual = {}
    entity_associations = {}
    for path in sorted(registry.rglob("*")):
        if path.is_symlink():
            raise ValueError("registry_symlink")
        if path.is_file():
            actual[path.relative_to(registry).as_posix()] = digest(path)
            if path.suffix in {".yaml", ".yml"}:
                manifest = yaml.safe_load(path.read_text())
                if not isinstance(manifest, dict):
                    continue
                _collect_entity_associations(manifest, entity_associations)
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
    identity = {"manifest_sha256": digest(expected_path), "files": actual}
    # A baseline Registry is an optional derived comparison input; only the
    # packaged Registry itself is claimed against the pinned official source.
    official = official_source_status(
        files=actual,
        origin=config.get("origin") or {},
        pin=official_source_pin(),
    )
    if not verify_official:
        # A derived comparison Registry never gates the run, so it must not
        # carry a fatal verdict for a run that deliberately continues.
        official = {**official, "fatal": False, "role": "comparison_baseline"}
    identity["official_source"] = official
    identity["entity_associations"] = {
        signal: sorted(items) for signal, items in entity_associations.items()
    }
    return registry, identity


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


def coverage_from_execution(
    *,
    config,
    evaluation,
    advice_meta,
    kind,
    official_source=None,
    entity_associations=None,
):
    """Derive coverage statuses from this run's matched signals and advisors.

    Obligations without an applicable CI/CD signal stay ``not_applicable``.
    Applicable but unloaded Advisors stay ``not_verified``. Never hard-code
    ``executed`` for fixtures that did not match Pipeline/Task groups.

    Official SemConv obligations and ECI-owned obligations are reported
    separately. Whether a Resource Required obligation exists is derived from
    the Registry that was read; ECI's own Run-ID requirement is a correlation
    rule enforced by ``weaver-terminal`` and is reported under its own rule ID.
    """
    matched_signals = set()
    matched_matchers = set()
    native = (evaluation or {}).get("native_report") or {}
    for sample in native.get("samples") or []:
        body = None
        if isinstance(sample, dict):
            body = sample.get("span") or sample.get("resource")
        if not isinstance(body, dict):
            continue
        match = (body.get("live_check_result") or {}).get("match_info") or {}
        signal = match.get("signal")
        matcher = match.get("signal_matcher") or match.get("matcher")
        if match.get("unmatched") is False and isinstance(signal, str) and signal:
            matched_signals.add(signal)
        if isinstance(matcher, str) and matcher:
            matched_matchers.add(matcher)
    cicd_pipeline = PIPELINE_SIGNAL in matched_signals
    cicd_task = "cicd.pipeline.task.internal" in matched_signals
    cicd_applicable = cicd_pipeline or cicd_task
    official_verified = bool((official_source or {}).get("verified"))
    if cicd_applicable:
        required_status = "executed"
        result_status = "executed"
    else:
        required_status = "not_applicable"
        result_status = "not_applicable"
    # Two separate propositions. The Registry *declares* which entities a Span
    # Group is bound to; that declaration is not proof that native Weaver
    # *executed* the Entity Required rule. Only native evidence from this run
    # admits ``executed``; a declared association without such evidence is
    # ``not_verified``, never ``executed`` and never wrongly ``not_applicable``.
    pipeline_entities = (entity_associations or {}).get(PIPELINE_SIGNAL)
    native_report = (evaluation or {}).get("native_report") or {}
    resource_evidence = []
    if not pipeline_entities:
        resource_status = "not_applicable"
        resource_entities = []
        resource_basis = (
            "pinned_semconv_declares_no_entity_association_for_pipeline_span"
            if official_verified
            else "registry_declares_no_entity_association_for_pipeline_span"
        )
    elif not cicd_pipeline:
        # The Registry does declare the association; this run simply did not
        # match the Pipeline signal, so no rule was applicable here. The
        # declaration is kept as read.
        resource_status = "not_applicable"
        resource_entities = list(pipeline_entities)
        resource_basis = "pipeline_signal_not_matched"
    else:
        resource_entities = list(pipeline_entities)
        resource_evidence = native_entity_rule_evidence(
            native_report, pipeline_entities, PIPELINE_SIGNAL
        )
        if resource_evidence:
            resource_status = "executed"
            resource_basis = "native_entity_required_finding_observed"
        else:
            resource_status = "not_verified"
            resource_basis = (
                "entity_association_declared_without_native_execution_evidence"
            )
    if not cicd_applicable:
        advice_status = "not_applicable"
    elif advice_meta:
        advice_status = "executed"
    else:
        advice_status = "not_verified"
    # Coverage records whether terminal correlation ran, not merely whether the
    # Check kind requested it. Failed correlation still counts as executed.
    if kind != "terminal":
        terminal_status = "not_applicable"
    else:
        correlation = (evaluation or {}).get("terminal_correlation_status")
        if correlation in {"passed", "failed"}:
            terminal_status = "executed"
        else:
            terminal_status = "not_verified"
    provenance = (evaluation or {}).get("time_provenance")
    if not isinstance(provenance, dict):
        provenance = {
            "status": "not_applicable",
            "reason": "no_observed_span_time_source",
        }
    # On a verified Source the certified identity comes from the pin, so the
    # Report never presents a Source-declared version as officially certified.
    return {
        "registry_version": (
            (official_source or {}).get("version")
            if official_verified
            else (config.get("origin") or {}).get("version")
        ),
        "registry_commit": (
            (official_source or {}).get("commit")
            if official_verified
            else (config.get("origin") or {}).get("commit")
        ),
        "official_registry_verified": official_verified,
        # Certified identity in one place: version, commit and the canonical
        # digest of the verified Registry bytes.
        "official_registry_tree_sha256": (official_source or {}).get("tree_sha256"),
        "official_source_reason": (official_source or {}).get("reason"),
        "expected_matchers": list(config.get("expected_matchers") or []),
        "matched_matchers": sorted(matched_matchers),
        "matched_signals": sorted(matched_signals),
        "pipeline_task_required": required_status,
        "conditionally_required_advice": {
            "policy_id": "cicd_conditional_error_type_required",
            "status": advice_status,
            "loaded": bool(advice_meta),
            "digest": (advice_meta or {}).get("sha256"),
        },
        "result_type_gate": result_status,
        "resource_required": resource_status,
        "resource_required_basis": resource_basis,
        # The Registry's declaration, and the native evidence that the rule
        # actually ran. The declaration alone never means ``executed``.
        "resource_required_entities": resource_entities,
        "resource_required_native_evidence": resource_evidence,
        "eci_run_id_correlation": {
            "rule_id": "eci_terminal_run_id_correlation",
            "status": terminal_status,
            "attribute": "cicd.pipeline.run.id",
            "enforced_by": "weaver-terminal",
            "failure_reasons": list(weaver.RUN_ID_CORRELATION_FAILURE_REASONS),
        },
        "terminal_correlation": terminal_status,
        "terminal_gate_failure_reasons": list(weaver.TERMINAL_GATE_FAILURE_REASONS),
        # Always a structured record, so a consumer can read it without
        # special-casing a missing or absent observation. A Terminal PASS does
        # not prove an exact end time or a duration.
        "time_provenance": provenance,
        "eci_policy_evaluated": (
            advice_status == "executed"
            or result_status == "executed"
            or terminal_status == "executed"
        ),
        "passed_means": (
            "only executed applicable obligations above; not full SemConv; "
            "official Registry bytes are verified separately from "
            "eci_policy_evaluated"
        ),
    }


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
        # Official upstream Registry bytes and ECI-owned policy evaluation are
        # independent of the gate result: a run can evaluate ECI policies and
        # fail, and a run can fail before any policy was evaluated.
        "official_registry_verified": False,
        "eci_policy_evaluated": False,
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
        # The declared entity associations are an internal input to the
        # coverage decision; the Report carries the decision, not every
        # metric group's bindings.
        entity_associations = identity.pop("entity_associations", None)
        # Record the Registry identity before any refusal, so the Report still
        # carries the computed digests that explain the refusal.
        report["inputs"]["registry"] = identity
        official = identity.get("official_source") or {}
        report["official_registry_verified"] = bool(official.get("verified"))
        if official.get("fatal"):
            raise ValueError(official["reason"])
        packaged = package_registry(report, registry)
        if kind == "registry":
            if "baseline" in config:
                baseline, baseline_identity = registry_inputs(
                    config, "baseline", verify_official=False
                )
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
            coverage_from_execution(
                config=config,
                evaluation=evaluation,
                advice_meta=advice_meta,
                kind=kind,
                official_source=identity.get("official_source"),
                entity_associations=entity_associations,
            )
        )
        report["coverage"] = coverage
        report["eci_policy_evaluated"] = bool(coverage.get("eci_policy_evaluated"))
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
