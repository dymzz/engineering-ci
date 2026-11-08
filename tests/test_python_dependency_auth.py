"""Private credentials are available only during dependency installation."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "python_dependency_auth",
    Path(__file__).resolve().parents[1]
    / ".dagger/src/engineering_ci_module/python_dependency_auth.py",
)
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)


class Container:
    def __init__(self, *, fail=False):
        self.calls = []
        self.secrets = {}
        self.fail = fail

    def with_secret_variable(self, name, value):
        self.secrets[name] = value
        self.calls.append(("secret", name))
        return self

    def without_secret_variable(self, name):
        self.secrets.pop(name)
        self.calls.append(("remove", name))
        return self

    def with_exec(self, args):
        self.calls.append(("exec", args, set(self.secrets)))
        if args[0] == "uv" or args[1] == "/tmp/python_dependency_packages.py":
            if self.fail:
                raise RuntimeError("original synchronization failure")
        else:
            assert not self.secrets
        return self


def test_private_secret_is_removed_before_any_probe_or_test():
    container = auth.sync_dependencies(Container(), "3.14", object())
    install, probe = [entry for entry in container.calls if entry[0] == "exec"]
    assert install[2] == {auth.TOKEN_VARIABLE}
    assert probe[2] == set()
    assert "not in os.environ" in probe[1][-1]
    assert not container.secrets
    assert "https://github.com" not in auth.CREDENTIAL_HELPER
    assert "$ENGINEERING_CI_GIT_TOKEN" in auth.CREDENTIAL_HELPER


def test_public_dependencies_do_not_bind_a_credential():
    container = auth.sync_dependencies(Container(), "3.14")
    assert all(entry[0] == "exec" and not entry[2] for entry in container.calls)
    assert len(container.calls) == 1


def test_installation_failure_is_not_converted_to_a_successful_probe():
    container = Container(fail=True)
    with pytest.raises(RuntimeError, match="original synchronization failure"):
        auth.sync_dependencies(container, "3.14", object())
    assert len([entry for entry in container.calls if entry[0] == "exec"]) == 1


def test_local_packages_do_not_bind_or_extract_git_credentials():
    container = auth.sync_dependencies(Container(), "3.14", dependency_packages=True)
    assert not container.secrets
    assert len(container.calls) == 1
    assert container.calls[0][1][-1] == "/tmp/engineering-ci-dependency-packages"
    with pytest.raises(ValueError, match="do not accept Git credentials"):
        auth.sync_dependencies(Container(), "3.14", object(), dependency_packages=True)
