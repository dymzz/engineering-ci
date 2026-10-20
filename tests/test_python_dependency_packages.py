"""Distribution identity contracts; real resolver/install checks run through Dagger."""

import importlib.util
from pathlib import Path
from zipfile import ZipFile

import pytest

spec = importlib.util.spec_from_file_location(
    "python_dependency_packages",
    Path(__file__).resolve().parents[1]
    / ".dagger/src/engineering_ci/python_dependency_packages.py",
)
packages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packages)


def wheel(directory, filename, name, version):
    path = directory / filename
    with ZipFile(path, "w") as archive:
        archive.writestr(
            f"example-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\n",
        )
    return path


def test_explicit_wheel_identity_and_original_bytes_are_recorded(tmp_path):
    import hashlib

    path = wheel(
        tmp_path, "example_package-0.1.2-py3-none-any.whl", "Example_Package", "0.1.2"
    )
    paths, records = packages.package_inputs(tmp_path, "consumer")
    assert paths == [path]
    assert records[0]["name"] == "example-package" and records[0]["version"] == "0.1.2"
    assert records[0]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_missing_and_duplicate_inputs_are_rejected_without_resolution(tmp_path):
    with pytest.raises(ValueError, match="no wheels"):
        packages.package_inputs(tmp_path, "consumer")
    wheel(tmp_path, "example-0.1.0-py3-none-any.whl", "Example", "0.1.0")
    with pytest.raises(ValueError, match="consumer-owned"):
        packages.package_inputs(tmp_path, "example")
    wheel(tmp_path, "example-0.1.2-py3-none-any.whl", "example", "0.1.2")
    with pytest.raises(ValueError, match="duplicate"):
        packages.package_inputs(tmp_path, "consumer")
