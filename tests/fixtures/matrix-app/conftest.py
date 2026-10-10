import json
import platform
import time
from pathlib import Path


def pytest_sessionstart(session):
    if Path(session.config.rootpath) != Path(__file__).resolve().parent:
        return
    session.matrix_started = time.perf_counter()
    path = Path("/tmp/engineering-ci-pytest-invocations")
    path.write_text(str(int(path.read_text()) + 1) if path.exists() else "1")


def pytest_sessionfinish(session, exitstatus):
    if not hasattr(session, "matrix_started"):
        return
    Path("/tmp/engineering-ci-fixture-timing.json").write_text(json.dumps({
        "actual_python": platform.python_version(), "tests_collected": session.testscollected,
        "pytest_session_seconds": time.perf_counter() - session.matrix_started,
        "exit_code": int(exitstatus),
    }))
