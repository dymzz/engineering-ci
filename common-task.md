# ECI-CHECK-01 — Frozen implementation task

Implement the engineering requirements from the supplied frozen Issue input in the fixed `dymzz/engineering-ci` checkout. Deliver working code and original execution evidence, not only a proposal.

## Input integrity

1. Read the local `inputs/issue-63.md` (or the provided workspace copy named `issue-63.md`). Its SHA-256 must be `9ea5538829348a26220b34153488c3fae62cf333b9ff273f7a45c62a812db74e`.
2. If that input is absent or its checksum differs, stop and report the mismatch; do not silently substitute a different Issue revision. The supplied input is the reviewed problem-driven v2 draft, with all four problem areas and Q1–Q13; it is not a verbatim copy of the published GitHub Issue.
3. The target Engineering CI source revision is `97a6c92fbb485692cc3db9e255c5b8113abab6ae`. Confirm the checkout matches the assigned starting revision.

Treat the frozen Issue as the functional request. Determine the implementation by examining the existing code and actual engineering requirements, rather than treating previous analysis or Issue comments as a prescribed answer.

## Deliverables

- Working source and configuration changes in your own checkout.
- Original positive and negative tool/CI evidence; state what was not actually verified.
- A concise explanation of significant design choices, affected interfaces, reused evidence and outstanding questions.
- Final changed-file list / diff and original execution trace or measured resource data if available.

Do not publish a Release or change repository administration settings as part of this implementation. Do not inspect outputs or reviews from another experimental run.
