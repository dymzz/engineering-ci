"""Provider source, check target and evidence ownership stay separate values."""

import copy
import json
import shutil
import subprocess
from inspect import Parameter, signature
from types import SimpleNamespace

import pytest
from vesryn_tool_runtime.documents import WorkflowError

from engineering_ci import checks, dagger_checks
from engineering_ci.checks_container import OMITTED_INPUTS
from engineering_ci.workflow_adapters import provider
from engineering_ci.workflow_adapters.assets import export as assets_export
from engineering_ci.workflow_adapters.assets import manifest as asset_manifest
from engineering_ci.workflow_adapters.checks import ChecksAdapter, dagger_catalog

TOOLS = {"python": {"version": "3.14.8"}}
JUNIT = (
    '<testsuite tests="1" failures="0" errors="0" skipped="0">'
    '<testcase name="ok"/></testsuite>'
)
# Read the real module pin instead of copying it, so a version bump cannot leave
# these tests asserting a Dagger the repository no longer declares.
ENGINE = dagger_checks.engine_version(checks.ROOT)
MANIFEST = f'engineVersion = "v{ENGINE}"\n'


def drop_executor(kwargs):
    """The adapter always sends its executor; this probe runs the native library."""
    return {key: value for key, value in kwargs.items() if key != "executor"}


def probe_run(seen, calls=None):
    """Answer only the three probes prepare() makes: CLI version, Git modes, Dagger API."""
    environment = {}

    def run(args, **kwargs):
        seen.append(args)
        if calls is not None:
            calls.append((args, kwargs))
        if "version" in args:
            return subprocess.CompletedProcess(
                args, 0, f"dagger v{ENGINE} 1111111\n", ""
            )
        if args[0] == "git":
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, environment_json(), "")

    return run, environment


def environment_json(sources=None):
    return json.dumps(
        {
            "schema_version": 1,
            "environment": {"platform": "Linux"},
            "runtime": {"engine_version": f"v{ENGINE}"},
            "tools": {"pytest": TOOLS},
            "source_inputs": {"pytest": {} if sources is None else sources},
        }
    )


def module(provider_root):
    """Give a synthetic Provider the same Dagger module pin as the real repository."""
    (provider_root / "dagger-module.toml").write_text(MANIFEST, encoding="utf-8")
    (provider_root / "dagger.lock").write_text("# lock\n", encoding="utf-8")
    return provider_root


def dagger_executable(tmp_path):
    """A real file the preflight accepts; every actual probe stays faked."""
    path = tmp_path / "dagger"
    path.write_text("# fake CLI\n", encoding="utf-8")
    return lambda name: str(path)


def declared(root, name="pytest"):
    """A consumer tree that needs nothing from the Provider to be checked."""
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "tests/test_consumer.py").write_text("def test_ok():\n    assert True\n")
    (root / "pyproject.toml").write_text("# consumer project\n", encoding="utf-8")
    (root / "uv.lock").write_text("# consumer lock\n", encoding="utf-8")
    return root


@pytest.fixture
def separation(tmp_path, monkeypatch):
    """A Provider checkout and a consumer that carries none of its configuration."""
    monkeypatch.setattr(checks, "preflight", lambda *args: copy.deepcopy(TOOLS))
    provider_root = tmp_path / "provider"
    target = tmp_path / "consumer"
    owner = tmp_path / "consumer-evidence"
    declared(provider_root).mkdir(parents=True, exist_ok=True)
    declared(target)
    owner.mkdir()
    # Distinct content: the consumer's own file differs from the Provider's.
    (provider_root / "tests/test_consumer.py").write_text("PROVIDER = True\n")
    (target / "tests/test_consumer.py").write_text("CONSUMER = True\n")
    # The Provider supplies the report parser; the consumer does not carry a copy.
    parser = provider_root / ".dagger/src/engineering_ci/pytest_report.py"
    parser.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(checks.ROOT / ".dagger/src/engineering_ci/pytest_report.py", parser)
    return provider_root, target, owner


def recorder(code=0):
    seen = []

    def invoke(args, **kwargs):
        seen.append((args, kwargs))
        if "pytest" in args:
            (kwargs["cwd"] / "core-junit.xml").write_text(JUNIT, encoding="utf-8")
        return subprocess.CompletedProcess(args, code, "", "")

    return invoke, seen


def test_check_target_receives_the_command_and_the_provider_only_supplies_policy(
    separation,
):
    provider_root, target, owner = separation
    process, seen = recorder()

    report, code = checks.execute(
        ["pytest"],
        root=provider_root,
        target=target,
        output_root=owner,
        run=process,
        environ={},
    )

    assert code == 0
    assert [item["name"] for item in report["checks"]] == ["pytest"]
    identity = report["checks"][0]["identity"]
    # The executed tree is the consumer; the Provider only recorded who selected it.
    assert identity["root"] == str(target.resolve())
    assert identity["provider"] == {
        "root": str(provider_root.resolve()),
        "policy_sha256": checks.digest(checks.PIPELINE),
    }
    assert seen and all(kwargs["cwd"] == target.resolve() for _, kwargs in seen)
    assert identity["inputs"] == checks.snapshot(target, "pytest")[0]
    assert identity["inputs"] != checks.snapshot(provider_root, "pytest")[0]


def test_results_belong_to_the_consumer_and_never_reach_the_provider_source(separation):
    provider_root, target, owner = separation
    process, _ = recorder()
    output = owner / "reports/pytest.json"

    checks.execute(
        ["pytest"],
        root=provider_root,
        target=target,
        output_root=owner,
        output=output,
        run=process,
        environ={},
    )

    assert json.loads(output.read_text(encoding="utf-8"))["checks"][0]["identity"][
        "root"
    ] == str(target.resolve())
    assert (target / "core-junit.xml").is_file()
    assert not (provider_root / "core-junit.xml").exists()
    assert not (provider_root / "reports").exists()


def test_single_check_target_keeps_the_existing_single_directory_identity(separation):
    provider_root, _, _ = separation
    process, _ = recorder()

    report, code = checks.execute(
        ["pytest"], root=provider_root, run=process, environ={}
    )

    assert code == 0
    identity = report["checks"][0]["identity"]
    assert identity["root"] == str(provider_root.resolve())
    # Unchanged contract: no second identity appears when no caller separates them.
    assert "provider" not in identity
    assert (provider_root / "core-junit.xml").is_file()


def test_target_needs_no_provider_configuration_at_all(separation):
    """The consumer carries no config/pipeline.toml and no pytest parser copy."""
    provider_root, target, owner = separation
    assert not (target / "config").exists()
    assert not (target / ".dagger").exists()
    process, seen = recorder()

    report, code = checks.execute(
        ["pytest"],
        root=provider_root,
        target=target,
        output_root=owner,
        run=process,
        environ={},
    )

    assert code == 0
    assert report["checks"][0]["status"] == "passed"
    assert seen and all(kwargs["cwd"] == target.resolve() for _, kwargs in seen)


def test_provider_owned_checks_refuse_a_foreign_target(separation):
    """Ruff and version name the Provider's own files, so they have no answer here."""
    provider_root, target, owner = separation
    process, seen = recorder()

    report, code = checks.execute(
        ["ruff-lint"],
        root=provider_root,
        target=target,
        output_root=owner,
        run=process,
        environ={},
    )

    assert code == 3
    assert seen == []
    entry = report["checks"][0]
    assert entry["status"] == "environment_error"
    assert entry["executed"] is False
    assert "Provider-owned paths" in entry["reason"]


def test_a_consumer_copy_of_the_provider_policy_is_not_required_or_consulted(
    separation,
):
    """Copying the Provider's configuration is explicitly not the mechanism."""
    provider_root, target, owner = separation
    (target / "config").mkdir()
    (target / "config/pipeline.toml").write_text(
        'schema_version = 1\n[toolchain]\nuv = "0.0.1"\nruff = "0.0.1"\n'
        'python = "3.9"\n',
        encoding="utf-8",
    )
    process, _ = recorder()

    report, code = checks.execute(
        ["pytest"],
        root=provider_root,
        target=target,
        output_root=owner,
        run=process,
        environ={},
    )

    assert code == 0
    # The Provider's policy selected the check, not the consumer's stray copy.
    assert report["checks"][0]["identity"]["provider"] == {
        "root": str(provider_root.resolve()),
        "policy_sha256": checks.digest(checks.PIPELINE),
    }


def test_dagger_failure_detail_survives_into_the_report(separation, tmp_path):
    """A bare exit code is not a diagnosable cause; keep the real Dagger text."""
    provider_root, target, owner = separation
    module(provider_root)
    # Let preparation and the identity comparison succeed so the run actually
    # reaches the transport failure whose diagnostic this test is about.
    sources = checks.snapshot(target, "pytest", portable=True, omit=OMITTED_INPUTS)[0]

    def run(args, **kwargs):
        if "version" in args:
            return subprocess.CompletedProcess(
                args, 0, f"dagger v{ENGINE} 1111111\n", ""
            )
        if args[0] == "git":
            return subprocess.CompletedProcess(args, 0, "", "")
        if kwargs.get("capture_output"):
            return subprocess.CompletedProcess(args, 0, environment_json(sources), "")
        kwargs["stderr"].write(
            "Error: failed to solve: config/pipeline.toml not found\n"
        )
        kwargs["stderr"].flush()
        return subprocess.CompletedProcess(args, 1, "", "")

    report, code = dagger_checks.execute(
        ["pytest"],
        root=provider_root,
        target=target,
        output_root=owner,
        run=run,
        which=dagger_executable(tmp_path),
        environ={},
    )

    assert code != 0
    assert report["checks"][0]["executed"] is False
    assert "config/pipeline.toml not found" in report["checks"][0]["reason"]


def test_dagger_names_the_capability_argument_not_the_target(separation, tmp_path):
    """The module's own checkout supplies policy and check code, not the consumer."""
    provider_root, target, _ = separation
    module(provider_root)
    seen = []
    run, _ = probe_run(seen)

    _, options, _, _ = dagger_checks.prepare(
        provider_root,
        target,
        ["pytest"],
        {"coverage": False, "junit": "core-junit.xml"},
        run,
        dagger_executable(tmp_path),
        {},
    )

    assert options[options.index("--source") + 1] == str(target.resolve())
    assert options[options.index("--capability") + 1] == "."


def test_non_git_consumer_has_no_git_execute_overrides(tmp_path):
    def run(args, **kwargs):
        return subprocess.CompletedProcess(
            args, 128, "", "fatal: not a git repository (or any parent): .git"
        )

    assert dagger_checks.source_modes(tmp_path, run, {}) == []
    with pytest.raises(checks.PreconditionError, match="Git input modes"):
        dagger_checks.source_modes(tmp_path, run, {"GIT_DIR": "missing"})


def test_git_access_failure_is_not_treated_as_a_non_git_consumer(tmp_path):
    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 128, "", "fatal: access denied")

    with pytest.raises(checks.PreconditionError, match="Git input modes"):
        dagger_checks.source_modes(tmp_path, run, {})


def test_dagger_explicitly_loads_an_installed_module_without_a_git_workspace(
    separation,
    tmp_path,
):
    root, target, _ = separation
    module(root)
    (root / "provider-assets.json").write_text("{}", encoding="utf-8")
    seen = []
    run, _ = probe_run(seen)
    dagger_checks.prepare(
        root,
        target,
        ["pytest"],
        {"coverage": False, "junit": "core-junit.xml"},
        run,
        dagger_executable(tmp_path),
        {},
    )
    command = next(args for args in seen if "api" in args)
    assert command[command.index("--load-module") + 1] == "."


@pytest.mark.parametrize("installed", [False, True])
def test_installed_mounts_keep_provider_code_out_of_consumer_inputs(
    monkeypatch, installed
):
    """Exercise the real mount helper without starting an Engine."""
    import importlib.util
    import sys

    monkeypatch.setitem(sys.modules, "dagger", SimpleNamespace(dag=None))
    spec = importlib.util.spec_from_file_location(
        "provider_mount_test", checks.ROOT / ".dagger/src/engineering_ci/pr_checks.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Container:
        def __init__(self):
            self.mounts = {}

        def with_directory(self, path, directory, **kwargs):
            self.mounts[path] = directory
            return self

        def with_workdir(self, path):
            self.workdir = path
            return self

    consumer, capability = object(), object()
    result = module.mount_sources(Container(), consumer, capability, installed)
    assert result.mounts["/workspace"] is consumer
    assert result.workdir == "/workspace"
    if installed:
        assert result.mounts["/provider"] is capability
    else:
        assert "/provider" not in result.mounts


def test_dagger_loads_the_module_from_the_provider_but_mounts_the_consumer(
    separation, tmp_path
):
    provider_root, target, _ = separation
    module(provider_root)
    seen, calls = [], []
    run, _ = probe_run(seen, calls)

    executable, options, _, _ = dagger_checks.prepare(
        provider_root,
        target,
        ["pytest"],
        {"coverage": False, "junit": "core-junit.xml"},
        run,
        dagger_executable(tmp_path),
        {},
    )

    api = next(args for args in seen if "api" in args)
    index = api.index("--source")
    # The module still resolves from the Provider CWD; only the mounted tree moved.
    assert api[index + 1] == str(target.resolve())
    assert all(
        kwargs.get("cwd") == provider_root for args, kwargs in calls if "api" in args
    )
    # Git input modes describe the tree that is actually mounted.
    assert [kwargs["cwd"] for args, kwargs in calls if args[0] == "git"] == [
        target.resolve()
    ]
    assert options[options.index("--source") + 1] == str(target.resolve())
    assert executable == dagger_executable(tmp_path)("dagger")


def test_dagger_keeps_the_historical_relative_source_when_nothing_is_separated(
    separation, tmp_path
):
    provider_root, _, _ = separation
    module(provider_root)
    seen = []
    run, _ = probe_run(seen)

    dagger_checks.prepare(
        provider_root,
        provider_root,
        ["pytest"],
        {"coverage": False, "junit": "core-junit.xml"},
        run,
        dagger_executable(tmp_path),
        {},
    )

    api = next(args for args in seen if "api" in args)
    assert api[api.index("--source") + 1] == "."


def test_adapter_publishes_the_check_target_and_owns_only_its_output_root(separation):
    provider_root, target, owner = separation
    process, _ = recorder()
    adapter = ChecksAdapter(
        provider_root,
        runner=lambda names, **kwargs: checks.execute(
            names, run=process, environ={}, **drop_executor(kwargs)
        ),
        executor="dagger",
        target=target,
        output_root=owner,
    )
    calls = [
        SimpleNamespace(
            step_id="pytest",
            tool_id="python.pytest",
            inputs={},
            attempt=1,
            previous=None,
        )
    ]

    results = adapter.invoke(
        calls, SimpleNamespace(execution_id="e1", run_dir=owner / "run")
    )

    result = results["pytest"]
    assert result.status == "passed"
    assert result.outputs["check_target"] == str(target.resolve())
    # Evidence paths are relative to the approved output root, never the Provider.
    assert result.outputs["report"]["path"].startswith("run/tool-results/")
    assert result.outputs["report"]["path"].endswith("/checks.json")
    assert not (provider_root / "tool-results").exists()


def test_adapter_rejects_a_run_directory_the_caller_did_not_approve(separation):
    provider_root, target, owner = separation
    elsewhere = owner.parent / "unapproved"
    elsewhere.mkdir()
    adapter = ChecksAdapter(
        provider_root,
        runner=lambda names, **kwargs: checks.execute(names, **kwargs),
        executor="dagger",
        target=target,
        output_root=owner,
    )

    with pytest.raises(Exception, match="approved output root"):
        adapter.invoke(
            [
                SimpleNamespace(
                    step_id="pytest",
                    tool_id="python.pytest",
                    inputs={},
                    attempt=1,
                    previous=None,
                )
            ],
            SimpleNamespace(execution_id="e1", run_dir=elsewhere),
        )


def test_catalog_declares_the_provider_target_and_output_root(separation):
    provider_root, target, owner = separation
    tools = dagger_catalog(provider_root, target=target, output_root=owner)
    spec, adapter = tools.resolve("python.pytest")

    assert adapter.target == target.resolve()
    assert adapter.output_root == owner.resolve()
    assert adapter.root == provider_root.resolve()
    assert spec.source == {
        "kind": "provider",
        "identity": "engineering-ci/dagger/1",
        "interface_sha256": checks.digest(checks.PIPELINE),
    }
    assert "check_target" in spec.output_schema["oneOf"][1]["properties"]


def test_provider_entry_point_defaults_to_the_primary_checkout():
    """The installed entry point still accepts root alone and keeps one identity."""
    source = signature(provider.catalog)
    assert source.parameters["root"].default is Parameter.empty
    for name in ("target", "output_root"):
        assert source.parameters[name].kind is Parameter.KEYWORD_ONLY
        assert source.parameters[name].default is None


def test_provider_transport_follows_the_approved_root_not_the_provider(tmp_path):
    adapter = provider.ProviderAdapter(
        tmp_path, {}, target=tmp_path / "consumer", output_root=tmp_path / "approved"
    )
    assert adapter.root == tmp_path.resolve()
    assert adapter.target == (tmp_path / "consumer").resolve()
    assert adapter.output_root == (tmp_path / "approved").resolve()


def test_installed_transport_uses_the_wheel_not_the_container_asset_copy(
    tmp_path, monkeypatch
):
    root = tmp_path / "assets"
    marker = root / "src/engineering_ci/workflow_adapters/provider.py"
    marker.parent.mkdir(parents=True)
    marker.write_text("# container-only code\n")
    (root / "provider-assets.json").write_text("{}")
    owner = tmp_path / "evidence"
    owner.mkdir()
    monkeypatch.delenv("PYTHONPATH", raising=False)

    def run(command, **kwargs):
        assert "PYTHONPATH" not in kwargs["env"]
        assert kwargs["cwd"] == root
        kwargs["stdout"].write(b'{"schema_version":1}')
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(provider.subprocess, "run", run)
    adapter = provider.ProviderAdapter(root, {}, output_root=owner)
    assert adapter.request("tools") == {"schema_version": 1}


def test_provider_catalog_rejects_a_directory_without_its_own_source(tmp_path):
    with pytest.raises(
        WorkflowError, match="primary source checkout or an unpacked asset directory"
    ):
        provider.catalog(tmp_path)


def asset_source(root):
    """A minimal Provider asset directory: the policy plus the Dagger module."""
    (root / ".dagger/src/engineering_ci").mkdir(parents=True, exist_ok=True)
    (root / ".dagger/src/engineering_ci/main.py").write_text("VALUE = 1\n")
    (root / ".dagger/src/engineering_ci/pr_checks.py").write_text("# module\n")
    (root / ".dagger/sdk/src").mkdir(parents=True, exist_ok=True)
    (root / ".dagger/sdk/src/client.py").write_text("# generated sdk\n")
    (root / ".dagger/pyproject.toml").write_text("# module project\n")
    (root / ".dagger/uv.lock").write_text("# module lock\n")
    (root / "config").mkdir(exist_ok=True)
    (root / "config/pipeline.toml").write_text("# policy\n")
    (root / "src/engineering_ci").mkdir(parents=True)
    (root / "src/engineering_ci/checks_container.py").write_text("# container runner\n")
    for name in ("dagger-module.toml", "dagger.lock", "dagger.toml"):
        (root / name).write_text(f"# {name}\n")
    return root


def test_provider_catalog_accepts_an_unpacked_asset_directory(tmp_path):
    """A consumer supplies assets, not a developer's checkout."""
    assets = tmp_path / "provider-assets"
    document = assets_export(checks.ROOT, assets)

    assert (assets / "config/pipeline.toml").is_file()
    assert (assets / "dagger-module.toml").is_file()
    assert (assets / ".dagger/src/engineering_ci/main.py").is_file()
    assert (assets / "src/engineering_ci/checks_container.py").read_bytes() == (
        checks.ROOT / "src/engineering_ci/checks_container.py"
    ).read_bytes()
    # An install location carries no development state.
    assert not (assets / ".git").exists()
    assert not (assets / "tests").exists()
    assert not (assets / ".venv").exists()
    assert not (assets / ".dagger/.venv").exists()
    assert document["sha256"]
    assert document["files"]["config/pipeline.toml"]
    assert (
        document["entry_points"]["vesryn.adapters"]["engineering-ci-dagger"]
        == "engineering_ci.workflow_adapters.provider:catalog"
    )
    # The unpacked location satisfies the Provider root contract on its own.
    assert provider.provider_root(assets) == assets.resolve()


def test_asset_manifest_detects_changed_bytes(tmp_path):
    source = asset_source(tmp_path / "source")
    before = asset_manifest(source)
    (source / "config/pipeline.toml").write_text("# policy changed\n")

    after = asset_manifest(source)

    assert (
        after["files"]["config/pipeline.toml"]
        != before["files"]["config/pipeline.toml"]
    )
    assert after["sha256"] != before["sha256"]


def test_export_refuses_a_destination_that_already_has_content(tmp_path):
    used = tmp_path / "used"
    used.mkdir()
    (used / "existing.txt").write_text("do not overwrite\n")

    with pytest.raises(Exception, match="new or empty directory"):
        assets_export(asset_source(tmp_path / "source"), used)
