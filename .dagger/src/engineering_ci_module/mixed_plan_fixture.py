"""Count actual mixed-Plan work; observers call the original implementations."""

import json
import time
from pathlib import Path

from dagger import dag

from . import link_cache_fixture, tooling

_JOURNAL_SUFFIX = """\
import fcntl, pathlib
with pathlib.Path('/eci-observer/processes.jsonl').open('a') as journal:
    fcntl.flock(journal, fcntl.LOCK_EX)
    journal.write(json.dumps({'argv':sys.argv[1:], 'exit_code':code,
                             'process_started_at':started_at,
                             'process_finished_at':datetime.datetime.now(datetime.UTC).isoformat()}) + '\\n')
"""


async def observe(module, plan, source):
    from engineering_ci import execution, native_producer, plan_contract, selection

    from .main import _exec_capture

    # Diagnostic writes happen only when the actual Exec runs. Mutable journal
    # bytes never feed a verdict or a producer output, and are not a pass cache.
    source_digest = await source.digest()
    journal_volume = dag.cache_volume(
        "eci-mixed-plan-observer-" + source_digest.removeprefix("sha256:")
    )
    journal_container = (
        dag.container()
        .from_(tooling.UV_IMAGE)
        .with_mounted_cache("/eci-observer", journal_volume)
    )

    async def journal(reset=False):
        script = (
            "from pathlib import Path; p=Path('/eci-observer/processes.jsonl');"
            + (
                "p.write_text('');print('')"
                if reset
                else "print(p.read_text() if p.exists() else '')"
            )
        )
        raw = (
            await journal_container.with_env_variable(
                "ECI_OBSERVER_READ_AT_NS", str(time.time_ns())
            )
            .with_exec(["python", "-I", "-c", script])
            .stdout()
        )
        return [json.loads(line) for line in raw.splitlines() if line]

    async def capture(container, argv, *, with_container=False):
        return await _exec_capture(
            container.with_mounted_cache("/eci-observer", journal_volume),
            argv,
            with_container=with_container,
            script_suffix=_JOURNAL_SUFFIX,
        )

    await journal(reset=True)

    commands = []
    checks = []
    original_command = execution._run_command
    original_check = execution.run_host_check

    def command(*args, **kwargs):
        started = time.time_ns()
        completed, argv = original_command(*args, **kwargs)
        commands.append(
            {
                "argv": argv,
                "started_at_unix_ns": started,
                "finished_at_unix_ns": time.time_ns(),
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
        return completed, argv

    def check(*args, **kwargs):
        started = time.time_ns()
        result = original_check(*args, **kwargs)
        checks.append(
            {
                "name": result.get("name"),
                "started_at_unix_ns": started,
                "finished_at_unix_ns": time.time_ns(),
                "status": result.get("status"),
                "exit_code": result.get("exit_code"),
                "result_id": result.get("result_id"),
            }
        )
        return result

    async def audit():
        processes = await journal()
        return {
            "module_local_processes": list(commands),
            "local_check_calls": list(checks),
            "native_processes": [
                item
                for item in processes
                if Path(item["argv"][0]).name in {"git", "lychee"}
                or item["argv"][:2] == ["python", "tools/build_docs.py"]
            ],
        }

    execution._run_command = command
    execution.run_host_check = check
    try:
        bundle = await link_cache_fixture.observe(
            module, plan, source, audit=audit, capture=capture
        )
    finally:
        execution._run_command = original_command
        execution.run_host_check = original_check
    observations = await audit()
    observations["native_process_journal"] = await journal()
    implementation, hashes = native_producer.implementation(
        "engineering-ci/dagger/mixed-plan-observation",
        {
            "dagger/mixed_plan_fixture.py": Path(__file__),
            "dagger/native_prerequisite.py": Path(__file__).with_name(
                "native_prerequisite.py"
            ),
            "engineering_ci/selection.py": Path(selection.__file__),
            "engineering_ci/plan_contract.py": Path(plan_contract.__file__),
        },
    )
    observations.update(implementation=implementation, implementation_sources=hashes)
    return bundle.with_new_file(
        "mixed-observations.json", json.dumps(observations, sort_keys=True)
    )
