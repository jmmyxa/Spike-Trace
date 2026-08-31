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
