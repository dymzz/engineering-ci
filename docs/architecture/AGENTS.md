# Technology Inventory — Agent Instructions

Applies to [technology.md](technology.md) and [integrations/](integrations/README.md). Other
architecture pages follow the root [AGENTS.md](../../AGENTS.md).

## Scope

- Inventory technologies this repository chooses and uses directly: runtime, optional and
  development dependencies; build, test and CI tooling; CI platforms and pinned actions or
  modules; external services; external CLIs it invokes; data formats and standards it
  implements.
- Exclude transitive packages the code neither imports nor invokes, and tools used only on a
  developer's machine.
- `technology.md` holds one row per technology. Relationship details live in exactly one
  integration page entry. Link to existing authoritative documents for contracts and
  configuration instead of copying them.

## Status

Use only `adopted`, `experimental`, `planned` and `removed`, as defined in `technology.md`.

- `adopted` requires usage evidence and a pin in a manifest or configuration.
- `experimental` requires usage evidence plus the open gap and the criterion for promotion or
  removal.
- `planned` links to the decision and is never described as available.
- `removed` records what replaced it and what compatibility remains, and is kept rather than
  deleted.
- Change a status only with the evidence the target status requires.

## Entry fields

Each integration page entry uses these fields, in English, whatever the page language:

- **Status** — one of the four values.
- **Pin** — version or constraint and the file that sets it (`unpinned` with the reason for
  host-provided services or formats).
- **Used by** — source, configuration or workflow paths and their role.
- **Contract** — link to the authoritative document.
- **Verify** — the test, command or search that confirms usage.
- **Verified** — `YYYY-MM-DD @ <sha12>`: the date and the base commit the entry was last
  checked against.

Record the relationship (technology → implementation → pin → contract → verification). Do not
copy configuration values beyond the pin, installation steps or contract definitions.

## Incremental maintenance

Update affected entries in the same change when it:

- edits a dependency declaration in `pyproject.toml`, `.dagger/pyproject.toml`, `dagger.json`
  or `dagger.lock`;
- changes a workflow `uses:` reference, runner, tool version or invoked external CLI;
- changes an external service, data format or standard version;
- adds, moves or deletes a path listed under **Used by**;
- changes a status.

Update **Verified** only on entries actually re-checked.

## Targeted verification

- Verify only entries the change affects or that you rely on: compare **Pin** with the
  manifest, search the **Used by** paths for the import or invocation, and confirm the
  **Verify** target still exists.
- Usage evidence is an import or invocation in source, a reference in configuration or CI, or
  a test that exercises it. A manifest declaration, lockfile entry or installed package alone is
  not evidence.
- Record mismatches you cannot resolve within the current task (declared but unused, used but
  undeclared, drift between two pins) under **Discrepancies** in `technology.md`. Do not
  guess a status or expand the task to fix them.
- Run a full inventory sweep only when the task asks for an audit.

## Writing

- Keep each page's existing language. Update `integrations/README.md` when pages are added,
  renamed or removed.
- Never record credentials, connection strings, private hosts or task progress.
