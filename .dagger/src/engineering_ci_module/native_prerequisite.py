"""Run explicitly source-only prerequisites as ordinary cached Dagger Execs."""

import dagger

from engineering_ci import plan_contract


async def run(client, source, name, spec, *, capture):
    plan_contract.validate_dagger_command(spec)
    image = client.container(platform=dagger.Platform(spec["platform"])).from_(
        spec["image"]
    )
    resolved_image = await image.image_ref()
    output_path = "/eci-artifacts"
    container = (
        image.with_mounted_directory("/workspace", source, read_only=True)
        .with_workdir("/workspace")
        .with_env_variable("PYTHONDONTWRITEBYTECODE", "1")
        .with_env_variable("ENGINEERING_CI_ARTIFACT_DIR", output_path)
        .with_exec(
            [
                "python",
                "-I",
                "-c",
                f"from pathlib import Path;Path({output_path!r}).mkdir()",
            ]
        )
    )
    argv = [arg.replace("{artifact_dir}", output_path) for arg in spec["argv"]]
    outcome, executed = await capture(container, argv, with_container=True)
    outputs = await executed.directory(output_path).sync()
    code = outcome["exit_code"]
    entry = {
        "name": name,
        "type": "dagger_command",
        "status": "passed" if code == 0 else "failed",
        "reason": None if code == 0 else "prerequisite_command_failed",
        "exit_code": code,
        "argv": argv,
        "stdout": outcome["stdout"],
        "stderr": outcome["stderr"],
        "execution_context": "dagger_export",
        "execution_location": "dagger_container",
        "result_origin": "dagger",
        "engine_cache_policy": "native_exec",
        "engine_cache_outcome": "unknown",
        "observation": {
            "process_started_at": outcome["process_started_at"],
            "process_finished_at": outcome["process_finished_at"],
            "source_directory_digest": await source.digest(),
            "image": resolved_image,
            "platform": spec["platform"],
            "source_read_only": True,
            "origin_execution_attributed": False,
        },
        "artifact_ref": {
            "directory_id": str(await outputs.id()),
            "digest": await outputs.digest(),
        },
    }
    return entry, outputs
