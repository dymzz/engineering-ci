# PR #165 — three local `test_plan_result.py` failures: original logs

This directory is the raw evidence the reviews repeatedly asked for. No isolation
adapter or wrapper was used: both runs are plain `python -m pytest` with only
`--basetemp` redirected.

## Candidates and commands

| Role | SHA | Command |
|---|---|---|
| Candidate | `0ec77a1506ce945ca8fa9a755e6542fa8bc2a8b2` | `python -m pytest tests/test_plan_result.py::test_dagger_plan_execution_producer_artifact_handoff_reaches_resolution tests/test_plan_result.py::test_dagger_plan_execution_lossless_pytest_dot_dir_collision -q -p no:cacheprovider -vv --basetemp=<tmp>` |
| Baseline | `941a2f44888a95eff5371fa80fe48272f5290104` (merge base) | same command, then the two surviving nodeids separately (`baseline-941a2f4-producer.txt`) |

Environment (both): Python 3.14.10, pytest 9.0.3, Windows; candidate used
`engineering_ci` from the PR worktree, baseline from the detached merge-base
worktree (`PYTHONPATH=<baseline>/src`, import path recorded in
`env-baseline.txt`). No tests were skipped in these runs.

## Per-case correspondence

| Case | Candidate | Baseline 941a2f4 | Attribution |
|---|---|---|---|
| `test_dagger_plan_execution_producer_artifact_handoff_reaches_resolution[worktree]` | failed | failed | Same assertion `blocked != passed` with `reason=shellcheck_unprepared`, cause `uv cache\.lock: access denied (os error 5)` — pre-existing environment, not introduced by this PR |
| `test_dagger_plan_execution_producer_artifact_handoff_reaches_resolution[commit]` | failed | failed | Same as above |
| `test_dagger_plan_execution_lossless_pytest_dot_dir_collision` | failed | **absent** (`not found`, exit 4) | Test added by this PR. Failing assertion is on the stub's captured pytest stdout (`assert 'test_dot_dir' in 'F ... [100%]'`): under pytest 9.0.3 the stub run renders only the progress line, so the fixture expectation does not match the tool output. The native exit/summary assertions above it were not reached; this is a PR-added test-fixture defect, not a baseline regression. |

Note: the repository whitespace hook rejects trailing spaces, so trailing
whitespace inside the captured logs was stripped; every other byte is verbatim.

Update: the third case was fixed after this record. Commit e407d5b asserts the bound pytest contract (report/exit/native input refs/argv) instead of the stub stdout rendering, so only the two environment-caused producer failures remain.

Raw files: `candidate-0ec77a1-full.txt` (22,454 bytes, complete `-vv` output),
`baseline-941a2f4-producer.txt` (14,228 bytes), `baseline-941a2f4-lossless-absent.txt`
(collect/exit-4 record), `lossless-failure-excerpt.txt`, and the two env files.
