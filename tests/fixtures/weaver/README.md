# Controlled Weaver checker fixture

`registry/semconv.yaml` is the original, unmodified matcher test model from
open-telemetry/weaver commit `51a5bf95bdbb2e20fb6e16638e6bbe7dde911e93`, path
`crates/weaver_live_check/data/model/matchers/semconv.yaml`.
SHA-256: `1860a160e68287387e88e0eb43e55f053b3c0cb4051bf83041cd3d3caa5df391`.
Git blob: `e03854a6a4d353548d8802aadfc91b302a71ec56`.
The upstream Apache-2.0 license is retained in `LICENSE.weaver`.

`registry/manifest.yaml` is ECI's local, dependency-free registration of that
model. It is derived test configuration, not an unmodified upstream manifest.
The original fixture's external dependency is unused by the selected
`acme.cart` / `acme.step` definitions. `registry-sha256.json` covers both actual
files and `.gitattributes` preserves their bytes across checkouts.

The finite OTLP capture and native sample JSON are ECI controlled examples.
The `fixture.cart` CEL matcher selects the `acme.cart` signal; the strict
`acme.step` group requires `acme.session.id`. They validate the checker, not a
real GitHub producer. The capture has a fixed nonzero trace/span identity and
completed timestamps. There is intentionally no official Pipeline terminal
claim in this generic test model.

```sh
dagger api call weaver-artifacts --source tests/fixtures/weaver --kind live export --path .artifacts/weaver-fixture
dagger api call weaver-enforce --source tests/fixtures/weaver --kind live
```
