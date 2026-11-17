"""Separate local link inputs from network observations at the Lychee Exec."""

import time


def arguments(argv_tail):
    """Only an explicit CLI offline mode permits persistent Exec reuse."""
    args = list(argv_tail)
    separator = args.index("--") if "--" in args else len(args)
    options, targets = args[:separator], args[separator:]
    offline = any(arg in {"--offline", "--offline=true"} for arg in options)
    # Ambiguous/conflicting flags must not accidentally select the cached path.
    offline = offline and "--offline=false" not in options
    if offline:
        return True, args
    # The refreshed Exec must not itself reuse an old .lycheecache response.
    # CLI false also overrides cache=true in a source-provided Lychee config.
    options = [
        arg
        for arg in options
        if arg not in {"--cache", "--cache=true", "--cache=false"}
    ]
    return False, ["--cache=false", *options, *targets]


async def run(client, installed, source, argv_tail, *, capture, source_noise):
    """Keep tool preparation/local scans cached; reobserve online links."""
    offline, args = arguments(argv_tail)
    bound = client.directory().with_directory("/", source, exclude=list(source_noise))
    container = installed.with_directory("/workspace", bound).with_workdir("/workspace")
    requested_at_ns = None
    if not offline:
        # beta.16 currentTimestamp is second-resolution. Use the actual request
        # time at nanosecond resolution only on this external-read Exec input,
        # never on Source, installation or other deterministic graph branches.
        requested_at_ns = time.time_ns()
        container = container.with_env_variable(
            "ECI_LINK_READ_REQUESTED_AT_NS", str(requested_at_ns)
        )
    argv = ["/usr/local/bin/lychee", *args]
    result = await capture(container, argv)
    result["observation"] = {
        "input_mode": "local_only" if offline else "external_network",
        "freshness_policy": "native_exec_cache" if offline else "external_reobserve",
        "requested_at_unix_ns": requested_at_ns,
        "process_started_at": result["process_started_at"],
        "process_finished_at": result["process_finished_at"],
        "execution_location": "dagger_container",
        "source_directory_digest": await bound.digest(),
        "argv": argv,
        "tool_response_cache_disabled": not offline,
        "origin_execution_attributed": False,
    }
    return result
