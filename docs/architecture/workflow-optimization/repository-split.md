# Vesryn Repository Extraction

## Scope and delivery

This change prepares engineering-ci v0.6.6 and Vesryn v0.1.0 as separate repositories.
The two authorized checkouts are `D:\workspace\engineering-ci` and `D:\workspace\vesryn`.
Vesryn is an independent project, not a worktree or staging copy of engineering-ci.

The current goal is an independently installable Harness and a pinned host adapter.
Agent/user interaction continues to drive development. Automatic optimization loops,
Contextual Bandit, policy learning and automatic next-round execution are not implemented.

## Ownership

| Responsibility | Authoritative repository |
| --- | --- |
| ToolSpec, Catalog, Workflow definition, Runtime and recovery | Vesryn |
| Generic CapabilityStore and domain-supplied admission | Vesryn |
| Raw archive, CloudEvents, projection, bounded retrieval, optional OTLP | Vesryn |
| Offline Rust4PM analysis and review-only observed candidates | Vesryn |
| Checks and current-input reuse validation | engineering-ci |
| PR reuse binding, comparison and strict CI approval/feedback contracts | engineering-ci |
| CI feedback summary and review against domain evidence | engineering-ci |
| Dagger binding, GitHub Run downloads and Release delivery | engineering-ci |

`workflow_core` currently has independent imports but also legacy CI-specific approval
and feedback Schema. The extraction separates the strict domain contract from the
portable lifecycle. The host keeps the original narrow validation and supplies it to
Vesryn; a generic verification scope does not weaken the host's quality gates.

Portable storage accepts a host feedback validator. It does not infer successful CI,
permission, current input validity or optimization benefit from an archived document.

## Dependency and bootstrap contract

Vesryn has no Python import/build dependency on `engineering_ci`. Its CI consumes the
released engineering-ci v0.6.5 implementation, pinned to
`804d791c60d34468166b6910c88fbc8149fdbf26`.

The consumer entry is `.github/workflows/reusable-python-ci.yml`, with a Vesryn-owned
Dagger module. engineering-ci's project-specific `ci.yml` cannot be used as a generic
consumer Workflow: it invokes helpers from the caller's `engineering_ci` source tree.

engineering-ci uses an explicit Harness extra and Vesryn v0.1.0 at immutable commit
`0dfc8f5a695f647651485d90943121314fef179e`,
including the package version in its lockfile. Basic checks/download/release must remain
usable without the Harness installed. Initial private-repository bootstrapping uses a
reviewable candidate commit, not a falsely claimed published Vesryn release. Formal
publication remains human initiated; later dependency upgrades use verified fixed versions.
The engineering-ci CLI continues to operate from its source checkout and shared TOML;
this extraction does not turn its wheel into a standalone distribution of repository
configuration. Vesryn's installed core/CLI has its own explicit project/config selection.

Both private repositories require Actions credentials that can read their pinned private
sources. `ENGINEERING_CI_READ_TOKEN` is the existing reusable Workflow contract. Credentials
remain outside source, TOML, generated documents and verification reports.
Local Dagger checks use the existing gh credential through a Secret only during the
locked dependency installation. The SDK removes the secret variable before probes/tests;
a runtime assertion checks that removal. An unapproved source URL or mutable revision
is rejected before looking up or forwarding credentials.

For review, merge the Vesryn core before completing the host dependency upgrade. The host
currently pins the reviewed candidate SHA. If squash/rebase produces a new merged SHA,
update that immutable pin and uv.lock in the same open host PR and let its normal CI
validate the resulting dependency. Do not point the host at a mutable branch or claim the
candidate is a published release. The existing Release Security Gate still rejects this
private Git source as incomplete; source-scanning/distribution approval is a later decision.

## Compatibility and evidence

The migration must preserve existing Tool IDs, definition/approval/feedback identities,
original bytes and OTel/XES attribute names. It must not bulk-rewrite historical documents.
SQL migrations 001–003, archive schema version 3 and the historical ownership marker
`engineering-ci.trace` remain stable. Vesryn becomes the single migration implementation;
the host delegates. Import/install/query must not initialize or upgrade a database.

Use an explicit namespace/configuration for each target. New Vesryn configuration uses
`vesryn_trace`; existing archives retain their actual schema. Compatibility configuration
from engineering-ci remains anchored to the engineering-ci checkout. Installed Vesryn
resolves explicit configuration or caller-local configuration independently of source layout.

The existing engineering-ci commands remain compatibility entry points. Installed adapters
are trusted package registrations; generated recipes still use Tool IDs, not Python imports
or arbitrary shell. A host adapter must be explicitly selected; no implicit installation,
activation, approval or capability mutation is introduced.

## Extraction acceptance

1. Install a built Vesryn wheel outside the source import path. Import/run the core and
   verify packaged SQL without importing engineering-ci or optional tracing SDKs.
2. Run Vesryn's portable tests in its own environment, including failure, interruption,
   recovery and independent domain capability lifecycle.
3. Bind engineering-ci to an immutable Vesryn revision; verify compatibility imports,
   strict domain approval/feedback and old serialized evidence.
4. Verify the base engineering-ci installation without Vesryn and the Harness installation
   with Vesryn separately. Missing Harness must be an explicit optional-feature error.
5. Run actual consumer CI using the existing pinned engineering-ci Workflow. Preserve
   original pytest failure semantics; export reports from the same execution.
6. Review both repository diffs and PRs. Authorized PR merge follows the current AGENTS.md rules;
   formal release remains human initiated. Preserve accepted phase SHAs with stage backup branches.

These are extraction checks. They do not establish optimization benefit or require Loop B.

Actual validation and remaining boundaries are recorded in
[extraction verification](../../verification/repository-split.md).
