# Native Producer attribute checks

Existing #74 Checker `94996d6cc28e73a9862003b836a6ad3b3fe9996b`, Weaver 0.28.0.
Each Source has zero Finding Filters. Each case preserves its original
Source, raw Artifact, native Report and strict API result. Native violation
paths are distinct from ECI Terminal Attempt correlation failures.

- [real-tasks](real-tasks/README.md) — live, passed, native/gate=0/0.
- [unknown-repository](unknown-repository/README.md) — live, failed, native/gate=1/1.
- [unknown-attempt](unknown-attempt/README.md) — live, failed, native/gate=1/1.
- [repository-type](repository-type/README.md) — live, failed, native/gate=1/1.
- [controlled-root](controlled-root/README.md) — terminal, passed, native/gate=0/0.
- [controlled-wrong-attempt](controlled-wrong-attempt/README.md) — terminal, failed, native/gate=0/1.

[Bundle and consumer handoff](../README.md).
