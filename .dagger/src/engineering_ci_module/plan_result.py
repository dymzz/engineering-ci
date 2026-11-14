"""One materialized Plan result; readers never accept Source or start tools."""

import json

import dagger
from dagger import function, object_type


@object_type
class PlanExecution:
    artifacts: dagger.Directory = dagger.field()

    @function
    async def report(self) -> str:
        """Read the report from this result's immutable artifact Directory."""
        return await self.artifacts.file("report.json").contents()

    @function
    async def enforce(self) -> None:
        """Fail on a non-passed Report, preserving this result's artifacts."""
        from engineering_ci import execution

        try:
            report = json.loads(await self.report())
        except ValueError:
            raise RuntimeError("plan_result_invalid") from None
        if (
            not isinstance(report, dict)
            or report.get("kind") != execution.REPORT_KIND
            or type(report.get("schema_version")) is not int
            or report["schema_version"] != execution.SCHEMA_VERSION
        ):
            raise RuntimeError("plan_result_invalid")
        try:
            original_exit = int(await self.artifacts.file("exit-code.txt").contents())
        except ValueError:
            raise RuntimeError("plan_result_exit_invalid") from None
        if (
            type(report.get("exit_code")) is not int
            or report["exit_code"] != original_exit
            or report.get("status") != "passed"
            or original_exit != 0
        ):
            raise RuntimeError(
                f"Plan {report.get('status', 'unknown')}: "
                f"{report.get('reason') or 'nonzero_or_inconsistent_exit'} "
                f"(exit {original_exit})"
            )


def result(report: dict, artifacts: dagger.Directory) -> PlanExecution:
    """Materialize current facts, never a host pass receipt or evidence admission."""
    from engineering_ci import execution

    report.setdefault("kind", execution.REPORT_KIND)
    report.setdefault("schema_version", execution.SCHEMA_VERSION)
    report.setdefault("exit_code", 3)
    report.setdefault("tests", {"status": "not_executed"})
    report.setdefault("checks", [])
    report.setdefault("prerequisites", [])
    report.setdefault("source_binding_verified", "unknown")
    report.setdefault("plan_declaration_consistent", None)
    report.setdefault("trusted", False)
    bundle = artifacts.with_new_file(
        "report.json", json.dumps(report, ensure_ascii=False, sort_keys=True)
    ).with_new_file("exit-code.txt", str(report["exit_code"]) + "\n")
    return PlanExecution(artifacts=bundle)
