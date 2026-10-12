"""Analyze a fixed projection locally; never call models or execute candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from ..mining.analysis import analyze
from ..tracing.store import ArchiveError
from .trace import write_new_file


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "projection", help="Content-free JSON produced by trace project"
    )
    parser.add_argument("--out", required=True, help="New analysis result file")
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args(argv)
    try:
        raw = Path(options.projection).read_bytes()
        projection = json.loads(raw)
        # Keep third-party warnings separate from the single JSON operation result.
        with redirect_stdout(sys.stderr):
            result = analyze(
                projection,
                hashlib.sha256(raw).hexdigest(),
                allow_incomplete=options.allow_incomplete,
            )
        write_new_file(
            Path(options.out), [json.dumps(result, sort_keys=True).encode() + b"\n"]
        )
        value = {
            "schema_version": 1,
            "operation": "mine",
            "status": result["status"],
            "exit_code": 0 if result["status"] == "passed" else 3,
            "analysis_id": result["analysis_id"],
            "cases": len(result["selected_cases"]),
            "excluded_cases": len(result["excluded_cases"]),
            "variants": len(result["variants"]),
            "llm_used": False,
            "output": str(Path(options.out).resolve()),
        }
    except ArchiveError as error:
        value = {
            "schema_version": 1,
            "operation": "mine",
            "status": error.status,
            "reason": str(error),
            "exit_code": 2,
        }
    except ImportError:
        value = {
            "schema_version": 1,
            "operation": "mine",
            "status": "missing_tool",
            "reason": "Install the mining extra to enable PM4Py",
            "exit_code": 2,
        }
    except (OSError, ValueError, TypeError, RecursionError):
        value = {
            "schema_version": 1,
            "operation": "mine",
            "status": "invalid_input",
            "reason": "Cannot read, validate or write the requested analysis data",
            "exit_code": 2,
        }
    print(
        json.dumps(value, sort_keys=True)
        if options.json
        else f"Workflow mining: {json.dumps(value, sort_keys=True)}"
    )
    return value["exit_code"]
