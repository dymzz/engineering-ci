"""Forward shared options without selecting a workflow or dispatching."""

from ..execution.cli import add_runtime_arguments


def runtime_arguments(options):
    args = []
    for name in ("refresh_repo", "save_log", "no_artifacts", "show_state"):
        if getattr(options, name):
            args.append("--" + name.replace("_", "-"))
    for name in (
        "artifact",
        "out",
        "interval",
        "timeout",
        "retry_attempts",
        "retry_delay",
        "download_backend",
        "connections",
        "download_timeout",
        "connect_timeout",
        "download_process_timeout",
        "download_recovery",
        "aria2c",
        "max_extract_bytes",
        "max_extract_files",
    ):
        value = getattr(options, name)
        if value is not None:
            args += ["--" + name.replace("_", "-"), str(value)]
    return args


__all__ = ["add_runtime_arguments", "runtime_arguments"]
