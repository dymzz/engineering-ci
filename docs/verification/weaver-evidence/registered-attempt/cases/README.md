# Executed acceptance cases

Each case preserves the native and Consumer outcomes separately. The positive
is genuine archived GitHub data; negatives are isolated controlled mutations.
Every Artifact archive is complete. Source uses one shared base plus exact
deltas; the input hashes in each receipt verify the reconstruction.

- [positive](positive/README.md) — Native 0/0; Consumer exit 0.
- [missing-task](missing-task/README.md) — Native 0/1; Consumer exit 1.
- [extra-task](extra-task/README.md) — Native 0/0; Consumer exit 1.
- [duplicate-task](duplicate-task/README.md) — Native 0/1; Consumer exit 1.
- [wrong-trace](wrong-trace/README.md) — Native 0/1; Consumer exit 1.
- [wrong-attempt](wrong-attempt/README.md) — Native 0/0; Consumer exit 1.
- [wrong-head](wrong-head/README.md) — Native 0/0; Consumer exit 1.
- [wrong-repository](wrong-repository/README.md) — Native 0/0; Consumer exit 1.
- [zero-matcher](zero-matcher/README.md) — Native 0/1; Consumer exit 1.
- [no-samples](no-samples/README.md) — Native 0/1; Consumer exit 1.
- [missing-task-url](missing-task-url/README.md) — Native 1/1; Consumer exit 1.
- [missing-error-type](missing-error-type/README.md) — Native 1/1; Consumer exit 1.

[Acceptance bundle](../README.md).
