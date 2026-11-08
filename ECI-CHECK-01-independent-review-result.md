# ECI-CHECK-01 — Independent Engineering Review Result

## 1. Review scope and input integrity

- Reviewed checkout: `D:\workspace\engineering-ci-eval-B`.
- Starting revision: `97a6c92fbb485692cc3db9e255c5b8113abab6ae` (`git rev-parse HEAD` matches; the implementation is an uncommitted working tree, not a commit sequence after the base).
- `issue-63.md`: SHA-256 `9ea5538829348a26220b34153488c3fae62cf333b9ff273f7a45c62a812db74e` — matches the required LF hash exactly (6,191 bytes, no CR bytes).
- `common-task.md`: read; target revision and input rules match the checkout.
- Changed implementation: 23 modified files plus 5 new source/test files (`.dagger/src/engineering_ci/script_scope.py`, `src/engineering_ci/requirements.py`, `tests/test_release_requirements.py`, `tests/test_required_checks.py`, `tests/test_script_scope.py`). `common-task.md` and `issue-63.md` are supplied inputs and are untracked in the checkout.
- No original CI Run IDs, Jobs, Steps, uploaded Artifacts, JUnit reports, or agent transcript are present in the workspace. `.test-tools/evidence/` contains four PSScriptAnalyzer JSON bundles and one actionlint evidence script, but no captured actionlint stdout and no CI identity.
- No implementation-session transcript, external execution-record location, or evaluation material was used; the user restricted the review to workspace evidence. Stage 2 therefore audits only the workspace records (git state, evidence bundles, file timestamps). This is recorded as a limitation, not filled in by reconstruction.

## 2. Stage 1 — Engineering Review

### 2.1 What changed

The implementation adds:

1. A canonical required-check table (`src/engineering_ci/requirements.py`, 14 requirements) and derives the release check registry, manifest schema expectations and drift tests from it.
2. A native Dagger `workflow_analysis` check that enumerates `.github/workflows`, passes the enumerated files to actionlint once, records `scope/candidates/analysed`, and enforces only a `clean` outcome (`script_scope.py`, `script_analysis.py`, `main.py`).
3. A `windows-2025` `powershell-analysis` job in `ci.yml` that runs the repo's own PSScriptAnalyzer runner over `.dagger/src/engineering_ci,config`, uploads `powershell-analysis` evidence, and is collected by `release.yml` into the release evidence.
4. Coverage semantics for script checks: `clean`, `findings`, `not_applicable`, `not_executed`, `failure`; empty scope and incomplete scan never pass; `enforce` consumes the existing report without a second scanner run.
5. A Dagger execution ledger: `pytest_ci.py` now writes `replaced-check.txt`; the reusable workflow uploads it; `manifest.facts` cross-checks the discovered listing, the surviving `remaining-checks.txt`, and the replaced pytest check, requiring all four `validation:fixture-*` checks to have executed or been replaced.
6. A `requirements` snapshot in the Release Manifest (keys, job, step, scope, Dagger check name, digest), candidate verification against the current set (`current_requirements`), and published-release verification against the manifest's own recorded requirements (`verified_results`).
7. A fifth required artifact `powershell-analysis` in `ARTIFACT_OUTPUTS` / `ARTIFACT_NAMES`, release downloading/ID enforcement, schema and documentation updates.

### 2.2 Initial dual-axis judgment (locked before Stage 2)

- **Implementation Status: `Defective`**
  - Deciding evidence: a confirmed material regression in historical Release evidence validation (finding C1 below). `ARTIFACT_NAMES` now contains five names and `manifest.validate` unconditionally requires a passed manifest to reference the *current* full set. A pre-change-shaped manifest (four artifacts, no `requirements` field, the 12 pre-change checks) is rejected with `ValueError: Passed manifest lacks required artifact references`; restoring the four-artifact set makes the same manifest validate. Consequently `publishing.published_evidence` (`publishing.py:114-155`) and `verification.published` (`verification.py:262-283`) cannot verify previously published Releases, and `publishing.publish_release` cannot resume a pre-change Draft. This directly contradicts the documented/implemented promise that a published Release "stays verifiable after the requirements change" (`publishing.py:140`, `docs/architecture/release/workflow.md:131-133`) and the Q9/Q10 historical-validity requirement. The gate fails closed (no false passing claim), but a central required behavior is invalidated.
  - Confirmed before Stage 2 by code inspection plus an independent reproduction; runtime CI evidence is not needed to establish it.
- **Verification Status: `Partially verified`**
  - Reliable evidence exists for the two scanner paths and several unit-level contracts: reviewer executions of actionlint 1.7.12 and PSScriptAnalyzer 1.9.0 on the intended inputs, targeted test files passing, and synthetic manifest/ledger reproduction.
  - Material gaps remain: the Dagger `workflow_analysis` check and the `windows-2025` job have never run in Dagger/GitHub Actions; no Run/Job/Step/Artifact identity exists; the full pytest suite could not be executed in the review sandbox; the Release artifact flow and Windows module preparation are unexecuted. These gaps are not hidden by the host-level evidence.

### 2.3 Findings

| Stage | Finding | Severity | Evidence status | Locator / engineering impact |
| --- | --- | --- | --- | --- |
| Stage 1 | **C1 — Historical Release evidence is invalidated by the new artifact set.** `ARTIFACT_NAMES` grew from 4 to 5 (`powershell-analysis`), and `manifest.validate` requires `{artifact names} == ARTIFACT_NAMES` for every passed manifest. `published_evidence` calls `validate` first, so a real pre-change manifest (4 artifacts, no `requirements`) is rejected before `verified_results`; `verification.published` reports `unavailable`; a pre-change Draft cannot be promoted. | **Critical** | **Verified** — code inspection plus reproduction: a pre-change-shaped manifest was rejected with `Passed manifest lacks required artifact references`; after restoring the 4-artifact set, the same manifest validated. | `src/engineering_ci/release/registry.py:68-76`; `src/engineering_ci/release/manifest.py:600-601`; `src/engineering_ci/release/publishing.py:114-155`; `src/engineering_ci/release/verification.py:262-283`; contradicted claim at `publishing.py:140` and `docs/architecture/release/workflow.md:131-133`. Impact: historical published/draft evidence cannot be reverified or resumed; the compatibility work for the missing `requirements` field is only half the contract change. |
| Stage 1 | **PSSA failure envelope is misread and the coverage count is emitted before scanning.** `powershell_invoke.ps1` writes an `ok` envelope before analyzing files, then on a per-file exception writes a *second* `error` envelope and exits 3. `powershell_analysis._records` parses only line 1 as the envelope and treats every later line as a diagnostic; `analyse` then raises `the analyzer process exited 3:` with a blank cause. `files_scanned` is a pre-scan file count, not a completed-scan count. | Minor | **Verified** by code inspection and reproduction; the agent's own `ps-empty` artifact shows `limitations: ["the analyzer process exited 3: "]`. | `.dagger/src/engineering_ci/powershell_invoke.ps1:93-121`; `src/engineering_ci/powershell_analysis.py:215-228,286-295,310-328`; `.test-tools/evidence/ps-empty/report.json:12`. Impact: no false pass (exit 3 still becomes a failure), but the failure reason and coverage provenance are lost; the first envelope carries the full pre-scan count even when analysis fails. Reproduced with the baseline `clean.ps1`: second envelope `Analyzing ... clean.ps1 failed: You cannot have more than one dynamic module in each dynamic assembly in this version of the runtime.` |
| Stage 1 | **The documented JSON Lines contract for PSScriptAnalyzer is contradicted by its failure path.** The module docstring says "the first line is the envelope, every following line is one diagnostic", but a per-file failure emits a second envelope after the first. This is the shared root of the previous finding; it is listed separately because it is a claim/evidence contradiction rather than only a code defect. | Minor | **Contradicted** by the same reproduction and by the `ps-empty` artifact. | `src/engineering_ci/powershell_analysis.py:23-25`; `.dagger/src/engineering_ci/powershell_invoke.ps1:93-121`. Impact: the observed contract is only true on success; the failure representation is undocumented and therefore easy for future consumers to misread. |
| Stage 1 | **The new requirement's scope is not bound to the executing Dagger check.** `Requirement.scope` is recorded and digested, but for `workflow_analysis` the actual scope comes from `script_analysis.actionlint_policy()`'s hard-coded default. Changing that default would not change the requirement digest, so a candidate could be treated as covering the old scope. Only the PowerShell scope is machine-checked against the Workflow input (`POWERSHELL_PATHS`). Other scopes (`repository:python`, `repository:tests`, `dagger:validation`, `repository:lock`) are similarly declarative. | Minor | **Verified** by code inspection; current values are consistent (reviewer actionlint enumerated exactly the three `.github/workflows` files). | `src/engineering_ci/requirements.py:80-104`; `.dagger/src/engineering_ci/script_analysis.py:118-130`; `tests/test_required_checks.py:223-250` (PowerShell-only binding). Impact: the "single source" propagation claim is weaker than documented for the newly required check; a future scope change can bypass the digest/delta mechanism. |
| Stage 1 | **Published/legacy validation accepts a self-declared subset of required checks.** For manifests without `requirements`, `effective_requirements` derives the required keys from the manifest's own `checks`, and `published_evidence` performs no current-requirements comparison. A legacy manifest with a check removed (or truncated `requirements.checks`) validates and passes published evidence as long as the remaining records and the artifact set are intact. The candidate path is protected by `current_requirements`. | Minor | **Verified** by reproduction: a manifest with `requirements` removed and `powershell_analysis` removed from `checks` validated (with the current 5-artifact set). | `src/engineering_ci/release/manifest.py:301-322,459-460,522-526`; `src/engineering_ci/release/publishing.py:114-155`; `src/engineering_ci/release/verification.py:145-165`. Impact: historical compatibility and truncation are indistinguishable; no false *check* pass, but a published manifest can understate its original requirement set. |
| Stage 1 | **The compatibility test does not model a pre-change manifest.** `test_a_published_manifest_is_verified_against_its_own_recorded_sources` removes only `requirements` and keeps the new five-artifact set (including `powershell-analysis`), so it passes while a real four-artifact manifest fails. | Minor | **Verified** by inspection and by the C1 reproduction. | `tests/test_release_requirements.py:113-123`. Impact: the test provides false assurance for the exact historical-compatibility claim it names. |
| Stage 1 | **Documentation and deferral text are stale after adding the required checks.** `docs/configuration/dagger.md:104` still says five default Checks; `docs/architecture/release/workflow.md:13` omits Workflow analysis from the PR check list and `:17` still says five native Dagger Checks; `main.py:491-492` says PowerShell is analysed by the `reusable-python-ci.yml` runner, while this repository's required job is now in `ci.yml`. | Minor | **Verified** by inspection. | `docs/configuration/dagger.md:104`; `docs/architecture/release/workflow.md:13,17`; `.dagger/src/engineering_ci/main.py:488-493`. Impact: inaccurate propagation guidance to maintainers/consumers; the deferral reason misidentifies the executing Workflow. |
| Stage 1 | **Two test weaknesses reduce the assurance of the new contracts.** `test_no_matching_scripts_is_not_executed_rather_than_clean` does not call `analyse`; it inspects the function source for the substring `not_executed` and asserts a hard-coded dictionary, so it does not verify the new `not_applicable` branch. `test_a_required_dagger_check_exists_and_is_selected` whitelists `ruff_lint`, `ruff_format`, `core_tests`, `version_integrity`, so its key-to-selected-check assertion is vacuous for those requirements. | Minor | **Verified** by inspection; reviewer reproduced the empty-scope path directly (`not_applicable`, enforce exit 1). | `tests/test_script_analysis.py:405-416`; `tests/test_required_checks.py:192-214`. Impact: the empty-scope and key-binding claims are not actually covered by those tests. |
| Stage 1/2 | **The central new runtime paths have never executed.** No Dagger container ran `workflow_analysis`; no `windows-2025` job ran `Analyse PowerShell scripts`; the Release artifact flow (five IDs, `manifest facts`, requirements snapshot, published path) has no Run/Job/Step/Artifact evidence. Only host-level actionlint/PSScriptAnalyzer runs and mocked unit tests exist. | Major (verification gap, not a confirmed defect) | **Unverified** — explicitly stated in `script_analysis.py` and `powershell_analysis.py` docstrings ("Still NOT VERIFIED"), and confirmed by the absence of any CI identity in the workspace. | `.dagger/src/engineering_ci/main.py:428-448`; `.github/workflows/ci.yml:105-164`; `.github/workflows/release.yml:96-131`; `src/engineering_ci/release/manifest.py:344-370`. Impact: environment-dependent correctness (Dagger module load, Windows runner label, `uv run --python 3.14`, PSGallery preparation, artifact upload/collection) is not established. |
| Stage 2 | **Original execution evidence is incomplete and not reproducible from the repository.** `.test-tools/evidence/workflow_analysis_evidence.py` is a reproducer, not a captured result; the PSSA JSON bundles are original but have no command log or CI identity; no JUnit/test output or agent transcript is retained. The final edits (`docs/architecture/testing/script-analysis.md` 16:52:15, `reusable-python-ci.yml` 16:53:05, `test_required_checks.py` 16:53:25) post-date the last evidence artifact (16:52:08). | Minor (process/evidence quality) | **Verified** from file timestamps and artifact content; original actionlint stdout is absent, while reviewer reruns reproduce the same results. | `.test-tools/evidence/`; file modification times; `.test-tools/evidence/ps-empty/report.json`. Impact: the final state cannot be traced to a complete test/CI run; Stage 2 cannot quantify or authenticate the original execution. |

## 3. Coverage and architecture

### 3.1 Issue questions

- **Resolved in substance:**
  - Q1: a canonical requirement table plus registry/manifest/schema/drift-test derivation exists; the Workflow is bound by tests rather than duplicated manually.
  - Q2: scope enumeration, `coverage.scope/candidates/analysed`, five outcomes, and fail-closed `not_applicable`/`not_executed` are implemented for actionlint and the Windows runner; reviewer reproductions confirmed clean/findings/empty/invalid paths.
  - Q3: `workflow_analysis` reuses `_run_actionlint` and `script.enforce`; one execution and one classifier decide the result.
  - Q4: a `windows-2025` job exists in `ci.yml`, uses the repository's own helper, and is reached by the Release through `core-tests`; no duplicate actionlint/PSScriptAnalyzer execution was introduced in Linux Dagger.
  - Q5: step-level check records plus the Dagger execution ledger now require all four `validation:fixture-*` checks, not just the two policy fixtures.
  - Q6: artifact IDs are required before evidence use; an artifact-delivery failure leaves the execution record but makes `checks.evidence` unavailable and fails the Manifest/Release.
  - Q7: the three-layer boundary (Dagger result / Actions Job / server-side Required Status) is documented; no repository-administration change is part of this work (server-side settings are not verifiable from the workspace).
  - Q8-Q10 (partly): the Manifest records the requirement set it was verified against; candidate verification compares it with the current set; published verification compares recorded check results with the original Actions results.
  - Q11-Q13 (partly): `registry.py`, `manifest.py`, the schema, Workflow YAML and tests were updated together; shared enumeration/classification was factored in `script_scope.py`/`script_analysis.py`, while the Windows runner keeps a separate execution path.

- **Missed or uncertain:**
  - **Historical artifact contract**: the Manifest records *requirements* but not the *artifact contract* in force when it was written. Adding `powershell-analysis` to the current set therefore invalidates every pre-change manifest (C1). This is the primary Q9/Q10 failure: the implementation distinguishes a current requirement set from an old one, but does not preserve the old artifact identity.
  - **Scope propagation for Dagger checks**: requirement scopes are metadata, not bindings to the executing check's defaults; only PowerShell is bound to a Workflow input.
  - **Public reusable path**: the reusable PowerShell job remains opt-in and pins `powershell_source_ref` (default `v0.4.7`), so the repository's new coverage semantics are not automatically delivered to reusable-workflow consumers. The repository's own required check uses `ci.yml`, which is consistent, but the public contract deserves an explicit decision.
  - **Negative paths in CI**: the real actionlint broken-fixture check (`fixture_workflow_analysis`) is skipped by `dagger.toml` and is not selected by PR or Release; the real PSSA negative fixture is only host evidence. Unit tests use canned diagnostics. There is no CI execution proving the required checks fail on findings.
  - **Deferred/legacy Dagger PowerShell policy** remains in `.dagger` (image, classifier, `deferred_report`) while the actual execution is the Windows module; the deferral message still names the reusable workflow.

### 3.2 Dependency and contract inconsistencies

1. `requirements.py` is called the single source, but the executed Dagger check set (`DAGGER_EXECUTION_CHECKS`) is a separate hard-coded tuple in `registry.py` (guarded by a drift test), and the new `workflow_analysis` coverage is a second hard-coded default in `script_analysis.py`.
2. The Manifest's historical-compatibility logic covers the `requirements` field but not `ARTIFACT_NAMES`; the schema and code both assume the current five-artifact set.
3. `validate` accepts `requirements`-less manifests and derives their required keys from their own checks; this is intentional for old assets but weakens the published-path completeness guarantee (see finding 4).
4. The Workflow step that produces PowerShell evidence and the `powershell-analysis` requirement are bound by a test that only checks `POWERSHELL_PATHS`, not that the step actually passes that variable to the runner; the current code does pass it.

## 4. Verification assessment

### 4.1 Original evidence in the workspace

- Four PSScriptAnalyzer bundles under `.test-tools/evidence/`: clean scope (2/2), `ps-negative` (1/1, 3 rule violations), `ps-empty` (failure with blank cause), `ps-fixtures` (3/3, 3 rule violations). They are original tool outputs, not CI artifacts; provenance is file timestamp only.
- `.test-tools/evidence/workflow_analysis_evidence.py`: a reproducer script for the Dagger sequence; no captured stdout, no CI identity.
- No JUnit, pytest cache content (inaccessible), agent transcript, Run ID, Job/Step record or Artifact identity.
- `.ruff_cache` timestamps show Ruff activity around 16:42 and 16:52-16:54; they do not establish a test run.

### 4.2 Reviewer reproductions (new evidence, not original execution)

- **actionlint 1.7.12** (host binary; same pin as `ACTIONLINT_VERSION`):
  - repository workflows: 3 candidates / 3 analysed / clean;
  - committed broken fixture: 1/1 / 2 findings (`syntax-check` line 12, `expression` line 16);
  - empty scope: `not_applicable` / enforce rejected;
  - missing path: exit 3 with `could not read ...`;
  - directory argument on Windows: exit 3 (`Incorrect function`), confirming why enumeration is needed.
- **PSScriptAnalyzer 1.9.0** (module copy under `.test-tools/pssa`, resolved through PSModulePath):
  - `.dagger/src/engineering_ci,config`: 2/2 clean, enforce exit 0;
  - fixture scripts: 3/3 findings (`PSAvoidUsingWriteHost`, `PSUseDeclaredVarsMoreThanAssignments`, `PSAvoidUsingCmdletAliases`), enforce exit 1;
  - empty directory: `not_applicable`, enforce exit 1;
  - non-script explicit path and missing path: failure bundle, exit 1;
  - the multi-value `-Path` fix was exercised with three file paths in one invocation.
- **Manifest/ledger**:
  - `manifest.facts` with a synthetic valid ledger returns the four checks;
  - a missing `fixture-failure-report`, a mismatched remaining list, and an unknown replaced check are rejected;
  - a pre-change-shaped Manifest (no `requirements`, 12 checks, 4 artifacts) is rejected by `validate`; restoring the 4-artifact contract makes it validate (C1);
  - a legacy manifest with a check removed validates on the published path (finding 4).
- **Targeted pytest** (workspace `.venv`, Python 3.14.8, pytest 9.0.3):
  - `tests/test_release_manifest.py`: 43 passed, 4 setup errors (the tmpdir plugin was disabled to work around the sandbox's 0o700 temporary-directory behavior);
  - `tests/test_required_checks.py` + `tests/test_release_requirements.py`: 39 passed;
  - `tests/test_script_analysis.py` excluding the two tempfile classes: 30 passed, 35 subtests passed;
  - `tests/test_release_verification.py` without `tmpdir`: 27 passed, 3 setup errors (same tmpdir workaround);
  - `tests/test_workflow_yaml.py`: 6 passed, 9 subtests passed;
  - `tests/test_ci_performance.py`: 6 passed, 8 skipped.
  - Full-suite execution is not usable in this review environment: pytest's temporary directories become inaccessible to the sandbox token, producing setup/teardown `PermissionError`s unrelated to the code. These are environment errors, not passing/failing test evidence.
- **Static checks**: `ruff check .` passed; `ruff format --check` reported the 18 explicitly named changed Python files as already formatted; `git diff --check` clean.
- **Not runnable in this environment**: Dagger (`docker` pipe access denied), the Windows runner job, real GitHub API/Release flows, PSGallery download on a hosted runner.

### 4.3 Authentic native execution vs simulated checks

- Authentic native execution exists for actionlint and PSScriptAnalyzer **on the review/author host**, not in Dagger or GitHub Actions. The PSSA JSON bundles are original; the actionlint evidence is a reproducer plus transcribed text.
- The unit tests use canned scanner outputs; they establish parser/classifier behavior, not that the real Dagger/Windows paths ran.
- Release tests use a mocked GitHub API; they establish manifest logic but not an original Release Run.
- The reviewer reproductions above are clearly new evidence and do not replace the missing original CI execution.

## 5. Stage 2 — Execution evidence audit

### 5.1 Process observations

- Workspace files indicate checkout at 15:52:06, substantive source/schema edits from 16:26 to 16:36, further manifest/tests/docs edits 16:42-16:45, host evidence 16:46-16:52, fixture edits 16:48 and 16:51, final doc/test/workflow edits 16:52-16:53, and Ruff cache writes at 16:54:14. This is ordering evidence only; elapsed time and token usage are `unknown` (no measured records, not reconstructed).
- One visible rework cycle: the `ps-empty` bundle at 16:47:40 records a PSScriptAnalyzer failure (`the analyzer process exited 3:` with no cause) while analyzing the old `clean.ps1`; the fixtures were then edited at 16:48:40 and 16:51:44 and re-run at 16:52:08 with three candidates and the expected findings. That validates the fixture correction but not the consumer's blank-error contract.
- Evidence reuse: the PSSA bundles are reused in the documentation and tests; the actionlint observations were transcribed into `tests/test_script_analysis.py` and the module docstring. No CI result was available to reuse.
- The final edits (`docs/architecture/testing/script-analysis.md` 16:52:15, `reusable-python-ci.yml` 16:53:05, `test_required_checks.py` 16:53:25) post-date the last evidence artifact; no retained test output demonstrates a post-edit test run, and the final-state test/CI claim cannot be traced from the workspace.
- There is no agent trajectory, no shell history, no raw actionlint stdout, no `dagger` execution log, no Windows job log, and no Run/Artifact identity. Resource metrics (time/tokens), exploration/retry counts, and CI durations are `unknown`.

### 5.2 Stage 2 supplemental findings and status adjustment

- **No new engineering defect** beyond the Stage 1 findings. The Stage 2 records corroborate finding 2 (the `ps-empty` artifact is a direct instance of the blank failure reason and the pre-scan envelope contract) and the evidence limitations in the final two findings.
- The compatibility test's false assurance (finding 5) is corroborated: the only "legacy" test keeps the new five-artifact set, so the C1 regression is invisible to the suite.
- Status adjustment: **none**. Stage 1 already recorded `Defective` (C1) and `Partially verified`; Stage 2 did not invalidate or upgrade those conclusions. The absence of original CI/trajectory evidence does not change the confirmed C1 defect.

## 6. Outstanding limitations

1. **No CI/Dagger/Windows execution evidence**: a real PR Run and a real manual Release Run are needed to establish the Dagger `workflow_analysis` check, the `windows-2025` analysis, PowerShell module preparation, artifact upload/collection, `manifest facts`, the requirements snapshot and the published path. Run/Job/Step IDs and Artifact identities would resolve the largest verification gap.
2. **No original agent trajectory or measured resources**: Stage 2 cannot authenticate tool invocations, exploration/retry history, time or token usage; these remain `unknown`.
3. **Full pytest suite**: blocked by the review sandbox's temporary-directory ACL behavior; the targeted files pass, but the full-suite state of the final tree is not independently established here.
4. **Historical evidence**: the C1 reproduction uses a synthetic pre-change manifest shape; an actual v0.7.0 Release Manifest would make the impact concrete. The fix (or an explicit decision to invalidate old evidence) is a design question for the implementation, not part of this review.
5. **Published-path completeness**: the deliberate legacy-subset acceptance (finding 4) needs an explicit threat-model decision; the current tests do not distinguish a genuine old manifest from a truncated one.
6. **PSGallery/`uv` behavior on a hosted Windows runner**: the runner has never prepared the pinned analyzer, and the required check has no persistent module cache; reliability remains unverified even though the code fails closed.

## 7. Final status

| Axis | Stage 1 | Post-Stage 2 |
| --- | --- | --- |
| Implementation Status | `Defective` — confirmed historical Release evidence regression (C1) | `Defective` (unchanged) |
| Verification Status | `Partially verified` — host-level scanner/unit evidence, but no Dagger/Windows/Release execution | `Partially verified` (unchanged) |

The critical blocker for any "sound" or "complete" conclusion is C1: a re-verification of an existing published Release or a pre-change Draft fails at `manifest.validate` because the current artifact set is required unconditionally, despite the new compatibility path for the `requirements` field. The remaining findings are minor engineering/verification-quality issues; the unverified CI/Dagger/Windows execution is the principal evidence gap.
