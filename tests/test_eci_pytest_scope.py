"""Exercise the repository policy through actual pytest subprocesses."""

import os
import shutil
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path

import pytest

from engineering_ci import selection, verify_report


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "checkout"
    (root / "src" / "engineering_ci").mkdir(parents=True)
    tests = root / "tests"
    tests.mkdir()
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
        'markers = ["quick: one selected test"]\n',
        encoding="utf-8",
    )
    plugin_dir = Path(__file__).parent
    for name in ("conftest.py", "_eci_pytest_scope.py", "_eci_pytest_report.py"):
        shutil.copyfile(plugin_dir / name, tests / name)
    (tests / "test_first.py").write_text(
        "import os\nfrom pathlib import Path\nimport pytest\n"
        "@pytest.mark.quick\ndef test_first():\n"
        '    assert "ECI_PYTEST_REPORT_DIRECTORY" not in os.environ\n'
        '    Path("first-ran").touch()\n',
        encoding="utf-8",
    )
    (tests / "test_second.py").write_text(
        "from pathlib import Path\nimport os\n"
        'def test_second():\n    Path("second-ran").touch()\n'
        '    assert os.environ.get("FAIL_SECOND") is None\n',
        encoding="utf-8",
    )
    return root


def _run(root, *args, **overrides):
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "PYTEST_ADDOPTS",
            "ECI_PYTEST_NO_AUTO_REPORT",
            "ECI_PYTEST_REPORT_DIRECTORY",
        }
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *args],
        cwd=root,
        env={**env, **overrides},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


@pytest.mark.parametrize(
    "args",
    [
        (),
        (".",),
        ("./tests",),
        ("--ff",),
        ("--lf",),
        ("-k", "test"),
        ("-m", "quick or not quick"),
    ],
)
def test_broad_or_effectively_full_run_requires_explicit_intent(checkout, args):
    result = _run(checkout, *args)
    assert result.returncode == 4, result.stdout + result.stderr
    assert "--full-suite" in result.stderr
    assert not list(checkout.glob("*-ran"))


@pytest.mark.parametrize(
    "args",
    [
        ("tests/test_first.py",),
        ("tests/test_first.py::test_first",),
        ("-k", "test_first"),
        ("-m", "quick"),
    ],
)
def test_native_explicit_or_effective_selection_is_kept(checkout, args):
    result = _run(checkout, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (checkout / "first-ran").is_file()
    assert not (checkout / "second-ran").exists()


def test_collect_only_and_empty_native_selection_do_not_run_tests(checkout):
    assert _run(checkout, "--collect-only").returncode == 0
    assert _run(checkout, "-k", "no_such_test").returncode == 5
    assert not list(checkout.glob("*-ran"))


def test_native_last_failed_selection_and_full_intent(checkout):
    fail = _run(checkout, "--full-suite", FAIL_SECOND="1")
    assert fail.returncode == 1, fail.stdout + fail.stderr
    assert (checkout / "first-ran").is_file()
    (checkout / "first-ran").unlink()
    result = _run(checkout, "--lf")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (checkout / "first-ran").exists()
    assert (checkout / "second-ran").is_file()


def test_host_plan_only_forwards_full_intent_to_repository_pytest(checkout, tmp_path):
    capture = {}
    assert (
        selection.run_tests(
            ["tests"],
            root=checkout,
            framework="pytest",
            full_suite=True,
            capture=capture,
        )
        == 0
    ), capture
    assert "--full-suite" in capture["argv"]
    assert (checkout / "second-ran").exists()
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    (consumer / "test_other.py").write_text(
        "def test_other(): pass\n", encoding="utf-8"
    )
    capture = {}
    assert (
        selection.run_tests(
            ["test_other.py"],
            root=consumer,
            framework="pytest",
            full_suite=True,
            capture=capture,
        )
        == 0
    ), capture
    assert "--full-suite" not in capture["argv"]


def test_exported_invocation_remains_queryable_after_relocation(checkout, tmp_path):
    original = tmp_path / "original"
    result = _run(
        checkout, "tests/test_first.py", ECI_PYTEST_REPORT_DIRECTORY=str(original)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    moved = tmp_path / "moved"
    original.rename(moved)
    report = verify_report.build_verify_report(
        pytest_invocations=[moved], test_records=True
    )
    sources = {source["source_type"]: source for source in report["sources"]}
    assert sources["pytest_invocation"]["declared_artifact_status"] == {
        "report_log": "loaded",
        "junit_xml": "loaded",
    }
    assert sources["pytest_reportlog"]["phase_reconciliation"]["status"] == "matched"


@pytest.mark.skipif(
    find_spec("xdist") is None, reason="optional pytest-experiments group"
)
def test_xdist_explicit_target_keeps_one_controller_report(checkout):
    refused = _run(checkout, "-k", "test_first", "-n", "2")
    assert refused.returncode == 4, refused.stdout + refused.stderr
    assert not list(checkout.glob("*-ran"))
    output = checkout / "xdist-result"
    result = _run(
        checkout,
        "tests/test_first.py",
        "-n",
        "2",
        ECI_PYTEST_REPORT_DIRECTORY=str(output),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (checkout / "first-ran").exists()
    assert not (checkout / "second-ran").exists()
    log = (output / "pytest-reportlog.jsonl").read_text(encoding="utf-8")
    assert log.count('"$report_type": "SessionFinish"') == 1


@pytest.mark.skipif(
    find_spec("testmon") is None, reason="optional pytest-experiments group"
)
def test_testmon_cold_database_cannot_run_an_implicit_full_suite(checkout):
    database = checkout / ".artifacts" / "testmondata"
    database.parent.mkdir()
    result = _run(checkout, "--testmon", TESTMON_DATAFILE=str(database))
    assert result.returncode == 4, result.stdout + result.stderr
    assert not list(checkout.glob("*-ran"))
    result = _run(
        checkout,
        "tests/test_first.py",
        "--testmon-noselect",
        TESTMON_DATAFILE=str(database),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (checkout / "first-ran").exists()
    assert not (checkout / "second-ran").exists()
