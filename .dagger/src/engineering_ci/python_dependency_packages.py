"""Assemble explicit wheel inputs while retaining the consumer's locked public deps."""

import hashlib
import json
import os
import re
import subprocess
import tomllib
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile


def normalized_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def wheel_metadata(path):
    with ZipFile(path) as archive:
        entries = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(entries) != 1:
            raise ValueError("Dependency wheel must contain exactly one METADATA")
        metadata = BytesParser().parsebytes(archive.read(entries[0]))
    name, version = metadata["Name"], metadata["Version"]
    if not name or not version or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ValueError("Dependency wheel has invalid distribution identity")
    return {
        "name": normalized_name(name),
        "version": version,
        "file": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def package_inputs(directory, project_name):
    paths = sorted(Path(directory).glob("*.whl"))
    if not paths:
        raise ValueError("Explicit dependency packages contain no wheels")
    records = [wheel_metadata(path) for path in paths]
    names = [record["name"] for record in records]
    if len(names) != len(set(names)) or normalized_name(project_name) in names:
        raise ValueError(
            "Dependency wheels have duplicate or consumer-owned identities"
        )
    return paths, records


def sync_local_dependencies(python_version, directory, *, environment):
    root = Path.cwd()
    configuration = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = configuration["project"]
    groups = configuration.get("dependency-groups", {})
    selected = (
        configuration.get("tool", {})
        .get("uv", {})
        .get("default-groups", ["dev"] if "dev" in groups else [])
    )
    if selected == "all":
        selected = list(groups)
    if "git+" in json.dumps({"project": project, "groups": groups}):
        raise ValueError(
            "Local wheel assembly requires version requirements rather than direct Git requirements"
        )
    paths, records = package_inputs(directory, project["name"])
    constraints = Path("/tmp/engineering-ci-local-constraints.txt")

    def checked(arguments, **kwargs):
        return subprocess.run(arguments, env=environment, check=True, **kwargs)

    export = [
        "uv",
        "export",
        "--frozen",
        "--no-hashes",
        "--no-emit-project",
        "--output-file",
        str(constraints),
    ]
    for record in records:
        export += ["--no-emit-package", record["name"]]
    checked(export, stdout=subprocess.DEVNULL)
    requirements = constraints.read_text(encoding="utf-8")
    if any(
        "git+" in line for line in requirements.splitlines() if not line.startswith("#")
    ):
        raise ValueError("Explicit local wheels do not cover selected Git dependencies")

    sync = [
        "uv",
        "sync",
        "--locked",
        "--refresh",
        "--python",
        python_version,
        "--no-install-project",
    ]
    for record in records:
        sync += ["--no-install-package", record["name"]]
    checked(sync)
    python = str(root / ".venv/bin/python")
    install = [
        "uv",
        "pip",
        "install",
        "--no-sources",
        "--refresh",
        "--python",
        python,
        "--constraint",
        str(constraints),
    ]
    for group in selected:
        install += ["--group", group]
    checked([*install, *(str(path) for path in paths), "."])
    checked(["uv", "pip", "check", "--python", python])
    names = [project["name"], *(record["name"] for record in records)]
    code = (
        "import json; from importlib.metadata import version; "
        f"print(json.dumps({{name: version(name) for name in {names!r}}}))"
    )
    installed = json.loads(
        checked([python, "-I", "-c", code], capture_output=True, text=True).stdout
    )
    for record in records:
        record["installed_version"] = installed[record["name"]]
        if record["installed_version"] != record["version"]:
            raise ValueError("Installed distribution does not match explicit wheel")
    receipt = {
        "schema_version": 1,
        "mode": "local-wheel-packages",
        "packages": records,
        "project": {"name": project["name"], "version": installed[project["name"]]},
        "pyproject_sha256": hashlib.sha256(
            (root / "pyproject.toml").read_bytes()
        ).hexdigest(),
        "lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
    }
    Path("/tmp/engineering-ci-dependency-packages.json").write_text(
        json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    import sys

    sync_local_dependencies(sys.argv[1], sys.argv[2], environment=dict(os.environ))
