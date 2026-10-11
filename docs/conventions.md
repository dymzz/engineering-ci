# Documentation Conventions

## 1. Structure

Structural Evolution

The documentation hierarchy is organized by semantic scope and responsibility, not by a fixed directory layout.

Use one classification axis at each directory level. Place functional topics within their documentation responsibility rather than alongside it.

Preserve meaningful subject boundaries regardless of directory depth or file count.

When the current task explicitly includes restructuring, create, split, merge, move, or remove directories only when their actual content and responsibilities justify that change.

Before restructuring, examine the affected directories, their README navigation, document contents, and existing references.

Do not flatten or merge distinct subjects merely to reduce directory depth or file count.

Ensure that structural changes preserve relevant definitions, clear ownership of each subject, and accurate navigation.

Use Git diffs to review the resulting changes and identify unintended content loss.

Naming conventions and documentation writing frameworks do not independently justify structural changes.

## 2. Naming — Required

These rules apply to documentation under `docs/` and `guides/`. Existing project instruction filenames are preserved.

- Directory names use lowercase English `kebab-case`.
- Markdown filenames use lowercase English `kebab-case.md`.
- The reserved navigation filename is exactly `README.md`.
- Names must describe their subject clearly within their parent directory.
- Use consistent terminology for the same concept throughout the documentation.
- Avoid ambiguous collection names such as `misc`, `other`, `notes`, or `temp`.
- Do not encode revision numbers, dates, or temporary status in names merely to preserve documentation history.
- Avoid repeating the parent directory's meaning in a filename when it adds no distinction.

Directory names and Markdown filename stems, except `README.md`, must match:

`^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$`

## 3. Navigation

- Each `README.md` provides a concise scope statement and links to its immediate child documents and directories.
- A link to the existing parent `README.md` may provide upward navigation; do not bypass a child directory's index to list its individual documents.
- Each navigation entry briefly identifies the content's responsibility.
- Every documentation file must be reachable through the README navigation hierarchy.
- Use relative Markdown links for internal documentation.
- Keep navigation updated when files are added, moved, renamed, or removed.
- Do not reproduce detailed definitions in navigation documents.

## 4. Definition Ownership

- Each normative definition has one authoritative location.
- Reference existing definitions with Markdown links instead of maintaining duplicates.
- Keep related constraints, behavior, and design rationale together when they belong to the same subject.
- Split or merge documents only when the current task includes that change and the definitions' meaning and ownership are established. Preserve authoritative technical content when reorganizing it.
- Avoid creating independent documents that repeat an existing subject without a distinct responsibility.

## 5. Maintenance

- These conventions constrain authorized changes; they do not grant permission to reorganize other documentation.
- Creating, moving, renaming, or deleting documents and directories must be within the current task's scope. A request to update content or links does not by itself authorize restructuring.
- Do not delete an established directory solely because it is empty, lacks a README, or could be replaced by a single file. Report the gap and propose the necessary navigation or scope change.
- Follow Docs-as-Code: maintain documentation alongside the implementation it describes.
- Update affected documentation within the same change when documented behavior or design changes.
- Preserve design information whose validity cannot be determined and report the unresolved conflict.
- Keep historical validation evidence when it has a distinct purpose; identify its historical scope in the content rather than presenting it as current operational guidance.
- Use Git history to recover previous revisions.
- Inspect actual repository changes using Git and review relevant code and documentation diffs together.
- Do not introduce additional tracking metadata, document identifiers, or registries to represent relationships already expressed through paths and links.

## 6. Writing Organization

User-facing guides are maintained under [User Guides](../guides/README.md).
Organize their content by the reader's purpose, following [Diátaxis](https://diataxis.fr/):

- Tutorials: guided learning and first use.
- How-to guides: steps to accomplish a specific task.
- Reference: commands, parameters, outputs, and interfaces for lookup.
- Explanation: reasons, concepts, and tradeoffs needed for understanding.

These purposes do not require four directories or one document per category. Keep technical design and authoritative constraints in `docs/`; guides link to them instead of copying definitions. README files contain scope and navigation only, including the guide index.
