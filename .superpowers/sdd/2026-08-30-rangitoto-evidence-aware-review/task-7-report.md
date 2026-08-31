# Task 7 Report: Training Projection and Sentinel-Only Hard Negatives

## Implementation

- Added `src/spiketrace/_active_learning_review_projection.py` with immutable
  `TrainingDecision`, `ProtectedInterval`, `TrainingWindow`, and
  `TrainingProjection` records.
- Training decisions fail closed unless the action is directly visible in video.
  Direct `free_ball` preserves its authority label and projects only to training
  `background`; an untimed sentinel has no human training row.
- Every timed action is protected, irrespective of eligibility. Merged
  occlusion/off-camera ranges are protected too, with `clip_bounds` expanded to
  the selected clip bounds.
- Human windows remain one per eligible action. Only exactly one confirmed
  participant projects a player number.
- Hard negatives require an exact one-action clip containing an untimed
  `background` sentinel. Candidates stay within that clip and donor side,
  respect same-side protection plus guard and chosen-negative overlap, while
  allowing different sides to share time. Ranking is non-background top-1,
  confidence, then the established SHA-256 tie-break.
- Updated `README.md` only to list the new module and focused test. No bundle,
  CLI, or real Rangitoto-data application was added.

## TDD Evidence

### RED

Command:

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& '.venv\Scripts\python.exe' -m unittest tests.test_active_learning_review_projection -v
```

Raw output before production module creation:

```text
ImportError: Failed to import test module: test_active_learning_review_projection
ModuleNotFoundError: No module named 'spiketrace._active_learning_review_projection'
Ran 1 test in 0.000s
FAILED (errors=1)
```

`SPIKETRACE_PYTHON` was unset in this checkout, so the same command's executable
was supplied explicitly from `.venv` for the recorded RED and all later checks.

### GREEN

Command:

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& '.venv\Scripts\python.exe' -m unittest tests.test_active_learning_review_projection -v
```

Raw output:

```text
test_derives_fail_closed_training_decisions ... ok
test_untimed_background_sentinel_has_no_human_window ... ok
test_full_clip_occlusion_and_zero_positive_cap_eliminate_generated_negatives ... ok
test_hard_negative_ranking_is_non_background_confidence_then_stable_sha ... ok
test_hard_negatives_apply_same_side_guard_and_allow_other_side_same_time ... ok
test_only_an_exact_single_sentinel_clip_side_can_donate ... ok
test_projects_one_window_per_eligible_action_and_only_one_confirmed_player ... ok
test_clip_bounds_visibility_protects_entire_selected_clip ... ok
test_protects_all_timed_actions_and_visibility_ranges ... ok
Ran 9 tests in 0.001s
OK
```

## Regression and Lint

Task 6 command:

```powershell
& '.venv\Scripts\python.exe' -m unittest tests.test_active_learning_review_observations -v
```

Raw output: `Ran 6 tests in 0.000s` / `OK`.

Task 5 command:

```powershell
& '.venv\Scripts\python.exe' -m unittest tests.test_active_learning_review_contract -v
```

Raw output: `Ran 19 tests in 62.084s` / `OK`.

Lint command:

```powershell
.venv\Scripts\ruff.exe check src\spiketrace\_active_learning_review_projection.py tests\test_active_learning_review_projection.py
```

Raw output: `All checks passed!`

`git diff --check` produced no output (success).

## Files

- `README.md`
- `src/spiketrace/_active_learning_review_projection.py`
- `tests/test_active_learning_review_projection.py`
- `.superpowers/sdd/2026-08-30-rangitoto-evidence-aware-review/task-7-report.md`

## Self-Review and Concerns

- Confirmed the projection does not edit v1 APIs, Task 5/6 modules, action
  labels, model head, source workbooks, or the protected
  `tests/.active-review-evidence-root-*` directories.
- `selection` and frozen `merged` input are already validated/bound by the Task
  5 review contract. The pure projection module requires the selection's merged
  path/hash binding to be present but performs no live-file read, so it cannot
  bypass that frozen-input trust boundary.
- No remaining implementation blockers or known functional concerns.

## Fix Round 1: Review Findings

### Decision and root cause

- The trusted `merged` input is now the existing Task 5 `FrozenArtifact`, while
  retaining the parameter's position and name. A parsed dictionary cannot prove
  its origin after raw bytes and binding metadata have been discarded.
- Projection first checks `sha256(merged.raw) == merged.sha256`, then compares
  the frozen artifact's repository path and SHA-256 exactly with
  `selection.source`. It verifies `merged.raw` with
  `verify_dual_crop_review_bytes`, parses only that frozen byte payload, and
  never reads `merged.absolute_path`.
- Background candidates now share the same descending-confidence rank field as
  every candidate; the non-background priority remains first and SHA-256 remains
  the final stable tie-break.

### RED

Command:

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& '.venv\Scripts\python.exe' -m unittest \
  tests.test_active_learning_review_projection.TrainingProjectionTests.test_background_candidates_sort_by_confidence_before_sha_tie_break \
  tests.test_active_learning_review_projection.TrainingProjectionTests.test_rejects_substituted_frozen_merged_bytes_with_stale_hash \
  tests.test_active_learning_review_projection.TrainingProjectionTests.test_rejects_frozen_merged_path_or_hash_that_differs_from_selection \
  tests.test_active_learning_review_projection.TrainingProjectionTests.test_uses_frozen_bytes_when_live_merged_path_changes -v
```

Raw result before the fix: background ranking failed (`(7, 5) != (5, 7)`), and
all four frozen-artifact binding checks errored because the old implementation
called `_mapping(merged, "merged")` and raised `TypeError: merged must be an
object.`

### GREEN

Command:

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& '.venv\Scripts\python.exe' -m unittest tests.test_active_learning_review_projection -v
```

Raw output: `Ran 13 tests in 0.769s` / `OK`.

The new focused coverage proves high-confidence predicted-background selection
despite reverse SHA order; substituted raw bytes with a stale SHA fail; path and
SHA mismatches against selection fail; and a mutated live `absolute_path` does
not affect the projection because it consumes the retained raw bytes.

### Regression and checks

- Task 7 + Task 6: `Ran 19 tests in 0.785s` / `OK`.
- Task 5 contract: `Ran 19 tests in 62.992s` / `OK`.
- Ruff: `All checks passed!`
- `git diff --check`: no output (success).
