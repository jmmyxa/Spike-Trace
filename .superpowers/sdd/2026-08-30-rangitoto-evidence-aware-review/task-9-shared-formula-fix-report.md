# Task 9 Prerequisite Report: Raw Shared-Formula Verification

Status: complete for the shared-formula prerequisite. Raw formula verification accepts the exact frozen XLSX under a hash-bound aggregate compatibility permission. The public verifier now advances past the original `C4:C15` failure and stops at the separate pre-existing `round-01-clip-023` overlap rule. Task 9 data composition remains out of scope and `round-01-review-v2.json` remains absent.

## Scope

- Starting HEAD: `983ea4c fix: reject blank review bundle identifiers`.
- Added raw OOXML verification from the exact `workbookBytes` already owned by `verifyWorkbookFile`; the workbook path is not reopened for formula evidence.
- Resolved `人工动作` through `xl/workbook.xml` and `xl/_rels/workbook.xml.rels`, then parsed its worksheet with strict SAX events.
- Kept importer display/formula checks, but accepted compressed importer blanks only when raw formulas are valid and an authenticated compatibility range covers the complete 12-row block.
- Added aggregate `人工动作!C4:C483` support plus aligned exact-block support. Invalid sheet/column/range alignment, block size, display value, or incomplete coverage remains fail-closed.
- Did not modify the selection, real workbook, Task 9 evidence override, canonical overlap behavior, or Task 9 data content.

## Dependency and Loader Boundary

Bundled workspace runtime:

```text
Node.js                 v24.19.0
@oai/artifact-tool      2.8.52
jszip                   3.10.1
sax                     1.6.1
```

Resolved only for the test process through a percent-encoded `data:text/javascript` ESM loader:

```text
@oai/artifact-tool -> .../node_modules/@oai/artifact-tool/dist/artifact_tool.mjs
jszip              -> .../node_modules/jszip/lib/index.js
sax                 -> .../node_modules/sax/lib/sax.js
```

Production imports remain portable bare specifiers and contain no machine-specific runtime paths. Every Python-backed command set `PYTHONPATH` to this worktree's resolved `src` and `SPIKETRACE_PYTHON` to this worktree's `.venv` Python.

## RED

The new public-boundary fixture creates an artifact-tool-authored workbook, rewrites raw worksheet XML to one standard shared block and the real C28 mixed block, confirms it survives `SpreadsheetFile.importXlsx` as producer-shaped formula strings, then calls `verifyWorkbookFile` with a bound aggregate permission.

Command shape:

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
$env:SPIKETRACE_PYTHON = (Resolve-Path '.venv\Scripts\python.exe').Path
$env:NODE_OPTIONS = '--experimental-loader=<percent-encoded bundled package map> --disable-warning=ExperimentalWarning'
& $SPIKETRACE_NODE tools\test_active_review_shared_formulas.mjs
```

Observed before production changes (`exit 1`):

```text
Error: Action shared hyperlink block C4:C15 is invalid.
    at fail (.../tools/active_review_workbook_semantics.mjs:27:32)
    at verifyWorkbookSemanticSnapshot (.../tools/active_review_workbook_semantics.mjs:127:362)
    at verifyWorkbookSemantics (.../tools/active_review_workbook_semantics.mjs:204:20)
    at async verifyWorkbookFile (.../tools/verify_active_review_batch.mjs:230:20)
    at async .../tools/test_active_review_shared_formulas.mjs:158:3
```

This was the intended RED: the real importer representation existed, the aggregate permission was hash-bound, and production still required invented `{formula, sharedFormula}` objects.

## GREEN and Tamper Coverage

After adding `active_review_xlsx_formulas.mjs` and wiring its result through the semantic verifier, the focused suite exited `0`:

```text
Inspect result written to file: .../active-review-shared-formulas-.../batch/expanded.xlsx.inspect.ndjson
exit=0
```

The first implementation run correctly revealed one integration defect rather than a semantic relaxation:

```text
Error: Action raw formula verification failed: worksheet cells are nested.
```

Bundled `sax@1.6.1` returns a QName string from `onclosetag`; normalizing that QName was the only correction. The next focused run passed all cases.

An independent fail-closed review then identified that local-name-only XML matching could authenticate a foreign namespace. The new `<evil:f>` public mutation first produced the expected RED:

```text
AssertionError [ERR_ASSERTION]: Missing expected rejection: foreign-namespace formula must not authenticate a follower
exit=1
```

The parser now requires exact transitional/strict SpreadsheetML, office relationship, package relationship, and worksheet relationship type URIs plus the correct unqualified/qualified attribute identities. The foreign formula, counterfeit relationship attribute, and suffix-only relationship type cases then passed in the focused suite. A second independent review reported no remaining Critical or Important issue.

The focused public suite proves:

- importer shape: C4 formula plus C5:C15 blanks; C28 and C29 formulas plus C30:C39 blanks;
- aggregate and exact block permissions accept valid raw producer formulas;
- missing permission, unaligned range, wrong block size, wrong sheet, or a permission not covering C4 rejects;
- deleting a follower `<f>` while retaining cached display rejects;
- wrong master hyperlink text, wrong follower `si`, cross-block `ref`, duplicate master, and an extra shared cell reject;
- a foreign-namespace `<evil:f>`, counterfeit relationship attribute, or suffix-only worksheet relationship type rejects;
- a workbook byte mutation rejects against the original bound SHA;
- expanded artifact-tool workbooks continue through the unchanged v1 tests without compatibility permission.

## Frozen Workbook Read-Only Evidence

Command:

```powershell
& $SPIKETRACE_NODE tools\test_active_review_shared_formulas.mjs --real `
  data\active-learning\rangitoto\round-01-selection.json `
  outputs\active-learning\rangitoto\round-01\review.xlsx `
  data\active-learning\rangitoto\round-01-evidence-overrides.json
```

Observed (`exit 0`):

```json
{"shared_blocks":40,"standard_shared_blocks":39,"mixed_block":"C28:C39","public_next_blocker":"Clip round-01-clip-023 has overlapping timed rows","selection_sha256":"c7c9d4c21ae8fb041eece192b9c4f2c66648c863fdf79c278f08ca11e6cfe06c","workbook_sha256":"3b3baa474bf5d20e24a2e979b389e5d1b6df755b3c8516c993d8cc719b53535b"}
```

The test reads the existing override, substitutes the aggregate shared-formula permission in memory, binds that in-memory envelope to the exact frozen selection/workbook bytes, and never writes an output. Raw verification found 39 full shared blocks and the exact mixed C28 ordinary / C29 master / C30:C39 follower block. The public verifier reached the unrelated canonical overlap check, proving the original formula failure is removed without changing later behavior.

## Regression and Lint Evidence

Focused evidence plus v1 batch command:

```powershell
& $SPIKETRACE_NODE tools\test_active_review_evidence.mjs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $SPIKETRACE_NODE tools\test_active_review_batch.mjs
```

Observed: `exit 0`. The evidence suite completed all bound-envelope, composition, race, and atomicity fixtures. The v1 batch suite completed expanded workbook build/verify, exact extractor byte assertions, corruption rejection, input-race rejection, and rollback tests.

Additional fresh checks:

```text
node --check changed .mjs files     exit 0
.venv/Scripts/ruff.exe check .      All checks passed!
git diff --check                    exit 0
```

`git diff --check` emitted only Git's existing LF-to-CRLF working-copy warnings for tracked files; it reported no whitespace error.

## Source Hashes

Before:

```text
selection  length=67932  sha256=c7c9d4c21ae8fb041eece192b9c4f2c66648c863fdf79c278f08ca11e6cfe06c
workbook   length=41985  sha256=3b3baa474bf5d20e24a2e979b389e5d1b6df755b3c8516c993d8cc719b53535b
override                 sha256=b28fe875eebe01dc7f23b778518b585ee441839b83a7c1b0ef2823e9ce4d32d0
```

After:

```text
selection  length=67932  sha256=c7c9d4c21ae8fb041eece192b9c4f2c66648c863fdf79c278f08ca11e6cfe06c
workbook   length=41985  sha256=3b3baa474bf5d20e24a2e979b389e5d1b6df755b3c8516c993d8cc719b53535b
override                 sha256=b28fe875eebe01dc7f23b778518b585ee441839b83a7c1b0ef2823e9ce4d32d0
review-v2                absent
```

## Self-Review and Concern

- All 480 expected action hyperlink cells require raw `<f>` nodes and exact clip-specific formula semantics. Cached `播放` never substitutes for a formula.
- Spreadsheet elements, office relationship attributes, package relationship elements, and worksheet relationship types require exact transitional or strict OOXML namespace identities; local-name or suffix matches cannot authenticate content.
- Shared groups require integer `si`, one master, exact aligned block `ref`, exact master text, coherent followers, and no extra shared formula cell on the action sheet.
- Compatibility authority is still taken only from the object whose selection/workbook path and SHA bindings `verifyWorkbookFile` verifies before raw/imported semantics.
- The raw helper resolves the named worksheet relationship and does not assume `sheet2.xml`.
- The only remaining real-public-path concern is the independently pre-existing `round-01-clip-023` overlapping timed rows rule. This task deliberately does not change that rule or Task 9 evidence content.

## Formal Review Round 1/5: Important Findings

Starting HEAD for this round:

```text
f70cc9ffd2b07c894734d9a3b3a9ffe138a7b240 fix: verify shared review formulas from OOXML
```

Only the four Important findings were changed. The loader/report-command Minor comments remain out of scope.

### 1. Exact XML Identity and Parent Chain

Public mutations moved all 480 formula cells under `worksheet/extLst/ext`, changed the worksheet root to a foreign namespace while rebinding `sheetData`, mixed strict and transitional SpreadsheetML on one worksheet, moved the named workbook `sheet` outside `workbook/sheets`, moved the matching package `Relationship` under an extension node, and mismatched row/cell references.

The relocation RED reached later imported-display validation instead of raw rejection:

```text
AssertionError: formula cells outside sheetData rows must not authenticate action formulas
actual: Error: Action hyperlink display row 4 is invalid.
expected: /raw formula/
exit=1
```

The GREEN parser requires exact `workbook -> sheets -> sheet`, `Relationships -> Relationship`, and `worksheet -> sheetData -> row -> c -> f` paths. The worksheet uses one allowed SpreadsheetML namespace, each cell row matches its containing row, duplicate authentic cells reject, and only a direct formula child authenticates a target cell. The focused suite then exited `0`.

### 2. Global Shared Groups and Exact Mixed Exception

Public mutations reused `si=0` in C4:C15 and C28:C39, mixed an ordinary formula into a standard shared block, moved the C28 ordinary formula, moved the C29 master, and added a second ordinary formula to the mixed block.

The global-group RED was:

```text
AssertionError: Missing expected rejection: shared si reused across blocks
exit=1
```

The GREEN verifier builds sheet-wide shared groups, requires one master and one block per `si`, requires standard blocks to be 12 shared cells with the master at the first cell, and permits only C28 ordinary plus C29 master plus C30:C39 followers for the mixed block. Fully expanded blocks remain exactly 12 ordinary formulas. The focused suite then exited `0`.

### 3. ZIP Admission Before JSZip

Public mutations duplicated the exact central-directory record for `xl/workbook.xml`, duplicated the resolved action worksheet record, and generated a 5 MiB highly compressible worksheet XML part.

The duplicate-entry RED proved JSZip's post-load map was too late:

```text
AssertionError: duplicate ZIP entry xl/workbook.xml must be rejected
actual: Error: BadPackageFormat from the later SpreadsheetFile importer
expected: /raw formula/
exit=1
```

The GREEN raw central-directory admission runs before `JSZip.loadAsync` and rejects duplicate normalized entry names, encryption, data descriptors, unsafe/traversing/backslash/absolute names, ZIP64, multi-disk archives, unsupported compression, local/central mismatches, excess counts/sizes/ratios, and invalid ZIP structures.

An independent Critical/Important review found that JSZip honors Info-ZIP Unicode Path field `0x7075`, which can replace a raw-safe admitted name and create an effective duplicate or unsafe path. The public central-header mutation first failed at the later XML layer:

```text
AssertionError: central Unicode Path extra fields must be rejected before JSZip
actual: Action raw formula verification failed: xl/workbook.xml has an invalid root element.
expected: /unsupported Unicode Path extra field/
exit=1
```

The admission parser now rejects `0x7075` in central and local extra fields, rejects duplicate extra-field IDs, and checks each extra field's complete bounds. Matching central and local public mutations then passed in the focused suite. ZIP comment field `0x6375` remains irrelevant to entry-path identity and was not changed.

Admission limits:

```text
archive bytes                 64 MiB
entry count                   2,048
compressed bytes per entry    16 MiB
uncompressed bytes per entry  16 MiB
XML/rels bytes per entry       4 MiB
total uncompressed bytes      64 MiB
compression ratio             200x
```

The frozen workbook has 41,985 archive bytes, 14 entries, a 167,746-byte largest part, 255,451 total uncompressed bytes, and an 8.12x maximum ratio. The focused suite then exited `0`.

### 4. Permission Canonicality

Public fixtures covered aggregate-plus-single overlap, a partial C4:C471 39-block aggregate, overlapping multi-block ranges, a permission over an expanded block, and duplicate permissions over one shared block.

The initial aggregate RED was:

```text
AssertionError: Missing expected rejection
```

It showed that C4:C483 authorized a producer fixture containing only two shared blocks and 38 expanded blocks. The GREEN raw path now accepts either the sole exact C4:C483 aggregate when all 40 blocks are shared, or unique exact 12-row single-block permissions matching shared blocks one-for-one. Expanded blocks have no compatibility permission. Direct semantic tests without raw block metadata retain their existing behavior. The focused suite then exited `0`.

### Fresh Round-1 Verification

Focused public suite:

```text
tools/test_active_review_shared_formulas.mjs
exit=0
```

Adjacent regressions and syntax checks:

```text
tools/test_active_review_evidence.mjs       exit=0
tools/test_active_review_batch.mjs          exit=0
node --check changed .mjs files             exit=0
```

Frozen real public path:

```json
{"shared_blocks":40,"standard_shared_blocks":39,"mixed_block":"C28:C39","public_next_blocker":"Clip round-01-clip-023 has overlapping timed rows","selection_sha256":"c7c9d4c21ae8fb041eece192b9c4f2c66648c863fdf79c278f08ca11e6cfe06c","workbook_sha256":"3b3baa474bf5d20e24a2e979b389e5d1b6df755b3c8516c993d8cc719b53535b"}
```

The selection, workbook, and override remain frozen. `round-01-review-v2.json` remains absent. The only remaining real-public-path concern is still the separate `round-01-clip-023` overlapping timed rows rule.

## Formal Review Round 2/5: Local ZIP CRC

Starting HEAD for this round:

```text
9fa6ae72ba9188c90d878c3c2f5c81451f01ab53 fix: harden shared formula package verification
```

The single scoped Important finding was that admission compared local and central signatures, names, flags, methods, and sizes, but did not compare the local-header CRC at offset `+14` with the central-directory CRC at offset `+16`.

The public mutation flips one bit only in the local header CRC for `xl/workbook.xml`. The central record, compressed data, workbook semantics, and compatibility envelope remain unchanged. Before the production fix, the focused suite produced the intended RED:

```text
AssertionError: Missing expected rejection: local CRC must match the central directory CRC
exit=1
```

The minimal GREEN reads both CRC fields and adds their equality to the existing central/local consistency gate. Data descriptors remain rejected, and the existing ZIP64, size/ratio, unsafe-name, Unicode Path, duplicate-entry, encryption, compression-method, and extra-field checks are unchanged. The focused public suite then exited `0`.

Fresh round-2 verification:

```text
tools/test_active_review_shared_formulas.mjs             exit=0
tools/test_active_review_evidence.mjs                    exit=0
tools/test_active_review_batch.mjs                       exit=0
real frozen public raw stage                             exit=0
node --check changed JavaScript files                    exit=0
.venv/Scripts/ruff.exe check .                           All checks passed!
git diff --check                                         exit=0
```

The real public stage again found 40 shared blocks, 39 standard blocks, the exact C28:C39 mixed block, and advanced to `Clip round-01-clip-023 has overlapping timed rows`. The selection and workbook hashes remain `c7c9d4...` and `3b3baa...`; `round-01-review-v2.json` remains absent. The untracked Task 9 override was concurrently updated by its owning task during verification and was neither modified nor staged by this formula-fix round. Scoped independent re-review reported no Critical or Important finding.
