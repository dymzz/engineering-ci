# ECI-CHECK-01 — Frozen implementation task

Implement the engineering requirements from the supplied frozen Issue input in the fixed `dymzz/engineering-ci` checkout. Deliver working code and original execution evidence, not only a proposal.

## Input integrity

1. Read the local `inputs/issue-63.md` (or the provided workspace copy named `issue-63.md`). Its SHA-256 must be `315eb95bd0dbde4223bf93699d969d89b39f52b53cb03820f4d7e95c1b2c43e1`.
2. If that input is absent or its checksum differs, stop and report the mismatch; do not silently replace it with the live GitHub Issue #63, which has a different, more detailed history.
3. The target Engineering CI source revision is `c40fb6996548ec1ce2e3a6555771baf2b4d3762c`. Confirm the checkout matches the assigned starting revision.

Treat the frozen Issue as the functional request. Determine the implementation by examining the existing code and actual engineering requirements, rather than treating previous analysis or Issue comments as a prescribed answer.

## Deliverables

- Working source and configuration changes in your own checkout.
- Original positive and negative tool/CI evidence; state what was not actually verified.
- A concise explanation of significant design choices, affected interfaces, reused evidence and outstanding questions.
- Final changed-file list / diff and original execution trace or measured resource data if available.

Do not publish a Release or change repository administration settings as part of this implementation. Do not inspect outputs or reviews from another experimental run.
