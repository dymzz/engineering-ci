"""Record interpreter evidence in the same process that invokes pytest once."""

import json
import platform
import sys
from pathlib import Path

RUNTIME_PATH = Path("/tmp/engineering-ci-pytest-runtime.json")


def runtime_info() -> dict:
    return {
        "schema_version": 1,
        "actual_python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "executable": sys.executable,
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "pytest_started": False,
        "pytest_exit_code": None,
    }


def main(argv=None, destination=RUNTIME_PATH) -> int:
    args = sys.argv[1:] if argv is None else argv
    evidence = runtime_info()
    if args == ["--probe"]:
        print(json.dumps(evidence))
        return 0
    evidence["requested_python"] = args[0]

    def save():
        destination.write_text(json.dumps(evidence), encoding="utf-8")

    if ".".join(map(str, sys.version_info[:2])) != args[0]:
        evidence["error"] = "interpreter_mismatch"
        save()
        return 64
    try:
        import pytest
    except ImportError:
        evidence["error"] = "pytest_unavailable"
        save()
        return 1
    evidence["pytest_started"] = True
    save()
    code = int(pytest.main(args[1:]))
    evidence["pytest_exit_code"] = code
    save()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
