# Controlled source bytes

- [source/ci-selection.toml](source/ci-selection.toml) is the exact comment-only
  Directory input to the eight native calls. It contains no production rules.
- [expected-generated.txt](expected-generated.txt) records the expected bytes
  written by the actual controlled Host command. It is an independent
  reconstruction, not an exported native Artifact. The temporary generated
  file was not delivered to downstream Dagger consumers.

The original Directory contains only `source/ci-selection.toml`. Its content
ID is `79a1803d2b2e51df82e82b46a61112c2eb925c7f12279ab4704ed5d79c228eb7`.
Reconstructing that tree plus `generated.txt` with the exact expected bytes
gives `4a7842a727092bb35d9f930451ba77f18efa8d357cd3ba42e113c14e3175cfe5`,
which equals the native Report's actual Host-after content ID. The immutable
Dagger input retains the first identity. This proves the guard detected a
real change; it does not prove generated-output delivery or Trusted coverage.

Parent: [dependency inventory and boundaries](../README.md).
