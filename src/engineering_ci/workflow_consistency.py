"""Validate cross-layer contracts and release guards; YAML owns the Workflow DAG."""

from __future__ import annotations

import re
import shlex

from ._paths import ROOT
from .pipeline import PIPELINE


def _workflow(path):
    """Read safe YAML with GitHub boolean rules and reject duplicate keys."""
    try:
        import yaml
    except ImportError as error:
        raise ValueError(
            "Workflow validation requires the locked PyYAML dependency"
        ) from error

    class Loader(yaml.SafeLoader):
        pass

    Loader.yaml_implicit_resolvers = {
        key: [
            (tag, pattern) for tag, pattern in values if tag != "tag:yaml.org,2002:bool"
        ]
        for key, values in Loader.yaml_implicit_resolvers.items()
    }
    Loader.add_implicit_resolver(
        "tag:yaml.org,2002:bool",
        re.compile(r"^(?:true|false)$", re.IGNORECASE),
        list("tTfF"),
    )

    def mapping(loader, node, deep=False):
        loader.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in result:
                raise yaml.constructor.ConstructorError(
                    None, None, "Duplicate key", key_node.start_mark
                )
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=Loader)
    except (yaml.YAMLError, TypeError) as error:
        # Do not print raw YAML snippets, which may contain accidental credentials.
        raise ValueError(f"Invalid or ambiguous Workflow YAML: {path.name}") from error
    _require(isinstance(value, dict), f"Workflow mapping: {path.name}")
    return value


def _require(condition, location):
    if not condition:
        raise ValueError(f"Pipeline / Workflow mismatch: {location}")


def _step(job, name):
    found = [step for step in job.get("steps", []) if step.get("name") == name]
    _require(len(found) == 1, f"exactly one Step named {name}")
    return found[0]


def _step_id(job, identity):
    found = [step for step in job.get("steps", []) if step.get("id") == identity]
    _require(len(found) == 1, f"exactly one Step with id {identity}")
    return found[0]


def _read_only(job, defaults, location):
    """Extra jobs may evolve, but cannot acquire publication write permissions."""
    permissions = job.get("permissions", defaults)
    _require(
        permissions == "read-all"
        or isinstance(permissions, dict)
        and all(value in {"read", "none"} for value in permissions.values()),
        location,
    )


def _artifact_output(job, output, name):
    """Follow a declared output to its upload Step without fixing the Step's label."""
    reference = job.get("outputs", {}).get(output, "")
    match = re.fullmatch(
        r"\$\{\{\s*steps\.([\w-]+)\.outputs\.artifact-id\s*\}\}", reference
    )
    _require(match is not None, f"{name} Artifact output")
    step = _step_id(job, match[1])
    _require(
        step.get("uses", "").startswith("actions/upload-artifact@")
        and step.get("with", {}).get("name") == name,
        f"{name} Artifact producer",
    )


def _artifact_input(job, reference, location):
    found = [
        step
        for step in job.get("steps", [])
        if step.get("uses", "").startswith("actions/download-artifact@")
        and step.get("with", {}).get("artifact-ids") == reference
    ]
    _require(len(found) == 1, location)


def _command(step, expected, location):
    _require(
        "if" not in step
        and "continue-on-error" not in step
        and "uses" not in step
        and shlex.split(step.get("run", "")) == shlex.split(expected),
        location,
    )


def _toolchain(job, *, python=False):
    reader = _step_id(job, "check_toolchain")
    _command(
        reader,
        'python3 -m engineering_ci.workflow_actions checks toolchain --github-output "$GITHUB_OUTPUT"',
        "toolchain reader",
    )
    setup = [
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("astral-sh/setup-uv@")
    ]
    _require(
        len(setup) == 1
        and "if" not in setup[0]
        and "continue-on-error" not in setup[0],
        "required setup-uv",
    )
    _require(
        setup[0].get("with", {}).get("version")
        == "${{ steps.check_toolchain.outputs.uv_version }}",
        "setup-uv must read TOML version",
    )
    if python:
        _require(
            setup[0].get("with", {}).get("python-version")
            == "${{ steps.check_toolchain.outputs.python_version }}",
            "setup-uv must read TOML Python version",
        )
    _require(
        job["steps"].index(reader) < job["steps"].index(setup[0]),
        "toolchain bootstrap order",
    )
    return setup[0]


def validate(*, root=ROOT, config=None, environment=None):
    """Fail before release operations when YAML cannot implement the configured policy."""
    from .release.registry import ARTIFACT_OUTPUTS, CHECKS, RELEASE_DEPENDENCIES

    config = PIPELINE if config is None else config
    release_config = config["delivery"]["release"]
    directory = root / ".github/workflows"
    ci = _workflow(directory / "ci.yml")
    release = _workflow(directory / release_config["workflow"])
    complete = _workflow(directory / "reusable-python-ci.yml")
    try:
        branch = release_config["branch"]
        _require(
            config["profiles"].get("tests") == ["pytest"],
            "CI tests alias must select pytest",
        )
        _require(
            set(ci["on"]) == {"push", "pull_request", "workflow_call"}, "CI triggers"
        )
        for event in ("push", "pull_request"):
            _require(ci["on"][event] == {"branches": [branch]}, f"CI {event} branch")
        _require(
            {"python-ci", "sync-tool-tests", "version-metadata"} <= ci["jobs"].keys(),
            "existing PR Job identities",
        )
        jobs = {}
        for job_id, job in ci["jobs"].items():
            _read_only(job, ci["permissions"], f"CI Job {job_id} permissions")
            name = job.get("name", job_id)
            _require(name not in jobs, f"unique CI Job {name}")
            jobs[name] = job
            if job_id not in {"python-ci", "sync-tool-tests", "version-metadata"}:
                continue
            _require(
                "if" not in job
                and "continue-on-error" not in job
                and "strategy" not in job,
                f"required CI Job {job_id}",
            )
            _toolchain(job, python=job_id != "python-ci")
        groups = {}
        for name, spec in config["checks"].items():
            groups.setdefault((spec["ci_job"], spec["ci_step"]), []).append(name)
        for (job_name, step_name), names in groups.items():
            step = _step(jobs[job_name], step_name)
            if set(names) == {"pytest"}:
                expected = "python3 -m engineering_ci.workflow_actions checks tests --coverage-from-env"
                _require(
                    step.get("env", {}).get("EXPORT_COVERAGE")
                    == "${{ inputs.coverage }}",
                    "core coverage invocation",
                )
            else:
                if set(names) == {"version", "lock"}:
                    names = ["version", "lock"]
                expected = (
                    "python3 -m engineering_ci.workflow_actions checks "
                    + " ".join(names)
                )
            _command(step, expected, f"{job_name} / {step_name} check invocation")
            _require(
                jobs[job_name]["steps"].index(step)
                > jobs[job_name]["steps"].index(_toolchain(jobs[job_name])),
                "check bootstrap order",
            )
        call = ci["on"]["workflow_call"]
        _require(
            call["inputs"]["coverage"]["type"] == "boolean"
            and call["inputs"]["coverage"]["default"] is False,
            "coverage input",
        )
        for output, job in (
            ("core_artifact_id", "sync-tool-tests"),
            ("ruff_artifact_id", "python-ci"),
        ):
            _require(
                call["outputs"][output]["value"]
                == f"${{{{ jobs.{job}.outputs.artifact_id }}}}",
                f"CI {output}",
            )
            _artifact_output(
                ci["jobs"][job],
                "artifact_id",
                "core-tests" if output == "core_artifact_id" else "release-tools-ruff",
            )

        _require(
            release_config["trigger"] == "manual"
            and set(release["on"]) == {"workflow_dispatch"},
            "manual Release trigger",
        )
        inputs = release["on"]["workflow_dispatch"]["inputs"]
        _require(
            set(inputs) == {"version", "publish", "pytest_scenario"},
            "Release input identities",
        )
        _require(
            inputs["version"]["type"] == "string"
            and inputs["version"]["required"] is True,
            "explicit Release version",
        )
        _require(
            inputs["publish"]["type"] == "boolean"
            and inputs["publish"]["default"] is False,
            "explicit publication opt-in",
        )
        _require(
            inputs["pytest_scenario"]["default"] == "pass"
            and inputs["pytest_scenario"]["options"] == ["pass", "assertion"],
            "failure probe input",
        )
        _require(
            release["permissions"] == {"contents": "read", "actions": "read"}
            and ci["permissions"] == {"contents": "read"},
            "read-only validation permissions",
        )
        _require(
            release["concurrency"]["cancel-in-progress"] is False,
            "serial Release operations",
        )
        _require(
            RELEASE_DEPENDENCIES.keys() <= release["jobs"].keys(),
            "existing Release Job identities",
        )
        for job_id, job in release["jobs"].items():
            if job_id != "publish":
                _read_only(job, release["permissions"], f"{job_id} permissions")
        for job_id, needs in RELEASE_DEPENDENCIES.items():
            job = release["jobs"][job_id]
            actual = job.get("needs", [])
            actual = [actual] if isinstance(actual, str) else actual
            _require(
                isinstance(actual, list)
                and all(isinstance(value, str) for value in actual)
                and len(actual) == len(set(actual))
                and needs <= set(actual),
                f"{job_id} dependencies",
            )
            _require(
                "continue-on-error" not in job and "strategy" not in job,
                f"{job_id} failure semantics",
            )
            expected_if = {
                "evidence": "${{ always() && !cancelled() && needs.candidate.result == 'success' }}",
                "verified": "${{ always() && !cancelled() && needs.candidate.result == 'success' }}",
                "publish": "${{ inputs.publish && needs.verified.result == 'success' }}",
            }.get(job_id)
            _require(job.get("if") == expected_if, f"{job_id} admission condition")
            permissions = job.get("permissions", release["permissions"])
            _require(
                permissions
                == {
                    "contents": "write" if job_id == "publish" else "read",
                    "actions": "read",
                },
                f"{job_id} permissions",
            )
        for job_id, workflow in (
            ("core-tests", "ci.yml"),
            ("complete-ci", "reusable-python-ci.yml"),
        ):
            _require(
                release["jobs"][job_id]["uses"] == f"./.github/workflows/{workflow}",
                f"{job_id} candidate-local Workflow",
            )
        for name, (producer, output) in ARTIFACT_OUTPUTS.items():
            reference = f"${{{{ needs.{producer}.outputs.{output} }}}}"
            _artifact_input(
                release["jobs"]["evidence"], reference, f"{name} Artifact input"
            )
        for producer, output, name, consumer in (
            ("evidence", "facts_artifact_id", "release-facts", "verified"),
            ("verified", "manifest_artifact_id", "release-manifest", "publish"),
        ):
            _artifact_output(release["jobs"][producer], output, name)
            _artifact_input(
                release["jobs"][consumer],
                f"${{{{ needs.{producer}.outputs.{output} }}}}",
                f"{name} Artifact input",
            )
        _require(
            release["jobs"]["core-tests"]["with"] == {"coverage": True},
            "Release core coverage",
        )
        policy = release["jobs"]["complete-ci"]["with"]
        _require(
            policy["dependency_security_mode"] == "enforce"
            and policy["verify_module_lock"] is True
            and policy["dependency_security_source_ref"] == "${{ github.sha }}",
            "existing security / module lock policy",
        )
        _require(
            policy["pytest_report_function"] == "fixture-pytest-artifacts"
            and policy["pytest_check"] == "engineering-ci:fixture-test"
            and policy["pytest_report_scenario"] == "${{ inputs.pytest_scenario }}",
            "single pytest report execution",
        )
        for job_id, operation, step_name, suffix in (
            ("candidate", "prepare", CHECKS["candidate"][1], ""),
            (
                "verified",
                "verify",
                "Require every validation job and exact candidate",
                " --directory release-facts --manifest release-manifest.json",
            ),
            (
                "publish",
                "publish",
                "Recheck main and publish immutable tag and evidenced GitHub Release",
                " --manifest release-evidence/release-manifest.json",
            ),
        ):
            job = release["jobs"][job_id]
            setup = _toolchain(job, python=True)
            step = _step(job, step_name)
            _command(
                step,
                f"uv run --locked python -m engineering_ci.workflow_actions release {operation}{suffix}",
                f"{job_id} locked release operation",
            )
            _require(
                job["steps"].index(setup) < job["steps"].index(step),
                f"{job_id} bootstrap order",
            )
            for key, source in (
                ("RELEASE_VERSION", "version"),
                ("RELEASE_PUBLISH", "publish"),
                ("RELEASE_SCENARIO", "pytest_scenario"),
            ):
                _require(
                    step["env"][key] == f"${{{{ inputs.{source} }}}}",
                    f"{job_id} {key} binding",
                )
            if job_id != "candidate":
                _require(
                    step["env"]["RELEASE_NEEDS"] == "${{ toJSON(needs) }}",
                    f"{job_id} original needs evidence",
                )
            else:
                _require(
                    step.get("id") == "prepare"
                    and job["outputs"]["sha"] == "${{ steps.prepare.outputs.sha }}",
                    "candidate SHA output",
                )
            checkouts = [
                step
                for step in job["steps"]
                if step.get("uses", "").startswith("actions/checkout@")
            ]
            ref = (
                "${{ github.sha }}"
                if job_id == "candidate"
                else "${{ needs.candidate.outputs.sha }}"
            )
            _require(
                len(checkouts) == 1 and checkouts[0].get("with", {}).get("ref") == ref,
                f"{job_id} candidate SHA checkout",
            )
        # Reuse the existing Manifest registry, including reusable Job prefixes.
        for job_name, step_name in CHECKS.values():
            if job_name.startswith("core-tests / "):
                job = jobs[job_name.removeprefix("core-tests / ")]
            elif job_name.startswith("complete-ci / "):
                name = job_name.removeprefix("complete-ci / ")
                job = complete["jobs"][name]
            else:
                job = release["jobs"][job_name]
            _step(job, step_name)
        if environment is not None:
            path = f"{environment['GITHUB_REPOSITORY']}/.github/workflows/{release_config['workflow']}"
            _require(
                environment["GITHUB_EVENT_NAME"] == "workflow_dispatch"
                and environment["GITHUB_WORKFLOW_REF"].split("@")[0] == path,
                "executing Release Workflow identity",
            )
            _require(
                environment["GITHUB_WORKFLOW_SHA"] == environment["GITHUB_SHA"],
                "executing Workflow / candidate SHA",
            )
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError(
            "Pipeline / Workflow mismatch: missing or malformed binding"
        ) from error
