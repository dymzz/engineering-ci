# Mixed Plan cache boundaries

This addresses [review 5933955956](https://github.com/dymzz/engineering-ci/pull/119#pullrequestreview-5933955956)
on #119. The initial [online-link experiment](../README.md) proved network
refresh and offline Exec reuse; it did not establish prerequisite/local-Check
deduplication for a whole Plan. The implemented declaration and execution
rules belong to the [Dagger integration](../../../architecture/integrations/dagger.md#plan-and-link-observation-cache-boundaries).

## Observed problem and repair

The baseline used a real v2 Plan produced by the existing selector, a real
Git repository, a source-only Python document generator, a local forwarding
Check, online Lychee, and a Data edge delivering the generated Markdown to
docs and the artifact bundle. Each fixture API request creates two new
PlanExecution objects, at HTTP 200 and then 404. Each object is separately
read through Report/Artifact/Enforce; it is not another Plan request.

With the original Module-local `command`, the same generator executed twice
in one request: **0.516 s and 0.459 s**, with identical output SHA-256 and
distinct original process observations. Both forwarding and docs ran once
per new Plan. Repeated readers did not start another command or Check.
This confirmed the review's repeat-work risk under the cache-never envelope.

The measured generator was then explicitly configured as `dagger_command`,
using the actual image digest from [native image metadata](image-query.json),
Linux amd64, `cache="source"`, the complete Source Directory and the same
generator argv. Its generated Markdown bytes match the Module-local baseline.
A separate cheap Module-local `module_probe` remains in the final mixed Plan,
so that boundary is still exercised. No action name is used to infer
determinism; undeclared/ordinary commands do not acquire a new cache.

## Actual execution counts

These counts cover two new Plan creations within each fixture request.
`native-repeated` is an independent API request with the same Plan/Source as
`native-first`. The last two requests use committed implementation
`0391997d82e134331ef8bf2cb84f337ad1731098` after the final output-selection
guard repair; their Source and runtime implementation hashes match.

| Request | Module-local costly generator | Native generator Execs | Module-local probe | forwarding calls | Plan Lychee Execs |
| --- | ---: | ---: | ---: | ---: | ---: |
| Module-local baseline | 2 | 0 | 0 | 2 | 2 actual target GETs; no Exec journal yet |
| First source-native observation | 0 | 1 | 2 | 2 | 2 |
| Independent repeat | 0 | 0 | 2 | 2 | 2 |
| Changed actual input file | 0 | 1 | 2 | 2 | 2 |
| Producer exits 23 after output | 0 | 1 | 2 | 2 | 0; docs blocked |
| Producer attempts Source write | 0 | 1 | 2 | 2 | 0; docs blocked |
| Final committed implementation, warm cache | 0 | 0 | 2 | 2 | 2 |
| Independent repeat of final implementation | 0 | 0 | 2 | 2 | 2 |

The first source-native observation also recorded two native `git ls-files`
Execs, one native `git show`, and two offline Lychee Execs over two distinct
Directory inputs (normal and missing local target). The two inventory Execs
consume the original and generated-overlay views; they are not claimed to be
one input. The independent repeat and both final requests added none of those
Git/offline Execs. They each added two online Lychee Execs and observed actual
HTTP 200 then 404. The local Check adapter still reevaluates each Plan; this
is not a claim that every deterministic Host computation is cached.

For every saved result in all eight requests, reader observations show:

- Report bytes equal the artifact Report and Directory identity remains equal.
- No additional Module-local command, local Check or instrumented native tool
  starts while reading Report/Artifact/Enforce.
- The target HTTP journal remains unchanged across those readers.

The counters wrap the original Module-local process and local-Check entry
points and retain their real returns; no Check verdict is stubbed. Native
process counting appends to a diagnostic Dagger CacheVolume only after an
actual capture Exec runs. The production capture and tool invocation are
used, with an added journal suffix; cached Execs do not append. Each request
clears only that diagnostic journal. Observer reads and HTTP control probes
are refreshed separately and are excluded from Check/tool counts; the full
native process journal remains inside each bundle. Mutable journal contents
do not feed computation results or verdicts and are not a pass/admission store.

## Invalidation and original failures

Changing `inputs/payload.txt` changed the consumed Directory digest, reran the
native generator once and changed the output SHA-256. Its repeated result
within that request remained shared. The native producer retains original
exit/stdout/stderr, process times, resolved image/platform and output Directory
reference, including when those observations came from an earlier cached Exec.

The real exit-23 producer keeps its partial generated file under
`prerequisites/build_docs/`, with `controlled producer failure after output`
in stderr. The dependent docs tool does not start; independent forwarding and
the Module-local probe still pass. Both Reports retain failed/23, and Enforce
rejects them. The read-only Source case preserves native exit 1 and
`OSError: [Errno 30] Read-only file system`; it likewise blocks only docs and
keeps the caller Source content unchanged. A partial file is not a satisfied
Data edge after a failed producer.

The normal HTTP-200 Plan is passed/0; HTTP-404 is failed/2 with original
Lychee diagnostics and Enforce rejection. Bundle export CLI 0 means evidence
delivery. Directory names `passed`/`failed` inside the fixture refer to those
HTTP contexts: producer-failure cases have failed Reports in both directories.

## Retained evidence and scope

[Receipt](receipt.json) gives each process/Check count, original native exit,
Result IDs, Report hashes, producer output references and reader checks.
[Native CLI chunks](native-cli.json) retain the actual calls and exits,
including an initial missing-input CLI diagnostic that never started a Plan.
An earlier whitespace probe hit the existing missing-Module-Git configuration
path and is not an acceptance result; the valid baseline uses the real local
forwarding Check. No whitespace guard or tool verdict was relaxed.

| Observation | Complete original bundle |
| --- | --- |
| Module-local baseline | [Bundle](module-local-bundle.tar.gz) |
| First native producer / repeat | [First](native-first-bundle.tar.gz), [repeat](native-repeated-bundle.tar.gz) |
| Input changes | [Bundle](changed-bundle.tar.gz) |
| Native failure / read-only boundary | [Exit 23](failed-bundle.tar.gz), [read-only](read-only-bundle.tar.gz) |
| Final committed implementation / repeat | [First](current-first-bundle.tar.gz), [repeat](current-repeated-bundle.tar.gz) |

The actual inputs are [baseline Source](local-check-source.tar.gz),
[native Source](native-source.tar.gz), [changed Source](changed-source.tar.gz),
[failed Source](failed-source.tar.gz), and [write-attempt Source](read-only-source.tar.gz),
with their respective [baseline](local-check-plan.json), [native](native-plan.json),
[changed](changed-plan.json), [failed](failed-plan.json), and [write-attempt](read-only-plan.json)
Plans. Preparation scripts are retained as original text:
[Module-local](prepare-module-local-source.txt), [native declaration](prepare-native-source.txt)
and [variants](prepare-source-variants.txt).

[Baseline implementation](baseline-implementation.tar.gz),
[first native implementation](native-implementation.tar.gz), and
[final implementation](current-implementation.tar.gz) match every hash
observed by their corresponding requests. Earlier working snapshots are not
relabeled as final HEAD. [SHA256SUMS](SHA256SUMS) preserves all raw evidence
bytes; the parent evidence remains unchanged.

Focused local verification: **220 passed** in contract/Plan/link suites
([JUnit](focused-tests.xml)); **11 passed** in affected selection/Host
prerequisite cases. After the unused-declaration output-selection guard, all
**69 Plan result tests passed**. Scoped Ruff check/format passed on 11 Python
files. An earlier broader affected-suite run had **288 passed, 1 failed**:
the unchanged Windows `weaver-cicd` Registry CRLF bytes differ from its
canonical manifest. Pins and fixture bytes were not weakened. A repository-local
pytest temp root uses a Git discovery ceiling to avoid inheriting this outer
worktree; no production Source guard was changed for tests.

This is controlled native computation and same-result evidence. It does not
authenticate caller-declared source-only eligibility, prove #66 Source closure,
attribute #87 fresh Tool Origin, grant #63 trust or complete #61/#86 B/D.
Production `engine_cache_outcome` remains unknown; only observed counts and
original results are reported here. No Weaver execution, OTLP capture, release
write or agent hook was invoked for this repair.

Parent: [Input-cache observations](../README.md).
