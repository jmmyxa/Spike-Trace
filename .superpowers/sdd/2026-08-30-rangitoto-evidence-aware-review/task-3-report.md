# Task 3 Report

## RED

Command:

```powershell
& $env:SPIKETRACE_NODE tools/test_active_review_evidence.mjs
```

Result: the bundled Node runtime/junction was unavailable in this environment, so the command could not be executed.

## GREEN

Commands:

```powershell
& $env:SPIKETRACE_NODE tools/test_active_review_evidence.mjs
& $env:SPIKETRACE_NODE tools/test_active_review_batch.mjs
```

Result: not runnable for the same missing `SPIKETRACE_NODE` runtime. `git diff --check` completed without whitespace errors.

## Self-review

- v1 `actionRows`, selection projection, strict blank-manual defaults, and published output fields remain intact.
- Frozen selection/workbook bytes are used directly; bound selection/workbook paths and SHA-256 values are required before compatibility is considered.
- Workbook compatibility is limited to authenticated banner trim, shared formula blocks, exact validation gaps, and null read-only clip-ID repairs.
- Canonical rows retain raw manual values and record normalized side inheritance and source repairs separately.
- Untimed backgrounds are clip sentinels; timed backgrounds are intervals and participate in overlap checks.

## Review Fix Round 1/5

Command (RED before schema restriction):

```powershell
& 'C:\Users\Fakelove\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' tools\test_active_review_evidence.mjs
```

Result: failed as intended with `Missing expected rejection` for a bound `A1` banner-trim permission.

GREEN commands:

```powershell
& 'C:\Users\Fakelove\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' tools\test_active_review_evidence.mjs
& 'C:\Users\Fakelove\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --check tools\active_review_workbook_semantics.mjs
git diff --check
& 'C:\Users\Fakelove\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' tools\test_active_review_batch.mjs
```

Results: the evidence/semantic test suite, Node syntax check, and whitespace check exited `0`. The batch regression test could not load `@oai/artifact-tool` because this worktree lacks the required dependency junction; no junction was created. Its new v1 assertions cover cleaned builder banners plus action-header and hyperlink-display tampering when the artifact runtime is available.

Fixes cover exact headers and label content, four-A2-only banner compatibility, clip/action hyperlink displays and strict shared-formula blocks, raw-vs-normalized A16 repair lineage, zero-source rejection for completed workbooks, side inheritance/conflicts, timed backgrounds, validation changes, and unexpected sheets. Strict blank template verification remains compatible by applying the nonempty-source rule only when manual values are allowed.
