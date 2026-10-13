"""Opt-in real Docker Collector/Jaeger acceptance; never a core CI prerequisite."""

import json
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

import pytest
from trace_projection import records

from engineering_ci.tracing.config import TelemetryConfig
from engineering_ci.tracing.projection import standardize
from engineering_ci.tracing.telemetry import deliver

OWNER = "engineering-ci.trace-p1-acceptance"
JAEGER = "cr.jaegertracing.io/jaegertracing/jaeger:2.22.0"
COLLECTOR = "otel/opentelemetry-collector:0.162.0"


def docker(*args):
    """Do not forward host environment values into containers or print raw tool errors."""
    process = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=90,
    )
    if process.returncode:
        raise RuntimeError(
            f"Docker operation failed ({args[0]}, exit {process.returncode}); inspect local Docker separately"
        )
    return process.stdout.strip()


def owned(name):
    data = json.loads(docker("inspect", name))[0]
    assert data["Config"]["Labels"].get("engineering-ci.owner") == OWNER


def stop(name):
    owned(name)
    docker("stop", "--time", "2", name)


def port(name, internal):
    data = json.loads(docker("inspect", name))[0]
    return int(data["NetworkSettings"]["Ports"][f"{internal}/tcp"][0]["HostPort"])


def wait_trace(endpoint, trace_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urlopen(
                f"{endpoint}/api/traces/{trace_id:032x}", timeout=1
            ) as response:
                value = json.load(response)
            if value.get("data"):
                return value["data"][0]
        except (URLError, TimeoutError, ValueError):
            pass
        time.sleep(0.2)
    raise AssertionError(
        "Jaeger did not return the acknowledged trace within the acceptance deadline"
    )


@pytest.fixture
def backend():
    """Own and clean only containers/network created by this one acceptance fixture."""
    prefix = "engineering-ci-p1-" + uuid4().hex[:12]
    names, network_created = [], False
    try:
        docker("network", "create", "--label", f"engineering-ci.owner={OWNER}", prefix)
        network_created = True
        name = prefix + "-jaeger"
        docker(
            "run",
            "--detach",
            "--name",
            name,
            "--label",
            f"engineering-ci.owner={OWNER}",
            "--network",
            prefix,
            "--network-alias",
            "jaeger",
            "--publish",
            "127.0.0.1::16686",
            "--publish",
            "127.0.0.1::4318",
            JAEGER,
        )
        names.append(name)
        collector = prefix + "-collector"
        config = Path(__file__).resolve().parents[2] / "config/collector.yaml"
        docker(
            "run",
            "--detach",
            "--name",
            collector,
            "--label",
            f"engineering-ci.owner={OWNER}",
            "--network",
            prefix,
            "--publish",
            "127.0.0.1::4318",
            "--mount",
            f"type=bind,source={config},target=/etc/otelcol/config.yaml,readonly",
            COLLECTOR,
            "--config=/etc/otelcol/config.yaml",
        )
        names.append(collector)
        direct, forwarding, query = (
            port(name, 4318),
            port(collector, 4318),
            port(name, 16686),
        )
        # Wait for actual socket listeners, not a Docker 'running' flag alone.
        import socket

        deadline = time.monotonic() + 30
        for value in (direct, forwarding, query):
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", value), timeout=0.5):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise AssertionError(
                            "Local Collector/Jaeger is unavailable; inspect its configuration"
                        ) from None
                    time.sleep(0.2)
        yield {
            "jaeger": name,
            "collector": collector,
            "direct": f"http://127.0.0.1:{direct}/v1/traces",
            "forwarding": f"http://127.0.0.1:{forwarding}/v1/traces",
            "query": f"http://127.0.0.1:{query}",
        }
    finally:
        for name in reversed(names):
            owned(name)
            docker("rm", "--force", name)
        if network_created:
            info = json.loads(docker("network", "inspect", prefix))[0]
            assert (
                info["Labels"].get("engineering-ci.owner") == OWNER
                and not info["Containers"]
            )
            docker("network", "rm", prefix)


@pytest.mark.parametrize("route", ["direct", "forwarding"])
def test_actual_otlp_to_jaeger_readback_preserves_failure_and_archive_link(
    backend, route
):
    value = standardize(records(), "a" * 64)
    result = deliver(
        value, "0" * 64, TelemetryConfig(endpoint=backend[route], timeout=2)
    )
    assert result["status"] == "accepted" and not result["backend_persistence_verified"]
    observed = wait_trace(
        backend["query"], int(value["spans"][0]["context"]["trace_id"], 16)
    )
    span = observed["spans"][0]
    tags = {x["key"]: x["value"] for x in span["tags"]}
    assert tags["engineering_ci.archive.snapshot_id"] == "a" * 64
    assert (
        tags["process.exit.code"] == 1
        and tags["engineering_ci.operation.outcome"] == "failed"
    )
    assert span["spanID"] == value["spans"][0]["context"]["span_id"][2:].zfill(16)
    assert span["startTime"] == 1_000_000 and span["duration"] == 1_000_000


def test_collector_acknowledges_before_backend_recovery_without_false_persistence(
    backend,
):
    value = standardize(records(), "a" * 64)
    stop(backend["jaeger"])
    result = deliver(
        value, "0" * 64, TelemetryConfig(endpoint=backend["forwarding"], timeout=2)
    )
    assert result["status"] == "accepted" and not result["backend_persistence_verified"]
    owned(backend["jaeger"])
    docker("start", backend["jaeger"])
    query = f"http://127.0.0.1:{port(backend['jaeger'], 16686)}"
    assert wait_trace(query, int(value["spans"][0]["context"]["trace_id"], 16))["spans"]
