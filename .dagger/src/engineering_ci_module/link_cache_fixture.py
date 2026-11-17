"""Native cache/freshness experiment; this controlled service is not a Check."""

import json
import time
from pathlib import Path

from dagger import dag

from . import plan_execution, tooling

_SERVER = """\
import datetime
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

status = 200
requests = []

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/target':
            requests.append({'method': self.command, 'path': self.path,
                             'status': status,
                             'at': datetime.datetime.now(datetime.UTC).isoformat()})
            payload = b'controlled link target'
            self.send_response(status)
        else:
            payload = json.dumps({'status': status, 'requests': requests}).encode()
            self.send_response(200)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        global status
        if self.path == '/reset':
            requests.clear()
            status = 200
        else:
            status = int(self.path.rsplit('/', 1)[1])
        self.do_GET()

HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
"""

_CONTROL = """\
import sys, urllib.request
request = urllib.request.Request('http://link-fixture:8080' + sys.argv[1],
                                 method=sys.argv[2])
with urllib.request.urlopen(request, timeout=10) as response:
    print(response.read().decode())
"""


async def observe(module, plan, source, *, audit=None, capture=None):
    from engineering_ci import execution, hygiene, native_producer

    from .main import _exec_capture

    _exec_capture = capture or _exec_capture

    installed, error = await module._lychee_container()
    if error is not None:
        return dag.directory().with_new_file(
            "installation-error.json", json.dumps(error)
        )
    service = (
        dag.container()
        .from_(tooling.UV_IMAGE)
        .with_new_file("/service.py", _SERVER)
        .with_exposed_port(8080)
        .as_service(args=["python", "/service.py"])
    )
    bound_tool = installed.with_service_binding("link-fixture", service)

    async def control(path="/journal", method="GET"):
        # These POSTs deliberately mutate only the disposable fixture service.
        # Their real responses, distinct from cached reads, are retained below.
        container = bound_tool.with_env_variable(
            "ECI_FIXTURE_CONTROL_REQUESTED_AT_NS", str(time.time_ns())
        )
        outcome = await _exec_capture(
            container, ["python", "-c", _CONTROL, path, method]
        )
        if outcome["exit_code"] != 0:
            raise RuntimeError(json.dumps(outcome))
        return {"process": outcome, "response": json.loads(outcome["stdout"])}

    class BoundModule:
        def __getattr__(self, name):
            return getattr(module, name)

        async def _run_lychee(self, consumer_source, argv_tail):
            return await module._run_lychee(
                consumer_source, argv_tail, installed=bound_tool, capture=_exec_capture
            )

        async def _run_git(self, consumer_source, argv_tail):
            return await module._run_git(
                consumer_source, argv_tail, capture=_exec_capture
            )

        async def _run_dagger_prerequisite(self, consumer_source, name, spec):
            return await module._run_dagger_prerequisite(
                consumer_source, name, spec, capture=_exec_capture
            )

    async def read_result(saved):
        original_id = await saved.artifacts.id()
        audit_before = await audit() if audit is not None else None
        before = await control()
        report = await saved.report()
        try:
            await saved.enforce()
            strict = {"status": "passed"}
        except RuntimeError as error:
            strict = {"status": "failed", "diagnostic": str(error)}
        after = await control()
        audit_after = await audit() if audit is not None else None
        return {
            "report_equals_artifact": report
            == await saved.artifacts.file("report.json").contents(),
            "same_bundle_id": original_id == await saved.artifacts.id(),
            "strict": strict,
            "before": before,
            "after": after,
            "audit_before": audit_before,
            "audit_after": audit_after,
        }

    reset = await control("/reset", "POST")
    argv = ["/usr/local/bin/lychee", "--cache=false", "--no-progress", "docs/link.md"]
    # Control: a cache-never Function still reuses an unchanged Exec result.
    legacy = bound_tool.with_directory(
        "/workspace", source, exclude=list(tooling.SOURCE_NOISE)
    ).with_workdir("/workspace")
    legacy_first = await _exec_capture(legacy, argv)
    changed = await control("/status/404", "POST")
    legacy_repeated = await _exec_capture(legacy, argv)
    legacy_journal = await control()

    restored = await control("/status/200", "POST")
    passed = await plan_execution.execute(
        BoundModule(), plan, source, "3.14", "ci-weaver.toml"
    )
    passed_readers = await read_result(passed)
    broken = await control("/status/404", "POST")
    failed = await plan_execution.execute(
        BoundModule(), plan, source, "3.14", "ci-weaver.toml"
    )
    failed_readers = await read_result(failed)

    offline_before = await control()
    offline_args = ["--offline", "--no-progress", "docs/link.md"]
    offline_first = await module._run_lychee(
        source, offline_args, installed=bound_tool, capture=_exec_capture
    )
    offline_repeated = await module._run_lychee(
        source, offline_args, installed=bound_tool, capture=_exec_capture
    )
    missing_local_source = source.without_file("docs/local.md")
    missing_local = await module._run_lychee(
        missing_local_source, offline_args, installed=bound_tool, capture=_exec_capture
    )
    missing_local_repeated = await module._run_lychee(
        missing_local_source, offline_args, installed=bound_tool, capture=_exec_capture
    )
    offline_after = await control()
    identity = await _exec_capture(
        installed,
        [
            "python",
            "-c",
            (
                "import hashlib,json,pathlib,platform,subprocess;"
                "print(json.dumps({'version':subprocess.check_output(['lychee','--version']).decode(),"
                "'binary_sha256':hashlib.sha256(pathlib.Path('/usr/local/bin/lychee').read_bytes()).hexdigest(),"
                "'platform':platform.platform(),'machine':platform.machine()}))"
            ),
        ],
    )
    implementation_files = {
        "dagger/" + name: Path(__file__).with_name(name)
        for name in (
            "main.py",
            "link_check.py",
            "link_cache_fixture.py",
            "plan_execution.py",
            "plan_result.py",
            "tooling.py",
        )
    }
    implementation_files.update(
        {
            "engineering_ci/execution.py": Path(execution.__file__),
            "engineering_ci/hygiene.py": Path(hygiene.__file__),
        }
    )
    implementation, implementation_sources = native_producer.implementation(
        "engineering-ci/dagger/link-observation", implementation_files
    )
    observations = {
        "scope": "controlled_HTTP_service_and_real_Lychee_Plan",
        "remote": "http://link-fixture:8080/target",
        "origin_execution_attributed": False,
        "implementation": implementation,
        "implementation_sources": implementation_sources,
        "source_digest": await source.digest(),
        "tool_asset": tooling.LYCHEE_ASSET,
        "tool_identity": identity,
        "reset": reset,
        "legacy_first": legacy_first,
        "changed": changed,
        "legacy_repeated": legacy_repeated,
        "legacy_journal": legacy_journal,
        "restored": restored,
        "passed_readers": passed_readers,
        "broken": broken,
        "failed_readers": failed_readers,
        "offline_before": offline_before,
        "offline_first": offline_first,
        "offline_repeated": offline_repeated,
        "missing_local": missing_local,
        "missing_local_repeated": missing_local_repeated,
        "offline_after": offline_after,
    }
    return (
        dag.directory()
        .with_directory("passed", passed.artifacts)
        .with_directory("failed", failed.artifacts)
        .with_new_file("observations.json", json.dumps(observations, sort_keys=True))
    )
