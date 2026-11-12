# Registered Producer Attempt input

Five original #75 files, copied byte for byte from PR #84 commit
`4a354c9db2506eacde5761e7ee3ede925d743b84`, under
`docs/verification/cicd-telemetry-attribute-mapping/`:

- `candidate.json`, `otel-traces.json`: revised Producer output, schema 9.
- `candidate.json.api-pages.json`: independent Attempt / Job API Source Witness.
- `receipt.json`, `producer.exit`: Producer source identity and original exit.

These are archived inputs, not a new API fetch or receiver receipt. Producer
source is `0e72f14e3bdd13ae6b6a96c41cf2659a85dfeede`; Run `42821429094/1`,
Head `7ea85c24a62e65a9384a0abe9eb541ca54064cbc`. The independent Expected Job
IDs come from the original #52 witness. The new witness cross-checks them
against these Source objects and reuses the existing Producer ID algorithm.

`SHA256SUMS` is a materialization manifest; the independent fixed hashes and
origin are in
[`real-attempt-registered-attributes.json`](../../integration/real-attempt-registered-attributes.json).
Editing both a local file and this manifest cannot change that expected pin.
The Registry and Check implementation remain owned by #74. No Producer
implementation, Checker, or Registry is copied into this fixture.

The original failing #52 archive is preserved separately. See the
[Weaver verification](../../../docs/verification/weaver.md) for actual execution.
