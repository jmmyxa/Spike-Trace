# Task 8 Report — Deterministic Six-File Review Bundle Publication

## Scope

Implemented the Task 8 output boundary in the isolated worktree at base `099bd63`:

- deterministic rendering of the six fixed v2 bundle files;
- byte-level and semantic cross-file validation;
- same-parent staging with exclusive file creation, flush/fsync, staging re-read, callback, and atomic no-replace directory publication;
- `apply_active_review_v2` orchestration over frozen review/base/video sources;
- `apply-active-review-v2` and `verify-active-review-bundle` CLI commands;
- independent frozen v1 compatibility fixtures;
- README clarification that Task 8 does not generate the real Rangitoto durable bundle.

The protected untracked directories `tests/.active-review-evidence-root-4BSyoz/` and
`tests/.active-review-evidence-root-X2MBGu/` were not edited or staged.

## Files

- Created `src/spiketrace/_active_learning_review_outputs.py`.
- Modified `src/spiketrace/active_learning_review.py`.
- Modified `src/spiketrace/cli.py`.
- Created `tests/test_active_learning_review_outputs.py`.
- Modified `tests/test_active_learning_review.py`.
- Created `tests/fixtures/active_review_v1_expected_manifest.csv`.
- Created `tests/fixtures/active_review_v1_expected_results.json`.
- Modified `README.md`.
- Modified `.gitattributes` to keep the byte-golden v1 CSV fixture at LF on every checkout.

## Output Contract

The renderer emits exactly:

1. `round-01-results.json`
2. `action_training_round_01.csv`
3. `round-01-observations.csv`
4. `round-01-visibility-events.csv`
5. `round-01-action-participants.csv`
6. `round-01-exports.manifest.json`

CSV files use UTF-8 BOM and CRLF, including normalized embedded field newlines. JSON uses UTF-8 without BOM, LF, and one trailing LF. The semantic `content_sha256` uses the brief's canonical compact JSON rule before `content_sha256` and `exports` are added.

Validation requires the exact six filenames and checks JSON schemas/discriminators, exact bytes/hashes/counts/encodings/line endings, canonical semantic content hash, export bindings, result/source identity, and row-by-row agreement between authority and all derived CSV views. Projected training rows are rebuilt from `authority.training_projection`, including authority-bound `training_video_path` and `review_match_id`; authority summary and entity counts are independently derived and require nonnegative integers excluding booleans.

## Publication Boundary

Publication creates one `.<name>.staging-<uuid>` directory under the destination parent. Each fixed artifact is exclusively created, written, flushed, fsynced, re-read, byte-compared, and validated. The source-stability callback runs exactly once immediately before the no-replace directory rename.

No-replace rename implementations are:

- Windows `MoveFileExW` with flags `0`;
- Linux `renameat2(..., RENAME_NOREPLACE)`;
- macOS `renamex_np(..., RENAME_EXCL)`.

The implementation never uses `os.replace`. Failure cleanup removes only the current invocation's staging directory. Tests inject create/open/write/flush/fsync/read/validation/callback/rename failures, preserve existing file/directory/symlink destinations, and verify two concurrent publishers produce exactly one winner without staging leaks.

## Orchestration

`apply_active_review_v2` freezes base-manifest bytes once and snapshots review sources once. Projection consumes the frozen merged-candidates artifact. Existing-video SHA verification and split isolation happen before render. The pre-rename callback revalidates all review snapshots, byte-compares the base manifest, and rehashes the video only when it was present and checked. The function returns the same `bundle.authority` object serialized into the validated staging bundle and performs no post-publication validation.

Lazy v2 imports keep the v1 load path isolated. The frozen v1 manifest fixture is 606 bytes with SHA-256 `e973abae145f3f22e45e332944db124af35c66b4ac9c6294dffa90283fdc6021`; the frozen v1 result fixture is 16114 bytes with SHA-256 `4190b9c87c4049da41621fb02cbb3c126177346e68407e8fe6c94c265d20e591`.

## TDD Evidence

Initial RED milestones:

```text
renderer: ModuleNotFoundError: spiketrace._active_learning_review_outputs
validator: missing validate_result_bundle
publication: missing publish_result_bundle
orchestrator: missing apply_active_review_v2
CLI: invalid command choice for both new commands
zero-cap guard: expected ActiveLearningError, none raised
rehash-divergent observation: validator accepted changed observation note
embedded newline: raw LF/CR remained inside CSV field
```

Review-follow-up RED for training semantics, summary, and strict entity counts:

```text
test_rejects_boolean_entity_count ... FAIL (ValueError not raised)
test_rejects_rehashed_training_view_that_diverges_from_authority ... FAIL (ValueError not raised)
test_rejects_rehashed_summary_that_diverges_from_authority_entities ... FAIL (ValueError not raised)
Ran 3 tests in 0.016s
FAILED (failures=3)
```

Independent v1 fixture mutation proof:

```text
time_precision_seconds deliberately changed from 1 to 9 in committed expected JSON
test_v1_result_and_bytes_do_not_drift_when_v2_output_module_is_imported ... FAIL
actual time_precision_seconds=1, expected=9
Ran 1 test in 0.033s
FAILED (failures=1)
```

Reviewer identity-bypass RED:

```text
field='video_path' ... FAIL (ValueError not raised)
field='match_id' ... FAIL (ValueError not raised)
Ran 1 test in 0.010s
FAILED (failures=2)
```

Targeted GREEN after authority-bound training identity:

```text
test_rejects_rehashed_training_identity_that_diverges_from_authority ... ok
test_rejects_rehashed_training_view_that_diverges_from_authority ... ok
test_accepts_complete_cross_validated_bundle_and_returns_summary ... ok
test_content_hash_is_derived_from_semantic_authority_and_exports_exact_bytes ... ok
Ran 4 tests in 0.019s
OK
All checks passed!
```

## Code Review

The first independent review found five gaps: semantic checks for observation-derived views, embedded CSV newlines, summary derivation, independent v1 fixtures, and strict entity-count types. Observation/visibility/participant rows are now rebuilt deterministically from authority; embedded newlines normalize to CRLF; summary is recomputed; v1 expectations are committed literal fixtures; booleans and other non-integers are rejected.

The first scoped re-check closed those findings and found that the training CSV label was not semantically compared. The validator now rebuilds projected training rows from authority. The next scoped re-check closed the four requested findings and found projected `video_path` and `match_id` were self-derived from the CSV. Both values are now stored in semantic authority metadata and used as the independent expected values. The following re-check confirmed that Important closed and found one zero-window early-return Minor; metadata validation now occurs before that return, with a base-only tamper regression. Final scoped review reported no Critical, Important, or Minor findings.

## Verification Evidence

Focused output/v1 verification before the final identity fix:

```text
Ran 22 tests in 0.236s
OK
```

Full focused five-module gate before the final identity fix:

```text
Ran 100 tests in 69.917s
OK
```

Scoped Ruff after all current fixes:

```text
All checks passed!
```

Repository-wide Ruff reports only the two protected untracked `make_video.py` scripts:

```text
tests/.active-review-evidence-root-4BSyoz/input/make_video.py: I001
tests/.active-review-evidence-root-X2MBGu/input/make_video.py: I001
Found 2 errors.
```

With those two protected directories explicitly excluded:

```text
All checks passed!
```

Final post-review focused gate:

```text
Ran 102 tests in 67.000s
OK
```

Final reviewer confirmation:

```text
zero-window/base-only empty identity tamper rejected
3 targeted tests passed
Ruff passed
No Critical, Important, or Minor findings
```

`python -m compileall -q` and `git diff --check` exited `0`. Final excluded Ruff, compileall, and diff checks were rerun immediately before commit.

## Concerns

- This task deliberately does not create or claim the final real Rangitoto six-file bundle; later work must run the new orchestration against the durable inputs.
- The two pre-existing protected evidence directories remain untracked and cause the only repository-wide Ruff findings; they are outside Task 8 scope and must not be staged.

## Formal Review Fix Round 1/5

Fix base: `61ff4ad`. This round addresses only the three formal-review Important findings. The separately recorded Minor about unused `BundleSettings` fields remains outside this fix loop for final aggregate review.

### 1. Base training prefix semantic binding

The original validator rebuilt only projected suffix rows. A test changed the first base row's `label`, `split`, `video_path`, or `match_id`, then refreshed the training export hash and manifest artifact metadata. All four variants were accepted.

RED command:

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
.venv\Scripts\python.exe -m unittest tests.test_active_learning_review_outputs.ResultBundleTamperValidationTests.test_rejects_rehashed_base_training_prefix_tampering -v
```

Raw RED result:

```text
field='label' ... FAIL — ValueError not raised
field='split' ... FAIL — ValueError not raised
field='video_path' ... FAIL — ValueError not raised
field='match_id' ... FAIL — ValueError not raised
Ran 1 test in 0.020s
FAILED (failures=4)
```

The semantic authority now contains exact `training_projection.base_training_view` fields `{fieldnames, data_rows, content_sha256}`. Rendering normalizes every frozen base row into its complete output field set after legacy match-ID fill, then hashes canonical JSON containing the ordered fieldnames and complete normalized prefix. Standalone validation uses the CSV header and prefix rows to independently recompute that semantic hash, requires strict field/count/hash types, and requires total training rows to equal base rows plus human/generated rows. It never opens the live base manifest.

A second RED showed that an empty base and empty projection could self-bootstrap authority fieldnames because no row exposed the real CSV header:

```text
test_rejects_rehashed_empty_base_training_header_tampering ... FAIL
AssertionError: ValueError not raised
```

CSV parsing now returns the actual header separately, so zero-row bundles also compare the header to authority. Both prefix and zero-row-header tamper tests are GREEN.

### 2. Strict VideoBinding and projection invariants

All tamper cases recomputed semantic `content_sha256`, synchronized manifest root sources/content hash, and refreshed authority artifact bytes/hash. Expected values were hand-derived from Task 5/7 contracts rather than generated with Task 7 production helpers.

RED command:

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
.venv\Scripts\python.exe -m unittest \
  tests.test_active_learning_review_outputs.ResultBundleTamperValidationTests.test_rejects_rehashed_invalid_video_binding_contract \
  tests.test_active_learning_review_outputs.ResultBundleTamperValidationTests.test_rejects_rehashed_invalid_projection_decision_relation \
  tests.test_active_learning_review_outputs.ResultBundleTamperValidationTests.test_rejects_rehashed_invalid_projection_windows_and_caps \
  tests.test_active_learning_review_outputs.ResultBundleTamperValidationTests.test_rejects_rehashed_player_projection_without_confirmed_participant -v
```

Raw RED result:

```text
VideoBinding: fps bool, zero duration, frame_count bool, zero width,
              bool crop coordinate, out-of-frame crop — all FAIL accepted
Decisions: duplicate, orphan, illegal vocabulary, missing — all FAIL
Windows/caps: human window_index, human top1, empty generated clip,
              negative generated index, negative requested cap,
              boolean effective cap — all FAIL accepted
Player projection without confirmed participant — FAIL accepted
Ran 4 tests in 0.069s
FAILED (failures=17)
```

The validator now independently enforces:

- exact repository-relative artifact/video paths, lowercase SHA-256, positive finite FPS/duration, positive non-boolean integer frame/dimensions, and exact bounded integer far/near crops;
- exact one-to-one decisions in action order, no duplicate/orphan/missing refs, and Task 7 decision/label/reason semantics reconstructed from serialized action evidence;
- human windows exactly reconstructed from eligible timed actions, video-side crops, and the one-confirmed-participant projection rule;
- generated-window exact fields, types, finite video bounds, background labels, side crop, stable source ref, donor sentinel, unique refs, nonnegative index, valid model top-1/confidence, and null player;
- strict nonnegative non-boolean positive/requested/effective counts with `effective=min(requested, positive)` and generated count bounded by effective cap.

Adding a valid sentinel donor to the synthetic output fixture exposed an existing renderer bug: untimed actions reached `float(None)` during CSV ordering. The valid-bundle test provided the RED error; both renderer and authority view ordering now sort null starts after timed rows.

GREEN evidence:

```text
Ran 4 tests in 0.068s
OK
Ran 30 tests in 0.295s
OK
```

### 3. Darwin capability preflight

The Darwin test mocks `sys.platform='darwin'` and a C library without `renamex_np`, supplies a counting publication IO, and asserts zero callback calls, zero IO operations, and no parent directory.

Raw RED result:

```text
test_macos_missing_renamex_fails_before_publication_side_effects ... FAIL
publication_io.trace contained 33 operations:
create_parent, create_staging, six create/write/flush/fsync sequences,
six reads, and rename
Ran 1 test in 0.016s
FAILED (failures=1)
```

`_require_noreplace_platform()` now resolves and configures `renamex_np` during entry preflight. Missing symbol or library raises before path calculation creates anything, IO, validation, or callback. The final rename reuses the same capability loader; Windows/Linux no-replace behavior remains unchanged.

GREEN result:

```text
test_macos_missing_renamex_fails_before_publication_side_effects ... ok
Ran 1 test in 0.003s
OK
```

### Round 1 regression gate

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
.venv\Scripts\python.exe -m unittest \
  tests.test_active_learning_review_contract \
  tests.test_active_learning_review_observations \
  tests.test_active_learning_review_projection \
  tests.test_active_learning_review_outputs \
  tests.test_active_learning_review
```

Raw result:

```text
Ran 109 tests in 67.091s
OK
```

Final round checks:

```text
Scoped Ruff: All checks passed!
Repository Ruff with both protected evidence roots excluded: All checks passed!
compileall: exit 0
git diff --check: exit 0
```

The unexcluded repository Ruff command still reports only the same two protected, untracked `make_video.py` import-order findings. Neither protected evidence root is changed or staged by this round.
