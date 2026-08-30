# Rangitoto Evidence-Aware Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the completed 40-clip Rangitoto workbook into an immutable evidence-aware result set, exclude inferred and visually unavailable observations from action-model training, and publish one synchronized six-file result bundle without changing source bytes.

**Architecture:** Preserve the existing v1 workbook and `apply-active-review` path exactly. A hash-bound Node pipeline verifies the real XLSX by semantics, preserves raw and normalized rows, applies explicit evidence overrides, and emits a deterministic v2 review input. Four focused Python modules validate that input, compose observations, derive the seven-class training projection, and render a complete result directory published by one same-parent atomic no-replace rename.

**Tech Stack:** Node.js ESM, `@oai/artifact-tool`, Python 3.10+, standard-library dataclasses/CSV/JSON/hashlib/ctypes, existing Spike-Trace loaders, `unittest`, Ruff, and executable Node tests.

## Execution Bootstrap

At the start of an implementation session, obtain both the bundled Node executable and bundled Node package directory through the Codex workspace-dependency loader. Assign the returned executable to `SPIKETRACE_NODE` and the returned package directory to `SPIKETRACE_NODE_MODULES`. Resolve this worktree's virtual-environment Python once:

```powershell
$env:SPIKETRACE_PYTHON = (Resolve-Path '.venv\Scripts\python.exe').Path
$env:PYTHONPATH = (Resolve-Path 'src').Path
```

Every Node command below uses `& $env:SPIKETRACE_NODE`, so it does not assume `node.exe` is globally installed.

The repository has no local `package.json` or `node_modules`, and Node ESM does not resolve the loader's packages through `NODE_PATH`. Before any Node task, create a temporary worktree-local junction only after validating both endpoints. Never replace or delete a pre-existing path:

```powershell
$worktreeRoot = (Resolve-Path '.').Path
$taskNodeModules = Join-Path $worktreeRoot 'node_modules'
$bundledNodeModules = (Resolve-Path -LiteralPath $env:SPIKETRACE_NODE_MODULES).Path
$bundledItem = Get-Item -LiteralPath $bundledNodeModules -Force
if (-not $bundledItem.PSIsContainer) {
    throw 'SPIKETRACE_NODE_MODULES must resolve to a directory.'
}

$script:SPIKETRACE_CREATED_NODE_MODULES_JUNCTION = $false
function Remove-SpikeTraceNodeModulesJunction {
    if (-not $script:SPIKETRACE_CREATED_NODE_MODULES_JUNCTION) { return }
    $link = Get-Item -LiteralPath $taskNodeModules -Force
    $target = (Resolve-Path -LiteralPath ([string]$link.Target)).Path
    if ($link.LinkType -ne 'Junction' -or $target -ne $bundledNodeModules) {
        throw 'Refusing to remove an unverified node_modules path.'
    }
    Remove-Item -LiteralPath $taskNodeModules
    $script:SPIKETRACE_CREATED_NODE_MODULES_JUNCTION = $false
}

$existingNodeModules = Get-Item -LiteralPath $taskNodeModules -Force -ErrorAction SilentlyContinue
if ($null -ne $existingNodeModules) {
    throw 'Worktree node_modules must be absent before this session starts.'
}

$createdLink = New-Item -ItemType Junction -Path $taskNodeModules -Target $bundledNodeModules
$script:SPIKETRACE_CREATED_NODE_MODULES_JUNCTION = $true
try {
    $createdTarget = (Resolve-Path -LiteralPath ([string]$createdLink.Target)).Path
    if ($createdLink.LinkType -ne 'Junction' -or $createdTarget -ne $bundledNodeModules) {
        throw 'Could not verify the temporary node_modules junction.'
    }
} catch {
    Remove-SpikeTraceNodeModulesJunction
    throw
}
```

Wrap every Node command listed for the selected implementation task in one PowerShell `try` block and call `Remove-SpikeTraceNodeModulesJunction` from its matching `finally` block. Cleanup never uses recursion and removes only a junction created by that session after revalidating its target.

## Global Constraints

- Preserve `outputs/active-learning/rangitoto/round-01/review.xlsx` byte-for-byte; expected SHA-256 is `3b3baa474bf5d20e24a2e979b389e5d1b6df755b3c8516c993d8cc719b53535b`.
- Preserve `data/active-learning/rangitoto/round-01-selection.json` byte-for-byte; expected SHA-256 is `c7c9d4c21ae8fb041eece192b9c4f2c66648c863fdf79c278f08ca11e6cfe06c`.
- Preserve the v1 extractor, `apply_active_review()`, `apply-active-review` CLI, tests, and output bytes.
- Do not add an audit/status/checkbox column or a separate `free_ball` column.
- `free_ball` remains a review-label value. Direct visual `free_ball` projects to `background` while authority preserves `review_label=free_ball`.
- Keep `src/spiketrace/constants.py` and the current seven-class model head unchanged.
- A primarily referee-, scoreboard-, context-, mixed-, fully-occluded-, off-camera-, or unresolved-evidence action never enters positive visual training.
- A directly visible action remains trainable when only its outcome is referee- or scoreboard-inferred.
- Every timed human observation is protected from automatic negatives, including timed `background` and `free_ball`.
- Fully occluded, off-camera, and unresolved intervals are protected; clip-bounded uncertainty protects the full selected clip.
- Only a clip/side with one untimed `background` sentinel may donate hard negatives.
- Hard negatives conflict with protected intervals and chosen negatives only on the same `team_side`.
- One block action has one training window regardless of participant count; participation is never broadcast to the team.
- Current Rangitoto participant output remains empty until identity assignments are independently confirmed.
- Use `unittest`, not pytest. Set `PYTHONPATH` to this worktree's `src` for Python tests.
- Bundle CSV files use UTF-8 BOM and CRLF. Bundle JSON files use UTF-8 without BOM, LF, and one trailing newline.
- The final result directory must not exist as a file, directory, or symlink. Publish one complete same-parent staging directory with an atomic no-replace directory move.
- Update `README.md` whenever implemented modules, tools, durable artifacts, or workflow structure change.

## Result Identity

Node and Python derive the same stable ID from source identity:

```python
canonical = "\0".join(
    (
        "spiketrace.active-review-observations",
        "2",
        batch_id,
        round_id,
        selection_sha256,
        workbook_sha256,
        evidence_overrides_sha256,
    )
).encode("utf-8")
result_set_id = f"{batch_id}/result-{hashlib.sha256(canonical).hexdigest()[:16]}"
```

The review input stores this ID and Python recomputes it. `review_input_sha256` is excluded because the review input contains `result_set_id`.

## Fixed Result Bundle

`data/annotations/rangitoto_round_01/` contains exactly:

```text
round-01-results.json
action_training_round_01.csv
round-01-observations.csv
round-01-visibility-events.csv
round-01-action-participants.csv
round-01-exports.manifest.json
```

The exports manifest lists the other five files with SHA-256, byte count, media type, encoding, and line ending. CSV entries use `data_rows` for rows excluding the header and `entity_counts=null`. The authority JSON entry uses `data_rows=null` and an exact `entity_counts` object. The manifest does not hash itself. Authority JSON includes the four CSV hashes and the exports-manifest path, but not its own file hash.

`round-01-visibility-events.csv` is intentionally the combined portable view for both event kinds. Authority JSON still keeps `occlusion_events` and `off_camera_events` separate; the broader filename avoids describing off-camera rows as occlusions.

## File Map

**Create:**

- `tools/active_review_io.mjs` — immutable byte snapshots, SHA-256, safe paths, and no-overwrite JSON publication.
- `tools/active_review_evidence_overrides.mjs` — strict hash-bound override envelope and references.
- `tools/active_review_workbook_semantics.mjs` — XLSX semantic equivalence and row canonicalization.
- `tools/compose_active_review_evidence.mjs` — deterministic review-input-v2 composer and CLI.
- `tools/test_active_review_evidence.mjs` — focused Node tests and optional real-workbook acceptance.
- `src/spiketrace/_active_learning_review_contract.py` — v2 source, entity, enum, lineage, and reference validation.
- `src/spiketrace/_active_learning_review_observations.py` — immutable observations and visibility-event merging.
- `src/spiketrace/_active_learning_review_projection.py` — training decisions, protected intervals, and hard negatives.
- `src/spiketrace/_active_learning_review_outputs.py` — serializers, exports manifest, and result-directory publication.
- `tests/test_active_learning_review_contract.py`
- `tests/test_active_learning_review_observations.py`
- `tests/test_active_learning_review_projection.py`
- `tests/test_active_learning_review_outputs.py`
- `data/active-learning/rangitoto/round-01-evidence-overrides.json`
- `data/active-learning/rangitoto/round-01-review-v2.json`
- `data/annotations/rangitoto_round_01/*`

**Modify:**

- `tools/verify_active_review_batch.mjs` — delegate semantics while retaining public exports and CLI summary.
- `tools/extract_active_review_results.mjs` — share immutable I/O without changing v1 JSON.
- `tools/build_active_review_batch.mjs` — stop creating new trailing banner spaces.
- `tools/test_active_review_batch.mjs` — retain v1 coverage.
- `src/spiketrace/active_learning_review.py` — retain v1 and add a small v2 orchestrator.
- `src/spiketrace/active_learning_selection.py` and `_active_learning_selection_artifact.py` — add frozen-byte selection validation while preserving path APIs.
- `src/spiketrace/dual_crop_review.py` — add a frozen-byte verifier used by the selection adapter.
- `src/spiketrace/cli.py` — add `apply-active-review-v2`.
- `tests/test_active_learning_review.py` — v1 regression and v2 integration/CLI tests.
- `tests/test_active_learning_selection.py` and `tests/test_dual_crop_review.py` — byte/path verifier equivalence tests.
- `.gitattributes`, `.gitignore`, `README.md`, and `docs/PROJECT_PLAN.md`.

**Explicitly unchanged:**

- `src/spiketrace/constants.py`, `domain.py`, `events.py`, `outputs.py`, `training.py`, and model architecture code.
- The source workbook, selection JSON, proxies, merged candidates, base 90-row manifest, and v1 results.
- Detection, tracking, jersey OCR, SQLite, accounts, and product UI runtime.

---

### Task 1: Share Immutable Node I/O Without V1 Drift

**Files:**

- Create: `tools/active_review_io.mjs`
- Create: `tools/test_active_review_evidence.mjs`
- Modify: `tools/extract_active_review_results.mjs:1`
- Modify: `tools/verify_active_review_batch.mjs:1`
- Modify: `tools/test_active_review_batch.mjs:1`

**Interfaces:**

```text
sha256(bytes: Uint8Array) -> lowercase hex
sha256File(filePath, options) -> Promise<lowercase hex>
readInputSnapshot(filePath, label) -> Promise<{path, bytes, sha256}>
normalizeRepoPath(value, repoRoot) -> repository-relative POSIX path
assertStableInput(filePath, expectedBytes, label) -> Promise<void>
parseJsonObjectStrict(bytes, label) -> duplicate-key-free object
publishJsonNoReplace(
  outputPath,
  payload,
  {io, beforePublish}
) -> Promise<Uint8Array>
```

`verify_active_review_batch.mjs` continues re-exporting `sha256File`. `extractActiveReviewResults(selectionPath, workbookPath, outputPath, options)` keeps its signature, object shape, two-space indentation, and final LF.

- [ ] **Step 1: Freeze current v1 bytes in a failing focused test**

Build `expectedDraft` independently from the selection fixture and assert exact bytes:

```js
const expectedBytes = Buffer.from(
  `${JSON.stringify(expectedDraft, null, 2)}\n`,
  "utf8",
);
assert.deepEqual(await fs.readFile(outputPath), expectedBytes);
```

Then import the absent shared module so the first run fails with `ERR_MODULE_NOT_FOUND`.

- [ ] **Step 2: Run RED**

Run `& $env:SPIKETRACE_NODE tools\test_active_review_evidence.mjs`.

Expected: nonzero because `active_review_io.mjs` does not exist.

- [ ] **Step 3: Move only immutable-byte helpers**

Keep v1 row filtering and object construction in the extractor. `publishJsonNoReplace` exclusively creates a unique sibling, writes and syncs, re-reads bytes, awaits `beforePublish()` immediately before final publication, publishes without replacement, and removes only its own temporary file.

Implement `parseJsonObjectStrict` as a small recursive-descent structural scan followed by `JSON.parse`: object parsing decodes each quoted key with `JSON.parse(rawKeyToken)`, stores decoded keys in a per-object `Set`, and rejects a duplicate before parsing the value. Array/object nesting uses separate sets. The scanner consumes the complete UTF-8 text and rejects invalid JSON before `JSON.parse` runs.

- [ ] **Step 4: Add failure and race assertions**

Use a raw duplicate fixture:

```js
const duplicate = Buffer.from(
  '{"selection":{"path":"a"},"selection":{"path":"b"}}',
  "utf8",
);
assert.throws(
  () => parseJsonObjectStrict(duplicate, "override"),
  /override contains duplicate key "selection"/,
);
```

Inject temporary open/write/sync/re-read/publication failure and `beforePublish` rejection. Assert a competing target remains unchanged and no temporary sibling leaks.

- [ ] **Step 5: Run GREEN**

```powershell
& $env:SPIKETRACE_NODE tools\test_active_review_evidence.mjs
& $env:SPIKETRACE_NODE tools\test_active_review_batch.mjs
```

Expected: both exit `0` and the v1 bytes remain exact.

- [ ] **Step 6: Commit**

```powershell
git add tools/active_review_io.mjs tools/test_active_review_evidence.mjs tools/extract_active_review_results.mjs tools/verify_active_review_batch.mjs tools/test_active_review_batch.mjs
git commit -m "refactor: share immutable active review io"
```

---

### Task 2: Validate the Hash-Bound Evidence Override

**Files:**

- Create: `tools/active_review_evidence_overrides.mjs`
- Modify: `tools/test_active_review_evidence.mjs`

**Interfaces:**

```text
loadEvidenceOverrideEnvelope(
  overridePath,
  {
    overrideBytes,
    selection,
    selectionPath,
    selectionBytes,
    workbookPath,
    workbookBytes,
    repoRoot
  }
) -> Promise<BoundEvidenceOverrides>

validateEvidenceOverrideReferences(
  boundOverrides,
  {selection, canonicalActionRows}
) -> ValidatedEvidenceOverrides
```

The exact envelope fields are:

```json
{
  "format": "spiketrace.active-review-evidence-overrides",
  "format_version": 1,
  "review_set_key": "rangitoto-taka-national-final/round-01",
  "batch_id": "selection batch_id",
  "round_id": "round-01",
  "selection": {"path": "data/active-learning/rangitoto/round-01-selection.json", "sha256": "lowercase sha256"},
  "workbook": {"path": "outputs/active-learning/rangitoto/round-01/review.xlsx", "sha256": "lowercase sha256"},
  "video": {"path": "selection video path", "sha256": "selection video sha256"},
  "workbook_compatibility": {
    "trimmed_banner_cells": [],
    "shared_formula_ranges": [],
    "validation_import_gaps": [],
    "read_only_repairs": []
  },
  "action_overrides": [],
  "supplemental_actions": [],
  "outcome_observations": [],
  "visibility_observations": [],
  "action_participants": []
}
```

Compatibility permissions come only from this exact-hash-bound envelope. Callers cannot pass a generic weakening profile.

`BoundEvidenceOverrides` has exact properties `payload`, `overridePath`, `overrideRepoPath`, `overrideBytes`, `overrideSha256`, `selectionBinding`, `workbookBinding`, and `videoBinding`. `ValidatedEvidenceOverrides` adds exact tuples `sourceRepairs`, `actionOverrides`, `supplementalActions`, `outcomes`, `visibilityObservations`, and `participants` while retaining `bound`.

Compatibility arrays use these exact records:

```text
trimmed banner:
  {sheet, cell, expected_value, actual_value}
shared formula:
  {sheet, range, block_size, expected_display_value}
validation gap:
  {sheet, range, validation_kind, expected_rule}
read-only repair:
  {clip_id, source_action_slot, sheet, cell, field,
   original_value, normalized_value, reason}
```

Override entities use exact fields:

```text
action override:
  {action_ref, expected_source, replacement_review_label, visibility,
   evidence_basis, replacement_note, reason}
expected_source:
  {review_label, relative_start_seconds, relative_end_seconds,
   team_side, note}
supplemental action:
  {supplemental_index, clip_id, review_label, relative_start_seconds,
   relative_end_seconds, team_side, visibility, evidence_basis,
   interval_scope, note, reason}
outcome:
  {outcome_index, related_action_refs, outcome, result_type,
   evidence_basis, status, note}
visibility:
  {visibility_index, event_kind, clip_id, team_side,
   relative_start_seconds, relative_end_seconds, interval_scope,
   related_action_refs, note, reason}
participant:
  {action_ref, track_id, identity_ref, player_number, participation,
   touch_status, assignment_status, assignment_confidence, evidence}
participant evidence:
  {kind, source_ref, value, confidence}
```

Task 2 is self-contained and validates these exact finite vocabularies:

```text
review_label: background | serve | receive | set | attack | block | dig | free_ball
team_side: far | near
visibility: direct_clear | direct_partial | fully_occluded | off_camera | unresolved
evidence_basis: direct_video | referee_signal | scoreboard | sequence_context | mixed
outcome: continued | point_won | point_lost | unknown
outcome status: observed_or_inferred | unresolved
event_kind: occlusion | off_camera
interval_scope: timed | clip_bounds
participation: primary_actor | block_attempt | support
touch_status: touched | no_touch | unknown
assignment_status: confirmed | candidate | unresolved
participant evidence kind: manual_review | track | jersey_ocr | roster
```

All Task 2 objects reject unknown or missing fields. JSON booleans never satisfy integer/number fields. Every number is finite; every ordinal/index is an integer greater than zero; every relative second is a nonnegative integer. `Text` means a JSON string, including the empty string. `NonEmptyText` means a string with non-whitespace content and no C0 control character. `RepoPath` means the exact repository-relative POSIX output of `normalizeRepoPath` with no empty, `.` or `..` segment. `Sha256` is exactly 64 lowercase hexadecimal characters. Every field marked nullable must still be present with JSON `null`; null is an exact value, never a wildcard.

The root and binding schemas are exact:

```text
ArtifactBinding = {
  path: RepoPath,
  sha256: Sha256
}

EvidenceOverrideEnvelope = {
  format: "spiketrace.active-review-evidence-overrides",
  format_version: integer 1,
  review_set_key: NonEmptyText in the form "<non-slash name>/<round_id>",
  batch_id: NonEmptyText equal to selection.batch_id,
  round_id: NonEmptyText matching round-[0-9]{2} and equal to selection.round_id,
  selection: ArtifactBinding equal to the supplied frozen selection,
  workbook: ArtifactBinding equal to the supplied frozen workbook,
  video: ArtifactBinding equal to selection.video path/SHA-256,
  workbook_compatibility: WorkbookCompatibility,
  action_overrides: ActionOverride[],
  supplemental_actions: SupplementalAction[],
  outcome_observations: OutcomeOverride[],
  visibility_observations: VisibilityOverride[],
  action_participants: ParticipantOverride[]
}
```

All listed root and compatibility arrays are required and may be empty. Compatibility records use:

```text
WorkbookCompatibility = {
  trimmed_banner_cells: TrimmedBanner[],
  shared_formula_ranges: SharedFormulaRange[],
  validation_import_gaps: ValidationGap[],
  read_only_repairs: ReadOnlyRepair[]
}

TrimmedBanner = {
  sheet: one of the four exact workbook sheet names,
  cell: uppercase A1 cell reference,
  expected_value: Text,
  actual_value: Text equal to expected_value.trimEnd() and not equal to expected_value
}

SharedFormulaRange = {
  sheet: one of the four exact workbook sheet names,
  range: uppercase A1 range,
  block_size: positive integer,
  expected_display_value: NonEmptyText
}

ValidationGap = {
  sheet: "人工动作",
  range: one of E4:E483 | F4:G483 | H4:H483,
  validation_kind: list | whole,
  expected_rule: the exact range-specific object listed below
}

ReadOnlyRepair = {
  clip_id: NonEmptyText naming a selected clip,
  source_action_slot: integer in [1, 12],
  sheet: "人工动作",
  cell: uppercase A1 cell reference for that clip/slot row,
  field: "clip_id",
  original_value: null,
  normalized_value: NonEmptyText exactly equal to clip_id,
  reason: NonEmptyText
}
```

Compatibility arrays reject duplicate `(sheet, cell)`, `(sheet, range)`, and `(clip_id, source_action_slot, field)` keys respectively. A banner permission authorizes only the stated `trimEnd()` difference. A validation permission authorizes only a missing importer representation when the OOXML rule equals the exact declared rule. A repair authorizes only the stated null-to-selected-clip-ID normalization.

Action records use:

```text
ExpectedSource = {
  review_label: ReviewLabel,
  relative_start_seconds: WholeSecond | null,
  relative_end_seconds: WholeSecond | null,
  team_side: TeamSide | null,
  note: Text | null
}

ActionOverride = {
  action_ref: NonEmptyText matching <selected-clip-id>/action-<three-digit slot>,
  expected_source: ExpectedSource,
  replacement_review_label: ReviewLabel | null,
  visibility: Visibility | null,
  evidence_basis: EvidenceBasis | null,
  replacement_note: Text | null,
  reason: NonEmptyText
}

SupplementalAction = {
  supplemental_index: positive integer,
  clip_id: NonEmptyText naming a selected clip,
  review_label: ReviewLabel,
  relative_start_seconds: WholeSecond | null,
  relative_end_seconds: WholeSecond | null,
  team_side: TeamSide,
  visibility: Visibility,
  evidence_basis: EvidenceBasis,
  interval_scope: IntervalScope,
  note: Text,
  reason: NonEmptyText
}
```

Expected-source times are either both null or both integers. Non-`background` expected sources require `start < end` within the selected clip; `background` may use that timed form or two nulls. Expected-source `team_side=null` and `note=null` mean the raw workbook cells are blank. Each action override must change at least one of label, visibility, evidence basis, or note: null replacement values mean “retain the source/default value,” while `replacement_note=""` explicitly clears a note. Action override refs are unique.

For supplemental actions, `interval_scope=timed` requires `0 <= start < end <= clip duration`; `interval_scope=clip_bounds` requires both times null and visibility in `fully_occluded | off_camera | unresolved`. Supplemental `background` is allowed only with `interval_scope=timed`. Supplemental indexes are unique and contiguous per clip, and the derived supplemental refs are unique across the envelope.

Outcome and visibility records use:

```text
OutcomeOverride = {
  outcome_index: positive integer,
  related_action_refs: NonEmptyText[],
  outcome: Outcome,
  result_type: null | string matching [a-z][a-z0-9_]{0,63},
  evidence_basis: EvidenceBasis,
  status: OutcomeStatus,
  note: Text
}

VisibilityOverride = {
  visibility_index: positive integer,
  event_kind: EventKind,
  clip_id: NonEmptyText naming a selected clip,
  team_side: TeamSide,
  relative_start_seconds: WholeSecond | null,
  relative_end_seconds: WholeSecond | null,
  interval_scope: IntervalScope,
  related_action_refs: NonEmptyText[],
  note: Text,
  reason: NonEmptyText
}
```

Both related-ref arrays may be empty (clips `007`/`014` may preserve an outcome without inventing a target-team action), but any present refs are unique and must resolve after source and supplemental refs exist. An unresolved outcome requires `outcome=unknown` and `result_type=null`. Visibility timing follows the same `timed`/`clip_bounds` pair rules and clip bounds as supplemental actions. Outcome and visibility indexes are unique, result-wide, and contiguous in array order.

Participant records use:

```text
ParticipantEvidence = {
  kind: ParticipantEvidenceKind,
  source_ref: NonEmptyText,
  value: NonEmptyText,
  confidence: finite number in [0, 1] | null
}

ParticipantOverride = {
  action_ref: NonEmptyText resolving to a source or supplemental action,
  track_id: NonEmptyText | null,
  identity_ref: NonEmptyText | null,
  player_number: NonEmptyText | null,
  participation: Participation,
  touch_status: TouchStatus,
  assignment_status: AssignmentStatus,
  assignment_confidence: finite number in [0, 1] | null,
  evidence: ParticipantEvidence[]
}
```

Participant evidence may be empty and rejects duplicate `(kind, source_ref, value)` tuples. `confirmed` requires nonnull identity, player number, and confidence. `candidate` requires nonnull confidence and at least one of track, identity, or player number. `unresolved` requires null identity, player number, and confidence but may retain a track. Within one action, exact duplicate participant relations, duplicate nonnull track IDs, and duplicate nonnull identity refs are rejected. The complete participant array may be empty.

The three allowed validation-gap records are exact:

```text
人工动作!E4:E483:
  validation_kind=list
  expected_rule={type: list, values: [background, serve, receive, set, attack, block, dig]}
人工动作!F4:G483:
  validation_kind=whole
  expected_rule={type: whole, operator: greaterThanOrEqual, formula1: 0}
人工动作!H4:H483:
  validation_kind=list
  expected_rule={type: list, values: [far, near]}
```

No other key is allowed inside `expected_rule`. Participant evidence requires nonempty `source_ref` and `value`; `confidence` is null or a finite number in `[0,1]`. `result_type` is null or lowercase snake case matching `[a-z][a-z0-9_]{0,63}`.

`expected_source` is compared field-for-field against the canonical row's `raw_values`, not `normalized_values`; no trimming, side inheritance, or coercion occurs before the comparison. `supplemental_index` is contiguous from `1` independently within each `clip_id`. `outcome_index` is contiguous from `1` across the complete `outcome_observations` array, and `visibility_index` is contiguous from `1` across the complete `visibility_observations` array. Array order is authoritative for all three index checks.

`background_scope` is derived later as `clip_sentinel | timed_interval | null`. It is not accepted from the override envelope.

`interval_scope=timed` requires paired integer times. `interval_scope=clip_bounds` requires both relative times null. Optional replacement values are represented by explicit null fields rather than omitted keys, so unknown/missing-field validation remains exact. A confirmed participant requires a nonnull identity, player number, and confidence; a candidate may retain nullable identity/number; unresolved requires all three assignment values null.

- [ ] **Step 1: Write strict-envelope RED tests**

Construct the minimal envelope with every array present and use `parseJsonObjectStrict`. Include the raw duplicate-key fixture from Task 1 plus one unknown field per nesting level. Cover malformed SHA, path escape, wrong batch/round, stale source hashes, duplicate targets, missing reason, and unknown enums. Expected errors name the complete field path, such as `Evidence override action_overrides[0].visibility is invalid.`

- [ ] **Step 2: Run RED**

Run `& $env:SPIKETRACE_NODE tools\test_active_review_evidence.mjs`.

Expected: import failure for `active_review_evidence_overrides.mjs`.

- [ ] **Step 3: Implement exact source binding**

Resolve paths inside `repoRoot` and require the supplied selection/workbook/override snapshots plus selection video metadata to match. Parse the supplied `overrideBytes` through `parseJsonObjectStrict`; do not reopen the override path. Validate compatibility records by exact sheet/cell/range.

The known repair shape is:

```json
{
  "clip_id": "round-01-clip-002",
  "source_action_slot": 1,
  "sheet": "人工动作",
  "cell": "A16",
  "field": "clip_id",
  "original_value": null,
  "normalized_value": "round-01-clip-002",
  "reason": "只读短片编号在 Excel 编辑后为空，按选择文件固定行恢复"
}
```

Action overrides store a complete expected-source snapshot and may replace only label, visibility, evidence basis, or note; they cannot move original times.

- [ ] **Step 4: Add reference-level rejection tests**

Reject unknown clips/slots, duplicate repairs/targets, expected-source mismatch, non-contiguous supplemental ordinals, dangling outcome/visibility/action-participant refs, and malformed participant enums.

- [ ] **Step 5: Run GREEN**

Run `& $env:SPIKETRACE_NODE tools\test_active_review_evidence.mjs`.

Expected: exit `0`.

- [ ] **Step 6: Commit**

```powershell
git add tools/active_review_evidence_overrides.mjs tools/test_active_review_evidence.mjs
git commit -m "feat: validate active review evidence overrides"
```

---

### Task 3: Verify and Canonicalize Workbook Semantics

**Files:**

- Create: `tools/active_review_workbook_semantics.mjs`
- Modify: `tools/verify_active_review_batch.mjs:227`
- Modify: `tools/build_active_review_batch.mjs:47`
- Modify: `tools/test_active_review_evidence.mjs`
- Modify: `tools/test_active_review_batch.mjs`

**Interfaces:**

```text
readWorkbookSemanticSnapshot(workbook) -> Promise<WorkbookSemanticSnapshot>
verifyWorkbookSemanticSnapshot(
  snapshot,
  {selection, projection, workbookSha256, boundEvidenceOverrides}
) -> VerifiedWorkbookSnapshot
canonicalizeWorkbookActionRows(
  selection,
  verifiedActionRows,
  {boundEvidenceOverrides}
) -> {canonicalActionRows, normalizationAudit}
verifyWorkbookFile(
  selectionPath,
  workbookPath,
  {
    allowManualValues = false,
    selectionBytes = null,
    workbookBytes = null,
    boundEvidenceOverrides = null
  } = {}
) -> Promise<VerifiedActiveReviewWorkbook>
```

The public verifier keeps raw `actionRows` for v1 and adds hidden `canonicalActionRows`, `normalizationAudit`, `compatibilityAudit`, and `boundEvidenceOverrides`. Existing v1 calls use the four defaults above: strict blank manual cells, live selection/workbook reads, and no compatibility relaxations. Supplied byte snapshots replace live reads rather than being compared after a second read.

Neither semantic helper is exported, and neither accepts a raw compatibility object or repair array. Before calling them, public `verifyWorkbookFile` requires the nonnull `boundEvidenceOverrides.selectionBinding` and `.workbookBinding` to equal the normalized input paths and SHA-256 digests of the supplied frozen bytes. Only after that check does it pass the authenticated object to the module-private helpers, which derive compatibility permissions or source repairs internally from `boundEvidenceOverrides.payload.workbook_compatibility`. A null binding means no compatibility permission and no repair. This keeps the hash-bound envelope as the only weakening authority.

- [ ] **Step 1: Write semantic-snapshot RED tests**

Create snapshots for four `A2` banners with only trailing spaces removed; 40 formula blocks represented as expanded or shared formulas; the three known validation importer gaps; exact blank `人工动作!A16`; timed `background 0-9` followed by actions; and blank later sides following one explicit `near`. Include the real importer shapes:

```js
const sharedFormulaBlock = [
  {
    cell: "C28",
    formula: null,
    sharedFormula: { index: 2, ref: null, text: "" },
    displayedValue: "播放",
  },
  {
    cell: "C29",
    formula: '=HYPERLINK("clips/round-01-clip-003.mp4","播放")',
    sharedFormula: { index: 2, ref: "C28:C39" },
    displayedValue: "播放",
  },
];
const importedValidationGaps = {
  "人工动作!E4:E483": {},
  "人工动作!F4:G483": {},
  "人工动作!H4:H483": {},
};
```

Assert all relaxations fail without exact workbook hash and bound override.

- [ ] **Step 2: Run RED**

Run `& $env:SPIKETRACE_NODE tools\test_active_review_evidence.mjs`.

Expected: import failure for `active_review_workbook_semantics.mjs`.

- [ ] **Step 3: Implement fail-closed workbook equivalence**

Apply these exact rules:

1. trim only four listed banner cells and only with `trimEnd()`;
2. validate formulas per 12-row clip block without assuming the imported master is the first cell;
3. require every nonempty formula to equal the expected relative hyperlink;
4. require every C-column displayed value to be `播放`;
5. accept importer-side validation gaps only for the three OOXML-preserved ranges;
6. preserve raw workbook values before repair;
7. apply only repairs declared exactly by the bound envelope; the real fixture declares only `A16`.

Remove trailing spaces from the builder's four banner strings for new workbooks.

- [ ] **Step 4: Implement stable row canonicalization**

Every populated source row becomes:

```json
{
  "action_ref": "round-01-clip-024/action-005",
  "clip_id": "round-01-clip-024",
  "source_action_slot": 5,
  "source_row": 284,
  "raw_values": {
    "clip_id": "round-01-clip-024",
    "review_label": "attack",
    "relative_start_seconds": 11,
    "relative_end_seconds": 12,
    "team_side": null,
    "note": "垫过去的自由球算不算进攻"
  },
  "normalized_values": {
    "clip_id": "round-01-clip-024",
    "review_label": "attack",
    "relative_start_seconds": 11,
    "relative_end_seconds": 12,
    "team_side": "near",
    "note": "垫过去的自由球算不算进攻"
  },
  "background_scope": null,
  "side_inherited": true,
  "source_repairs": []
}
```

Side inheritance is a forward scan by original slot. A blank side inherits only after exactly one explicit side has appeared in that clip. A blank first populated side, zero sources, or prior `far`/`near` conflict fails.

An untimed `background` is `clip_sentinel` and must be the only populated row. A timed `background` is `timed_interval`, keeps integer times, may coexist with actions, and cannot overlap another same-side timed source row.

- [ ] **Step 5: Add exact tamper tests**

Assert an extra blank read-only ID, altered hyperlink display, unlisted banner edit, unexpected sheet, validation change outside the three ranges, conflicting side, and unauthorized repair fail. Assert raw `A16=null` remains audited while its normalized clip ID is restored.

- [ ] **Step 6: Run GREEN**

```powershell
& $env:SPIKETRACE_NODE tools\test_active_review_evidence.mjs
& $env:SPIKETRACE_NODE tools\test_active_review_batch.mjs
```

Expected: both exit `0`; v1 strict workbooks and output bytes remain compatible.

- [ ] **Step 7: Commit**

```powershell
git add tools/active_review_workbook_semantics.mjs tools/verify_active_review_batch.mjs tools/build_active_review_batch.mjs tools/test_active_review_evidence.mjs tools/test_active_review_batch.mjs
git commit -m "fix: verify review workbooks semantically"
```

---

### Task 4: Compose the Lossless Review Input V2

**Files:**

- Create: `tools/compose_active_review_evidence.mjs`
- Modify: `tools/active_review_evidence_overrides.mjs`
- Modify: `tools/test_active_review_evidence.mjs`

**Interfaces:**

```text
deriveResultSetId({
  batchId,
  roundId,
  selectionSha256,
  workbookSha256,
  evidenceOverridesSha256
}) -> string

composeEvidenceSynthesisInput({
  selection,
  canonicalActionRows,
  validatedOverrides,
  normalizationAudit
}) -> ActiveReviewEvidenceInputV2

composeActiveReviewEvidence(
  selectionPath,
  workbookPath,
  overridePath,
  outputPath,
  {repoRoot, io, afterVerification}
) -> Promise<ActiveReviewEvidenceInputV2>
```

`composeActiveReviewEvidence` is the only snapshot owner. It calls `readInputSnapshot` once for selection, workbook, and override; passes those exact bytes to the envelope loader and workbook verifier; derives result identity from those digests; and supplies a `beforePublish` callback that re-reads and byte-compares all three. No v2 helper reopens a source independently.

Its workbook call is exact:

```js
const verifiedWorkbook = await verifyWorkbookFile(selectionPath, workbookPath, {
  allowManualValues: true,
  selectionBytes: selectionSnapshot.bytes,
  workbookBytes: workbookSnapshot.bytes,
  boundEvidenceOverrides,
});
const validatedOverrides = validateEvidenceOverrideReferences(
  boundEvidenceOverrides,
  {
    selection: verifiedWorkbook.selection,
    canonicalActionRows: verifiedWorkbook.canonicalActionRows,
  },
);
const payload = composeEvidenceSynthesisInput({
  selection: verifiedWorkbook.selection,
  canonicalActionRows: verifiedWorkbook.canonicalActionRows,
  validatedOverrides,
  normalizationAudit: verifiedWorkbook.normalizationAudit,
});
```

`afterVerification(payload)` is a test hook invoked after semantic/reference composition and before output-byte rendering. After it returns, normal execution still performs the final byte-stability callback inside `publishJsonNoReplace`.

- [ ] **Step 1: Write deterministic composition RED tests**

Assert original refs use actual workbook slots, supplemental refs use `supplemental-001` with null slot, all raw/normalized values survive, Task 3 `normalizationAudit` is emitted byte-for-byte in source order, visibility `reason` becomes `source_reason`, and `training_decision` is absent because Python owns projection.

Use a fixture with an unoverridden direct action, off-camera override plus matching off-camera visibility interval, `attack -> free_ball` relabel, timed supplemental `free_ball`, `point_lost/free_ball_error` outcome, full-clip occlusion, and no participants.

- [ ] **Step 2: Run RED**

Run `& $env:SPIKETRACE_NODE tools\test_active_review_evidence.mjs`.

Expected: import failure for `compose_active_review_evidence.mjs`.

- [ ] **Step 3: Implement deterministic composition**

Unoverridden populated rows default to `direct_clear + direct_video`. Apply overrides only after expected-source comparison. Convert relative whole seconds to source-video seconds while preserving both. Keep action, outcome, visibility, and participant arrays separate.

The top-level fields are exactly:

```text
format, format_version, result_set_id, review_set_key, batch_id, round_id,
selection, workbook, evidence_overrides, video, time_precision_seconds,
source_review_rows, source_repairs, action_observations,
outcome_observations, visibility_observations, action_participants,
normalization_audit
```

Their format discriminators are exact:

```text
format: spiketrace.active-review-evidence-input
format_version: 2
time_precision_seconds: 1
```

Node composition tests assert all three literals, and Task 5 rejects any other string, version, boolean-as-version, or time precision.

The four top-level bindings have exact nested schemas:

```text
selection, workbook, evidence_overrides:
  {path, sha256}
video:
  {video_id, path, sha256, fps, frame_count, width, height,
   duration_seconds, crops}
video.crops:
  {far: [x1, y1, x2, y2], near: [x1, y1, x2, y2]}
```

Binding paths are repository-relative POSIX paths and hashes are lowercase SHA-256. The `video` object is the complete selection video object, not a reduced `{path, sha256}` binding. Crop coordinates and frame dimensions are nonnegative integers with `x1 < x2 <= width` and `y1 < y2 <= height`; FPS and duration are finite positive numbers.

Entity fields are exact:

```text
action observation:
  {action_ref, clip_id, source_action_slot, source_row, raw_values,
   normalized_values, review_label, relative_start_seconds,
   relative_end_seconds, start_seconds, end_seconds, team_side,
   visibility, evidence_basis, interval_scope, background_scope,
   side_inherited, note, source_reason, source_repairs}
outcome observation:
  {outcome_ref, related_action_refs, outcome, result_type,
   evidence_basis, status, note}
visibility observation:
  {visibility_ref, event_kind, clip_id, team_side, start_seconds,
   end_seconds, interval_scope, related_action_refs, note, source_reason}
action participant:
  {action_ref, track_id, identity_ref, player_number, participation,
   touch_status, assignment_status, assignment_confidence, evidence}
normalization audit entry:
  {kind, clip_id, action_ref, source_row, raw_value,
   normalized_value, reason}
```

`normalization_audit.kind` is exactly `read_only_repair | side_inheritance`, and `composeEvidenceSynthesisInput` emits the supplied Task 3 array unchanged and in source order. Source actions copy `interval_scope=timed` for timed rows and `interval_scope=null` only for an untimed `background_scope=clip_sentinel`; supplemental actions preserve their declared `timed | clip_bounds`. Source actions copy canonical `side_inherited`; supplemental actions set it to `false`. Unoverridden source actions use `source_reason=null`, while an action override or supplemental action copies its required override `reason` into `source_reason`. Every visibility override copies its required `reason` into its visibility observation `source_reason`; merging may not discard the bound source observation or its ref.

Supplemental refs are `<clip_id>/supplemental-<three-digit per-clip index>`. Outcome refs are `<result_set_id>/outcome-<three-digit result-wide index>` and visibility refs are `<result_set_id>/<event-kind>-source-<three-digit result-wide index>`. An action with `visibility=off_camera` or `fully_occluded` must be covered by a same-side explicit visibility observation of the corresponding kind; otherwise composition fails.

- [ ] **Step 4: Protect sources during publication**

Use the three snapshots described above for every parse and hash. Resolve the source-video path only from the frozen selection and require its expected hash to equal the bound override video hash. The publisher's `beforePublish` callback re-reads and byte-compares all three snapshots, then streams the video through `sha256File` and compares it with the frozen selection hash immediately before publication. Reject an existing output and remove only this invocation's temporary sibling.

- [ ] **Step 5: Add trust-boundary tests**

Cover stale selection/workbook/override bytes mutated by `afterVerification`, source-video bytes mutated by `afterVerification`, duplicate/unknown refs, supplemental bounds outside the clip, time movement of an original row, uncovered off-camera action, malformed outcome/participant relation, output collision, injected write/sync/publish failure, and deterministic bytes across distinct temporary paths. Every mutation case leaves the target absent and leaks no temporary sibling.

Add `main(argv)` with exactly four positional arguments and this failure contract:

```js
assert.equal(
  usage,
  "Usage: node tools/compose_active_review_evidence.mjs " +
    "SELECTION_JSON REVIEW_XLSX EVIDENCE_OVERRIDES_JSON OUTPUT_JSON",
);
```

Direct execution writes one compact JSON summary line to stdout; validation errors write the stack/message to stderr and set exit code `1`. Exported functions do not call `process.exit`.

Add `--real SELECTION WORKBOOK OVERRIDES REVIEW_INPUT` handling to `tools/test_active_review_evidence.mjs`. It reads the four supplied artifacts, binds the first three snapshots through `loadEvidenceOverrideEnvelope`, calls `verifyWorkbookFile` with `allowManualValues: true` and the frozen bytes, calls `validateEvidenceOverrideReferences(boundEvidenceOverrides, {selection: verifiedWorkbook.selection, canonicalActionRows: verifiedWorkbook.canonicalActionRows})`, and passes that exact result plus `verifiedWorkbook.normalizationAudit` to the pure `composeEvidenceSynthesisInput`. It renders these exact bytes and compares them with the existing `REVIEW_INPUT`:

```js
const rendered = Buffer.from(
  `${JSON.stringify(payload, null, 2)}\n`,
  "utf8",
);
assert.deepEqual(rendered, reviewInputBytes);
```

The real-data path never calls `composeActiveReviewEvidence`, never invokes a publisher, and asserts the real counts and hashes listed in Task 9.

- [ ] **Step 6: Run GREEN**

```powershell
& $env:SPIKETRACE_NODE tools\test_active_review_evidence.mjs
& $env:SPIKETRACE_NODE tools\test_active_review_batch.mjs
```

Expected: both exit `0`.

- [ ] **Step 7: Commit**

```powershell
git add tools/compose_active_review_evidence.mjs tools/active_review_evidence_overrides.mjs tools/test_active_review_evidence.mjs
git commit -m "feat: compose evidence-aware review inputs"
```

---

### Task 5: Validate the V2 Python Contract

**Files:**

- Create: `src/spiketrace/_active_learning_review_contract.py`
- Create: `tests/test_active_learning_review_contract.py`
- Modify: `src/spiketrace/dual_crop_review.py:144`
- Modify: `src/spiketrace/active_learning_selection.py:77`
- Modify: `src/spiketrace/_active_learning_selection_artifact.py:294`
- Modify: `tests/test_dual_crop_review.py`
- Modify: `tests/test_active_learning_selection.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ArtifactBinding:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class VideoBinding:
    video_id: str
    path: str
    sha256: str
    fps: float
    frame_count: int
    width: int
    height: int
    duration_seconds: float
    crops: dict[str, tuple[int, int, int, int]]


@dataclass(frozen=True, slots=True)
class FrozenArtifact:
    absolute_path: Path
    repo_path: str
    raw: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class ReviewInputSnapshots:
    selection: FrozenArtifact
    review_input: FrozenArtifact
    workbook: FrozenArtifact
    evidence_overrides: FrozenArtifact
    merged_candidates: FrozenArtifact


@dataclass(frozen=True, slots=True)
class ReviewSourceHashes:
    selection_sha256: str
    workbook_sha256: str
    evidence_overrides_sha256: str
    review_input_sha256: str
    merged_candidates_sha256: str


@dataclass(frozen=True, slots=True)
class ValidatedReviewInput:
    result_set_id: str
    review_set_key: str
    batch_id: str
    round_id: str
    time_precision_seconds: int
    source_hashes: ReviewSourceHashes
    selection_binding: ArtifactBinding
    review_input_binding: ArtifactBinding
    workbook_binding: ArtifactBinding
    evidence_overrides_binding: ArtifactBinding
    merged_candidates_binding: ArtifactBinding
    video_binding: VideoBinding
    merged_candidates: dict[str, object]
    source_review_rows: tuple[dict[str, object], ...]
    source_repairs: tuple[dict[str, object], ...]
    action_observations: tuple[dict[str, object], ...]
    outcome_observations: tuple[dict[str, object], ...]
    visibility_observations: tuple[dict[str, object], ...]
    action_participants: tuple[dict[str, object], ...]
    normalization_audit: tuple[dict[str, object], ...]
```

Public functions:

```text
derive_result_set_id(
  batch_id,
  round_id,
  selection_sha256,
  workbook_sha256,
  evidence_overrides_sha256
) -> str

verify_dual_crop_review_bytes(
  merged_bytes,
  csv_bytes=None
) -> dict[str, object]

validate_merged_review_source_bytes(
  merged_bytes,
  *,
  merged_repo_path,
  repo_root,
  require_video
) -> dict[str, object]

validate_review_selection_payload(
  payload,
  verified_merged,
  repo_root,
  require_video
) -> dict[str, object]

load_review_selection_bytes(
  selection_bytes,
  *,
  merged_bytes,
  merged_repo_path,
  repo_root,
  require_video=True
) -> dict[str, object]

snapshot_review_sources_v2(
  review_input_path,
  selection_path,
  repo_root
) -> ReviewInputSnapshots

load_review_sources_v2(
  snapshots,
  selection
) -> ValidatedReviewInput

assert_review_snapshots_stable(snapshots) -> None
```

`snapshot_review_sources_v2` reads selection and review input once, parses their frozen bytes only far enough to resolve the selected merged-candidate, workbook, and override paths, and freezes all five artifacts. The orchestrator calls `load_review_selection_bytes` with `snapshots.selection.raw`, `snapshots.merged_candidates.raw`, and `snapshots.merged_candidates.repo_path`; that function verifies the merged bytes through `verify_dual_crop_review_bytes`, builds the same `{merged, source, video, verification}` object as the path API through `validate_merged_review_source_bytes`, and passes it to `validate_review_selection_payload`. `load_review_sources_v2` accepts only that validated selection plus `ReviewInputSnapshots`, parses its own review/merged payloads from frozen bytes, and never reopens a path. `assert_review_snapshots_stable` compares all five live paths with their frozen bytes.

Refactor the existing path-based `verify_dual_crop_review`, `validate_merged_review_source`, and `load_review_selection` to read each current file once and then call the new byte/payload cores. Their return objects, errors, and bytes remain unchanged. `load_review_selection_bytes` verifies that the selection's `source.merged_json` equals `merged_repo_path` and its declared hash equals the digest of `merged_bytes`. Current v2 rejects nonempty `previous_selections` because those source bytes are not part of this result's snapshot set; a future round must extend `ReviewInputSnapshots` before accepting them.

- [ ] **Step 1: Write contract RED tests**

Use `unittest.TestCase` to assert the golden result-ID formula, exact fields, all five frozen artifact bindings, complete video binding, repository-relative paths, lowercase hashes, finite numbers, whole-second relative times, source/absolute bound equality, stable refs, and repair lineage.

In existing selection/dual-crop tests, assert path-based and byte-based verifiers return equal dictionaries for identical bytes. Mutate the live merged file after snapshot and assert projection still reads the frozen payload while `assert_review_snapshots_stable` rejects publication.

- [ ] **Step 2: Run RED**

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& $env:SPIKETRACE_PYTHON -m unittest tests.test_active_learning_review_contract -v
```

Expected: import failure because `_active_learning_review_contract.py` does not exist.

- [ ] **Step 3: Implement strict validation**

Reject unknown/missing fields, duplicate JSON keys, bool-as-integer, NaN/infinity, path escape, hash mismatch, repeated source slot/ref, non-contiguous supplemental refs, and dangling outcome/visibility/participant refs. Add a mutation test showing validation continues to use frozen bytes while `assert_review_snapshots_stable` detects the changed live file before publication.

Use these enums:

```text
review_label: background | serve | receive | set | attack | block | dig | free_ball
visibility: direct_clear | direct_partial | fully_occluded | off_camera | unresolved
evidence_basis: direct_video | referee_signal | scoreboard | sequence_context | mixed
outcome: continued | point_won | point_lost | unknown
outcome status: observed_or_inferred | unresolved
event_kind: occlusion | off_camera
interval_scope: timed | clip_bounds
participation: primary_actor | block_attempt | support
touch_status: touched | no_touch | unknown
assignment_status: confirmed | candidate | unresolved
```

`result_type` is null or lowercase snake case matching `[a-z][a-z0-9_]{0,63}`. `free_ball_error` requires `point_lost` related to a `free_ball`. Confirmed assignment requires confidence in `[0,1]`; unresolved assignment has null identity and player number.

- [ ] **Step 4: Add ambiguity tests**

Assert `mixed` parses but remains available for fail-closed projection; unknown enums fail. Assert a direct serve with referee-derived outcome remains a direct action and the outcome never edits action fields.

- [ ] **Step 5: Run tests and lint**

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& $env:SPIKETRACE_PYTHON -m unittest tests.test_active_learning_review_contract -v
& $env:SPIKETRACE_PYTHON -m unittest tests.test_dual_crop_review tests.test_active_learning_selection -v
.venv\Scripts\ruff.exe check src\spiketrace\_active_learning_review_contract.py tests\test_active_learning_review_contract.py
```

Expected: tests pass and Ruff exits `0`.

- [ ] **Step 6: Commit**

```powershell
git add src/spiketrace/_active_learning_review_contract.py src/spiketrace/dual_crop_review.py src/spiketrace/active_learning_selection.py src/spiketrace/_active_learning_selection_artifact.py tests/test_active_learning_review_contract.py tests/test_dual_crop_review.py tests/test_active_learning_selection.py
git commit -m "feat: validate evidence-aware review inputs"
```

---

### Task 6: Compose Actions, Outcomes, Visibility Events, and Participants

**Files:**

- Create: `src/spiketrace/_active_learning_review_observations.py`
- Create: `tests/test_active_learning_review_observations.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ActionObservation:
    action_ref: str
    clip_id: str
    source_action_slot: int | None
    source_row: int | None
    raw_values: dict[str, object]
    normalized_values: dict[str, object]
    review_label: str
    relative_start_seconds: int | None
    relative_end_seconds: int | None
    start_seconds: float | None
    end_seconds: float | None
    team_side: str
    visibility: str
    evidence_basis: str
    interval_scope: str | None
    background_scope: str | None
    side_inherited: bool
    note: str
    source_reason: str | None
    source_repairs: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class OutcomeObservation:
    outcome_ref: str
    related_action_refs: tuple[str, ...]
    outcome: str
    result_type: str | None
    evidence_basis: str
    status: str
    note: str


@dataclass(frozen=True, slots=True)
class VisibilityObservation:
    visibility_ref: str
    event_kind: str
    clip_id: str
    team_side: str
    start_seconds: float
    end_seconds: float
    interval_scope: str
    related_action_refs: tuple[str, ...]
    note: str
    source_reason: str


@dataclass(frozen=True, slots=True)
class VisibilityEvent:
    event_ref: str
    event_kind: str
    team_side: str
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    interval_scope: str
    related_action_refs: tuple[str, ...]
    source_refs: tuple[str, ...]
    source_intervals: tuple[tuple[float, float], ...]
    note: str


@dataclass(frozen=True, slots=True)
class ActionParticipant:
    action_ref: str
    track_id: str | None
    identity_ref: str | None
    player_number: str | None
    participation: str
    touch_status: str
    assignment_status: str
    assignment_confidence: float | None
    evidence: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class ObservationSet:
    result_set_id: str
    actions: tuple[ActionObservation, ...]
    outcomes: tuple[OutcomeObservation, ...]
    visibility_observations: tuple[VisibilityObservation, ...]
    occlusion_events: tuple[VisibilityEvent, ...]
    off_camera_events: tuple[VisibilityEvent, ...]
    participants: tuple[ActionParticipant, ...]
```

```text
compose_observation_set(
  review,
  selection
) -> ObservationSet

merge_visibility_events(
  observations,
  result_set_id,
  event_kind
) -> tuple[VisibilityEvent, ...]
```

`ObservationSet` contains immutable actions, outcomes, original visibility observations, merged occlusion events, merged off-camera events, and participants.
The module constant `_VISIBILITY_MERGE_GAP_SECONDS = 1.0` is format-v2 behavior and is not caller-configurable.

- [ ] **Step 1: Write observation RED tests**

Cover original/supplemental refs, one block with zero/one/multiple participants, separate outcome evidence, and source-order preservation. Multiple participant rows must never duplicate the action.

- [ ] **Step 2: Write exact merge RED tests**

Create same-side occlusions that overlap, are separated by exactly `1.0` second, and are separated by `1.01` seconds; also create simultaneous far-side occlusion and same-time off-camera interval. Expect transitive merge through one second, separation after `1.01`, side separation, and kind separation.

- [ ] **Step 3: Run RED**

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& $env:SPIKETRACE_PYTHON -m unittest tests.test_active_learning_review_observations -v
```

Expected: import failure because `_active_learning_review_observations.py` does not exist.

- [ ] **Step 4: Implement immutable composition**

The result set has one bound source video. Within it, group visibility observations by side and kind. Sort by start/end/ref and merge when:

```python
should_merge = next_start <= current_end + _VISIBILITY_MERGE_GAP_SECONDS
```

Union and sort related action refs and source refs. Preserve atomic source intervals in authority. A merged event uses `interval_scope=clip_bounds` if any source is clip-bounded, otherwise `timed`. Deduplicate notes in source-ref order and join them with ` | `. Generate stable merged refs in sorted order:

```text
<result_set_id>/occlusion-001
<result_set_id>/off-camera-001
```

Compute duration from merged bounds. Summary `affected_action_count` uses the distinct union across both event kinds.

- [ ] **Step 5: Add participant boundary tests**

Assert `block_attempt + no_touch` is valid, non-participants are absent, zero participants is valid, and confirmed/candidate rows remain relations rather than action-window copies.

- [ ] **Step 6: Run tests and lint**

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& $env:SPIKETRACE_PYTHON -m unittest tests.test_active_learning_review_observations -v
.venv\Scripts\ruff.exe check src\spiketrace\_active_learning_review_observations.py tests\test_active_learning_review_observations.py
```

Expected: tests pass and Ruff exits `0`.

- [ ] **Step 7: Commit**

```powershell
git add src/spiketrace/_active_learning_review_observations.py tests/test_active_learning_review_observations.py
git commit -m "feat: compose evidence-aware observations"
```

---

### Task 7: Derive Training Projection and Sentinel-Only Hard Negatives

**Files:**

- Create: `src/spiketrace/_active_learning_review_projection.py`
- Create: `tests/test_active_learning_review_projection.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class TrainingDecision:
    decision: str
    training_label: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class ProtectedInterval:
    source_ref: str
    clip_id: str
    team_side: str
    start_seconds: float
    end_seconds: float
    reason: str


@dataclass(frozen=True, slots=True)
class TrainingWindow:
    source_ref: str
    clip_id: str
    start_seconds: float
    end_seconds: float
    training_label: str
    review_label: str
    team_side: str
    crop: tuple[int, int, int, int]
    player_number: str | None
    generated: bool
    window_index: int | None
    source_top1_action: str | None
    source_top1_confidence: float | None
    note: str


@dataclass(frozen=True, slots=True)
class TrainingProjection:
    decisions: tuple[tuple[str, TrainingDecision], ...]
    human_windows: tuple[TrainingWindow, ...]
    protected_intervals: tuple[ProtectedInterval, ...]
    generated_background_windows: tuple[TrainingWindow, ...]
    positive_training_count: int
    requested_background_cap: int
    effective_background_cap: int
```

```text
derive_training_decision(action) -> TrainingDecision
build_protected_intervals(observations, selection) -> tuple[ProtectedInterval, ...]
project_training_windows(observations, selection) -> tuple[TrainingWindow, ...]
select_hard_negatives(
  selection,
  merged,
  observations,
  protected_intervals,
  guard_seconds,
  cap,
  seed
) -> tuple[TrainingWindow, ...]
build_training_projection(
  observations,
  selection,
  merged,
  background_guard_seconds,
  max_background_windows,
  background_seed
) -> TrainingProjection
```

- [ ] **Step 1: Write decision RED tests**

Assert:

```text
direct_clear/direct_video serve       -> eligible / serve
direct_partial/direct_video block     -> eligible / block
direct_clear/direct_video free_ball   -> eligible_as_background / background
direct_clear/direct_video timed bg    -> eligible / background
fully_occluded action                 -> excluded
off_camera action                     -> excluded
unresolved action                     -> excluded
referee/scoreboard/context action     -> excluded
mixed action evidence                 -> excluded
```

An untimed background sentinel produces no direct row.

- [ ] **Step 2: Write protection and donor RED tests**

Assert every timed human observation is protected, including excluded action, timed background, and free ball. Assert full occlusion, off-camera, and unresolved ranges are protected; `clip_bounds` protects the full clip.

The donor condition is exact:

```python
clip_actions == (one_untimed_background_sentinel,)
donor_side = one_untimed_background_sentinel.team_side
```

If either side of that clip contains any other populated action, neither side may donate.

- [ ] **Step 3: Run RED**

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& $env:SPIKETRACE_PYTHON -m unittest tests.test_active_learning_review_projection -v
```

Expected: import failure because `_active_learning_review_projection.py` does not exist.

- [ ] **Step 4: Implement fail-closed projection**

Use:

```python
visually_usable = action.visibility in {"direct_clear", "direct_partial"}
directly_based = action.evidence_basis == "direct_video"
if not visually_usable or not directly_based:
    decision = TrainingDecision("excluded", None, "insufficient_visual_evidence")
elif action.review_label == "free_ball":
    decision = TrainingDecision(
        "eligible_as_background",
        "background",
        "free_ball_projects_to_background",
    )
elif action.review_label == "background" and action.background_scope == "clip_sentinel":
    decision = TrainingDecision("excluded", None, "background_sentinel_only")
else:
    decision = TrainingDecision("eligible", action.review_label, "direct_visual")
```

Exactly one confirmed participant may project `player_number`. Zero or multiple confirmed participants keep it blank, and participant count never changes window count.

Human windows keep their action `source_ref` and set `window_index`, `source_top1_action`, and `source_top1_confidence` to null. A generated negative uses the stable source ref `<clip_id>/hard-negative-<side>-<window_index>` and preserves the exact nonnegative merged input-run `window_index`, seven-class `source_top1_action`, and finite `[0,1]` `source_top1_confidence` used for ranking.

- [ ] **Step 5: Implement safe hard negatives**

Verify the merged-candidate path/hash from selection. Rank non-background model top-1 first, then confidence, then the existing SHA tie-break. A candidate must be fully inside a sentinel-authorized clip/side, avoid same-side protected intervals plus guard, and avoid chosen same-side negatives. Different sides may share time.

Default/effective cap counts only trainable non-background actions; free ball and timed background do not increase it. Zero positive windows yield cap `0`.

- [ ] **Step 6: Add deterministic edge cases**

Cover full-clip occlusion eliminating negatives, far/near same-time legality across distinct donor clips, an opposite-side action disqualifying a would-be donor clip, action clips donating none, same-side conflicts, exact guard boundary, deterministic tie-break, and zero positives.

- [ ] **Step 7: Run tests and lint**

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& $env:SPIKETRACE_PYTHON -m unittest tests.test_active_learning_review_projection -v
.venv\Scripts\ruff.exe check src\spiketrace\_active_learning_review_projection.py tests\test_active_learning_review_projection.py
```

Expected: tests pass and Ruff exits `0`.

- [ ] **Step 8: Commit**

```powershell
git add src/spiketrace/_active_learning_review_projection.py tests/test_active_learning_review_projection.py
git commit -m "feat: project evidence-aware training windows"
```

---

### Task 8: Render and Atomically Publish the Six-File Bundle

**Files:**

- Create: `src/spiketrace/_active_learning_review_outputs.py`
- Create: `tests/test_active_learning_review_outputs.py`
- Modify: `src/spiketrace/active_learning_review.py:681`
- Modify: `src/spiketrace/cli.py:207`
- Modify: `tests/test_active_learning_review.py:1`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class BundleSettings:
    generator_version: str
    legacy_base_match_id: str
    review_match_id: str
    video_root_audit: dict[str, str]
    training_video_path: str
    source_video_file_checked: bool
    background_guard_seconds: float
    max_background_windows: int | None
    background_seed: int


@dataclass(frozen=True, slots=True)
class RenderedReviewBundle:
    authority: dict[str, object]
    artifacts: tuple[tuple[str, bytes], ...]
```

```text
render_result_bundle(
    *,
    review: ValidatedReviewInput,
    observations: ObservationSet,
    projection: TrainingProjection,
    base_fieldnames: Sequence[str],
    base_rows: Sequence[Mapping[str, str | None]],
    base_manifest_binding: ArtifactBinding,
    settings: BundleSettings,
) -> RenderedReviewBundle

validate_result_bundle(bundle_dir: str | Path) -> dict[str, object]

publish_result_bundle(
    output_dir: str | Path,
    bundle: RenderedReviewBundle,
    *,
    validate: Callable[[Path], dict[str, object]] = validate_result_bundle,
    before_publish: Callable[[], None] | None = None,
    io: object | None = None,
) -> None


apply_active_review_v2(
    base_manifest_path: str | Path,
    selection_path: str | Path,
    review_input_path: str | Path,
    output_dir: str | Path,
    *,
    repo_root: str | Path,
    legacy_base_match_id: str,
    review_match_id: str,
    video_root: str | Path | None = None,
    background_guard_seconds: float = 0.5,
    max_background_windows: int | None = None,
    background_seed: int | None = None,
    require_files: bool = True
) -> dict[str, object]
```

CLI:

```text
spiketrace apply-active-review-v2 BASE_MANIFEST SELECTION REVIEW_INPUT OUTPUT_DIR
  --repo-root PATH
  --legacy-base-match-id ID
  --review-match-id ID
  [--video-root PATH]
  [--background-guard-seconds FLOAT]
  [--max-background-windows INT]
  [--background-seed INT]
  [--allow-missing-videos]

spiketrace verify-active-review-bundle OUTPUT_DIR
```

Visibility merge gap is fixed at `1.0` by format v2 and is not a CLI setting.

- [ ] **Step 1: Write CSV-byte RED tests**

Assert each CSV starts with BOM bytes `EF BB BF`, uses only CRLF, has stable fields, and serializes lists as compact JSON. Participant output is exact header plus CRLF and zero data rows.

Observation fields:

```text
result_set_id,selection_sha256,workbook_sha256,generator_version,
observation_type,observation_ref,action_ref,clip_id,source_action_slot,
review_label,relative_start_seconds,relative_end_seconds,start_seconds,
end_seconds,team_side,visibility,evidence_basis,training_decision,outcome,
result_type,status,related_action_refs_json,note
```

Visibility fields:

```text
result_set_id,selection_sha256,workbook_sha256,generator_version,event_kind,
event_ref,team_side,start_seconds,end_seconds,duration_seconds,interval_scope,
related_action_refs_json,source_refs_json,note
```

Participant fields:

```text
result_set_id,selection_sha256,workbook_sha256,generator_version,action_ref,
track_id,identity_ref,player_number,participation,touch_status,
assignment_status,assignment_confidence,evidence_json
```

- [ ] **Step 2: Write authority and exports-manifest RED tests**

Build the four CSVs first. Build authority JSON with:

```text
format, format_version, result_set_id, content_sha256, batch_id, round_id,
generator_version, sources, source_review_rows, repairs,
action_observations, outcome_observations, occlusion_events,
off_camera_events, action_participants, protected_intervals,
training_projection, summary, exports
```

Authority discriminators are exactly `format="spiketrace.active-review-observations"` and integer `format_version=2`. The exports manifest discriminators are exactly `format="spiketrace.active-review-exports-manifest"` and integer `format_version=1`. Output tests and `validate_result_bundle` reject any other string, missing value, non-integer, or boolean-as-version.

The authority and exports-manifest `sources` object has exact fields `selection`, `review_input`, `workbook`, `evidence_overrides`, `merged_candidates`, `base_manifest`, `video`, and `verification`. The first six are exact `{path, sha256}` artifact bindings. `video` is the complete `VideoBinding` object. `verification` is exactly `{source_video_file_checked: bool}`; it is `false` only when `--allow-missing-videos` accepted an absent source video.

Compute `content_sha256` from the authority object before adding `content_sha256` and `exports`, using this exact canonical byte rule:

```python
semantic_bytes = json.dumps(
    semantic_authority,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8")
content_sha256 = hashlib.sha256(semantic_bytes).hexdigest()
```

There is no trailing newline in `semantic_bytes`. Add `content_sha256`, then add an exact `exports` object with fields `training_csv`, `observations_csv`, `visibility_events_csv`, `action_participants_csv`, and `manifest`. The first four values are `{path, sha256}` bindings to the exact rendered CSV bytes; `manifest` is exactly `{path: "round-01-exports.manifest.json"}` and has no hash. Render authority, then render the exports manifest with authority plus four CSV hashes, byte counts, encodings, and line endings. Every artifact entry has exact fields:

```text
path: repository-bundle-relative filename
media_type: application/json | text/csv
sha256: 64-character lowercase digest of exact bytes
bytes: non-negative integer byte length
encoding: utf-8 | utf-8-sig
line_ending: lf | crlf
data_rows: non-negative integer for CSV, null for JSON
entity_counts: null for CSV; for authority JSON an exact object containing
  action_observations, outcome_observations, occlusion_events,
  off_camera_events, action_participants, and training_rows
```

CSV entries use integer `data_rows` and null `entity_counts`. The exports manifest does not list itself. Its exact root fields are `format`, `format_version`, `result_set_id`, `content_sha256`, `generator_version`, `sources`, and `artifacts`; `artifacts` follows the fixed five-file order from the bundle section: authority, training, observations, visibility events, participants.

`validate_result_bundle` removes only `content_sha256` and `exports` from a copy of the parsed authority object, rerenders the remainder with the exact canonical rule above, and recomputes the digest. It requires that digest to equal both authority `content_sha256` and exports-manifest root `content_sha256`; copying a stale digest between the two files is not sufficient.

- [ ] **Step 3: Run RED**

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& $env:SPIKETRACE_PYTHON -m unittest tests.test_active_learning_review_outputs -v
```

Expected: import failure because `_active_learning_review_outputs.py` does not exist.

- [ ] **Step 4: Implement deterministic serialization**

Keep training CSV on the existing manifest schema. Bind it through exact SHA in authority/exports and source action refs in `training_projection`; do not add review-only columns to `load_manifest`.

Use `csv.DictWriter` with `lineterminator="\r\n"`, encode as `utf-8-sig`, and sort view rows by time/type/ref. JSON uses `ensure_ascii=False`, two-space indentation, and one LF.

- [ ] **Step 5: Implement true atomic no-replace publication**

Create one `.<output-name>.staging-<uuid>` under the destination parent. Exclusively create the six fixed filenames, write, flush, and `fsync`; then re-read and cross-validate all six. After staging validation succeeds, invoke `before_publish` exactly once, immediately before the no-replace directory rename. A callback failure removes this invocation's staging directory and leaves the final destination absent.

Implement `rename_directory_noreplace(source, destination)`:

- Windows: `MoveFileExW` through `ctypes` with flags `0`;
- Linux: `renameat2` through `ctypes` with `RENAME_NOREPLACE`;
- macOS: `renamex_np` through `ctypes` with `RENAME_EXCL`;
- unsupported platform: fail before publication.

Never use `os.replace`. Existing file, directory, empty directory, or symlink must remain unchanged. Failure removes only this invocation's staging directory.

- [ ] **Step 6: Add failure injection**

Inject every directory/file create, open, write, flush, fsync, re-read, validation, `before_publish`, and rename failure. Mutate each bound source from `before_publish` tests and assert no final directory appears. Test existing file, nonempty directory, empty directory, symlink, and concurrent publisher. Exactly one concurrent call may win; the loser preserves the winner and leaks nothing.

- [ ] **Step 7: Add v2 orchestration without touching v1**

`apply_active_review_v2` must:

1. freeze base-manifest bytes once and create its repository-relative `ArtifactBinding`;
2. call `snapshot_review_sources_v2` once, which freezes selection, review input, workbook, override, and merged candidates;
3. call `load_review_selection_bytes(snapshots.selection.raw, merged_bytes=snapshots.merged_candidates.raw, merged_repo_path=snapshots.merged_candidates.repo_path, repo_root=root, require_video=require_files)`;
4. if the selected video exists, verify its SHA by streaming and set `source_video_file_checked=true`; if it is absent, fail when `require_files=true`, otherwise set the flag to `false` without attempting a hash;
5. enforce split isolation;
6. call `load_review_sources_v2(snapshots, selection)` and never read the live merged path during projection;
7. compose observations;
8. build projection and hard negatives from `review.merged_candidates`;
9. reuse v1 base-row/match-ID/video-root/canonical-column behavior only for byte construction and precompute `training_video_path` with the existing portable-video-path rule;
10. create `BundleSettings`, including `training_video_path` and `source_video_file_checked`, and call the exact `render_result_bundle` interface with `base_manifest_binding`;
11. define the publication callback to call `assert_review_snapshots_stable`, byte-compare the live base manifest with its frozen bytes, and stream-rehash the video only when `source_video_file_checked=true`;
12. call `publish_result_bundle(..., before_publish=publication_callback)`; staging validation, the callback, and one no-replace rename form the success boundary, so the application performs no post-publication validation;
13. return `bundle.authority`, the same object serialized as authority JSON in the already validated staging bundle.

Keep `apply_active_review` and `apply-active-review` unchanged.

- [ ] **Step 8: Add CLI and integration tests**

Assert exact forwarding, both missing-video branches and their `source_video_file_checked` values, source mutation inside the pre-rename callback, output collision, both new CLI commands, training CSV reload through `load_manifest`, and returned-result equality.

`verify-active-review-bundle` calls `validate_result_bundle` read-only and prints its summary. It rejects an extra/missing file, BOM/line-ending mismatch, stale artifact hash, stale or copied-but-incorrect `content_sha256`, wrong `data_rows`/`entity_counts`, and cross-view result-ID mismatch.

Freeze a v1 fixture independently as `EXPECTED_V1_RESULT`, `EXPECTED_V1_MANIFEST_BYTES`, and `EXPECTED_V1_RESULTS_BYTES`. Run existing `apply_active_review` before and after v2 module imports and assert all three values remain byte-for-byte equal; merely keeping the old CLI name is insufficient.

- [ ] **Step 9: Run focused and regression gates**

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& $env:SPIKETRACE_PYTHON -m unittest tests.test_active_learning_review_contract tests.test_active_learning_review_observations tests.test_active_learning_review_projection tests.test_active_learning_review_outputs tests.test_active_learning_review -v
.venv\Scripts\ruff.exe check src\spiketrace tests
```

Expected: all tests pass and Ruff exits `0`.

- [ ] **Step 10: Commit**

```powershell
git add src/spiketrace/_active_learning_review_outputs.py src/spiketrace/active_learning_review.py src/spiketrace/cli.py tests/test_active_learning_review_outputs.py tests/test_active_learning_review.py
git commit -m "feat: publish evidence-aware review bundles"
```

---

### Task 9: Author and Compose Real Rangitoto Evidence

**Files:**

- Create: `data/active-learning/rangitoto/round-01-evidence-overrides.json`
- Create: `data/active-learning/rangitoto/round-01-review-v2.json`

**Prerequisite:** Tasks 1-8 are green. Do not edit workbook or selection JSON.

- [ ] **Step 1: Reconfirm immutable hashes**

```powershell
Get-FileHash -Algorithm SHA256 data\active-learning\rangitoto\round-01-selection.json
Get-FileHash -Algorithm SHA256 outputs\active-learning\rangitoto\round-01\review.xlsx
```

Expected: the two Global Constraint hashes.

- [ ] **Step 2: Encode workbook compatibility and normalization**

Bind the four banner cells, one shared-formula range with 40 blocks, three validation-import gaps, and exact `人工动作!A16` repair. The 42 forward side inheritances remain derived normalization and are not duplicated as action overrides.

- [ ] **Step 3: Inspect only evidence-sensitive clips and encode facts**

Use proxy/original video at whole-second precision:

- `009` and `010`: off-camera serves are excluded action evidence and each has a matching off-camera visibility interval;
- `011`: add visible ineffective block jump as supplemental `block`; participants remain empty;
- `017`: encode visible passive third-contact return as `free_ball`; if bounds lack visual support, encode unresolved clip-bounded observation rather than guessing;
- `018`: add direct `free_ball 8-9` and `point_lost/free_ball_error` outcome;
- `023`: exclude block cover from `dig` training;
- `024` slot 5: relabel `attack 11-12` to `free_ball` without moving time;
- `034`: keep visible serve trainable and save only scoring result as referee-inferred;
- `035`: add one full-clip occlusion and prohibit negatives;
- `007` and `014`: preserve supported outcomes only; invent no missed dig or target-team contact.

Every entry includes a Chinese reason grounded in reviewer notes. Add no jersey number.

- [ ] **Step 4: Compose v2 input**

```powershell
& $env:SPIKETRACE_NODE tools\compose_active_review_evidence.mjs data\active-learning\rangitoto\round-01-selection.json outputs\active-learning\rangitoto\round-01\review.xlsx data\active-learning\rangitoto\round-01-evidence-overrides.json data\active-learning\rangitoto\round-01-review-v2.json
```

- [ ] **Step 5: Run real-workbook acceptance**

```powershell
& $env:SPIKETRACE_NODE tools\test_active_review_evidence.mjs --real data\active-learning\rangitoto\round-01-selection.json outputs\active-learning\rangitoto\round-01\review.xlsx data\active-learning\rangitoto\round-01-evidence-overrides.json data\active-learning\rangitoto\round-01-review-v2.json
```

Assert:

- 40 clips and 83 populated source records;
- one `A16` repair;
- 42 inherited sides across 14 clips, all from earlier unique `near`;
- `round-01-clip-006/action-001` remains timed `background 0-9` beside slots 2-4;
- all 83 source refs are unique and have integer source slots;
- supplemental refs have null source slots;
- every action has explicit visibility and evidence;
- selection/workbook/override paths and hashes match;
- all source byte snapshots remain unchanged.

Also assert the real facts individually:

- clips `009` and `010` contain excluded off-camera serve actions plus linked `off_camera` visibility observations;
- clip `011` contains one supplemental visible `block` and zero participants;
- clip `017` contains either one timed visible `free_ball` or one clip-bounded unresolved observation, never a guessed timed action;
- clip `018` contains direct `free_ball 8-9` and linked `point_lost/free_ball_error`;
- clip `023` source block-cover row is excluded from `dig` training;
- clip `024` action slot 5 preserves `11-12` and changes only label to `free_ball`;
- clip `034` serve is direct/trainable while scoring outcome is referee-inferred;
- clip `035` has one full-clip occlusion and no negative-sampling authorization;
- clips `007` and `014` contain no invented target-team action.

- [ ] **Step 6: Commit Node-authored evidence only**

```powershell
git add data/active-learning/rangitoto/round-01-evidence-overrides.json data/active-learning/rangitoto/round-01-review-v2.json
git commit -m "data: compose Rangitoto evidence review input"
```

---

### Task 10: Apply and Validate the Durable Result Bundle

**Files:**

- Create: `data/annotations/rangitoto_round_01/round-01-results.json`
- Create: `data/annotations/rangitoto_round_01/action_training_round_01.csv`
- Create: `data/annotations/rangitoto_round_01/round-01-observations.csv`
- Create: `data/annotations/rangitoto_round_01/round-01-visibility-events.csv`
- Create: `data/annotations/rangitoto_round_01/round-01-action-participants.csv`
- Create: `data/annotations/rangitoto_round_01/round-01-exports.manifest.json`
- Modify: `.gitattributes`
- Modify: `.gitignore`

- [ ] **Step 1: Add deterministic repository policy**

Add:

```gitattributes
data/annotations/rangitoto_round_01/*.csv text eol=crlf
```

Allow only the durable directory and six fixed files through `.gitignore`. Videos, workbooks, proxies, and staging directories remain ignored.

- [ ] **Step 2: Confirm destination is absent**

Use `Get-Item -Force` and `Test-Path` to confirm `data/annotations/rangitoto_round_01` is absent as file, directory, or symlink. If anything exists there, stop and report the collision; changing this fixed durable path requires a revised plan and documentation contract.

- [ ] **Step 3: Apply review v2**

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& $env:SPIKETRACE_PYTHON -m spiketrace apply-active-review-v2 data\annotations\usa_germany_2024_annotations_expanded_batch_02.csv data\active-learning\rangitoto\round-01-selection.json data\active-learning\rangitoto\round-01-review-v2.json data\annotations\rangitoto_round_01 --repo-root . --legacy-base-match-id usa-germany-2024-olympics --review-match-id rangitoto-taka-national-final
```

Expected: one directory containing exactly six files.

- [ ] **Step 4: Audit through exports manifest**

Run the independent validator first:

```powershell
& $env:SPIKETRACE_PYTHON -m spiketrace verify-active-review-bundle data\annotations\rangitoto_round_01
```

Then assert:

- all five listed hashes, byte counts, encodings, and line endings match actual bytes;
- CSV `data_rows` exclude headers; authority has `data_rows=null` and exact `entity_counts`;
- authority, exports-manifest root, and every nonempty observations/visibility/participant row use the recomputed result ID;
- training CSV and the current header-only participant CSV are bound through their exact authority/exports hashes, with no metadata pseudo-row;
- source hashes match selection, workbook, override, review input, base manifest, merged candidates, and video metadata;
- participants CSV has zero rows;
- `free_ball` never appears as a model label;
- excluded refs never enter training;
- every generated negative comes from a sentinel-authorized clip/side;
- no negative overlaps same-side protection plus guard;
- occlusion and off-camera remain separate;
- affected-action count uses distinct refs;
- no player/participant was fabricated.

- [ ] **Step 5: Reconfirm source immutability**

Rehash workbook and selection and compare bytes with Task 9 snapshots. Load cumulative training CSV with `load_manifest` using the effective video root.

- [ ] **Step 6: Commit durable results**

```powershell
git add .gitattributes .gitignore data/annotations/rangitoto_round_01
git commit -m "data: publish Rangitoto evidence-aware results"
```

---

### Task 11: Synchronize README, Project Plan, and Verification

**Files:**

- Modify: `README.md`
- Modify: `docs/PROJECT_PLAN.md`

- [ ] **Step 1: Update program tree**

List four Python modules, four Node modules, tests, override/review-v2 inputs, and six-file result directory. Keep v1 tools documented as compatible.

- [ ] **Step 2: Remove stale user instructions**

Delete statements saying the user still needs to fill `review.xlsx` or run v1 extraction. State 40 clips and 83 records are complete, workbook remains immutable, and no further workbook action is required.

- [ ] **Step 3: Document confirmed semantics**

Document:

- `free_ball` is an action value, not a column or automatic error;
- action and outcome evidence are independent;
- visible `free_ball` projects to training `background` but remains `free_ball` in authority;
- continuous occlusion and off-camera are separate duration-bearing exports;
- protected intervals block false negatives;
- participant output is empty until identity confirmation;
- ineffective blocks belong only to actual participants;
- the compose command and single-output-directory apply command.

- [ ] **Step 4: Update project status and sequence**

Set `docs/PROJECT_PLAN.md` date to `2026-08-30`. Replace old “free ball as background plus note” with authority-label/training-projection split. Replace direct `ActionEvent.player_number` mutation with many-to-many `action_participants` plus exactly-one-confirmed compatibility projection.

Read actual counts/hashes from authority and exports manifest. Set next order:

1. finalize untouched full-match validation;
2. run efficient two-stage fine-tuning;
3. implement identity tracking and confirmed number assignment on selected rallies;
4. add SQLite and user review/statistics/export workflow.

- [ ] **Step 5: Run full verification**

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& $env:SPIKETRACE_PYTHON -m unittest tests.test_active_learning_review_contract tests.test_active_learning_review_observations tests.test_active_learning_review_projection tests.test_active_learning_review_outputs tests.test_active_learning_review -v
& $env:SPIKETRACE_PYTHON -m unittest discover -s tests -v
.venv\Scripts\ruff.exe check .
& $env:SPIKETRACE_PYTHON -m compileall -q src tests
& $env:SPIKETRACE_PYTHON -m spiketrace verify-active-review-bundle data\annotations\rangitoto_round_01
& $env:SPIKETRACE_NODE tools\test_active_review_evidence.mjs
& $env:SPIKETRACE_NODE tools\test_active_review_batch.mjs
& $env:SPIKETRACE_NODE tools\test_rangitoto_review.mjs
git diff --check
git status --short
```

Expected: tests pass, lint/compile and real-bundle validation are clean, Node exits `0`, no whitespace errors exist, and `git status --short` lists only `README.md` and `docs/PROJECT_PLAN.md` before their commit.

- [ ] **Step 6: Commit and push**

```powershell
git add README.md docs/PROJECT_PLAN.md
git commit -m "docs: hand off Rangitoto evidence review"
git push origin codex/rangitoto-active-learning-round-01
git status --short
git rev-parse HEAD
git rev-parse origin/codex/rangitoto-active-learning-round-01
```

Expected: status is empty and the two revision hashes match.

## Deferred Work

1. Independent full-match validation truth and model-quality reporting.
2. Two-stage action-model fine-tuning and checkpoint comparison.
3. Person detection, short-term tracking, jersey OCR, roster constraints, and nonempty participant assignments.
4. SQLite persistence, accounts/workspaces, statistics pages, and export UI.
5. A future workbook/front-end editor that writes v2 evidence fields directly.
