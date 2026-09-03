# Task 2 Implementer Report

## RED

Command:

```powershell
$env:PYTHONPATH='E:\Spike-Trace\.worktrees\socal-independent-validation\src'; .venv\Scripts\python.exe -m unittest tests.test_validation_rallies -v
```

Result: failed during import with `ModuleNotFoundError: No module named 'spiketrace.validation_rallies'`.

## GREEN

Command:

```powershell
$env:PYTHONPATH='E:\Spike-Trace\.worktrees\socal-independent-validation\src'; .venv\Scripts\python.exe -m unittest tests.test_validation_rallies tests.test_validation_contract -v
```

Result: `Ran 11 tests ... OK`.

## Implementation

Added deterministic motion candidate detection, complete rally/non-rally coverage, side-switch splitting with crop and geometry validation, queue JSON serialization/loading, and silent proxy publication with SHA-256 manifest. Reused the Task 1 validation and video contracts and preserved explicit source-root handling without recognition.

## Self-review

- Queue IDs and endpoint coverage are deterministic.
- Overlaps, invalid bounds, incomplete coverage, missing side mappings, and invalid crops raise `ValidationError`.
- Queue writes are no-overwrite via `write_new_bytes`; proxy output rejects pre-existing directories and publishes the manifest atomically.
- README and project plan document the module.

## Commit

Recorded after staging all requested files.

## Reviewer Fix Round 1

RED: added regressions for internal coverage gaps and non-finite duration; the pre-fix run failed `test_invalid_settings_rejected` (ValidationError not raised).

GREEN: after enforcing finite duration/settings, contiguous complete coverage, side interval overlap/team validation, and queue binding identity/version checks, `python -m unittest tests.test_validation_rallies -v` reports `Ran 4 tests ... OK`.

## Reviewer Fix Round 2

RED: added explicit-binding proxy regression; pre-fix behavior accepted proxy generation without a frozen source. GREEN: proxy generation now requires `binding`, resolves only `binding.repo_video_path` beneath explicit `video_root` (or the binding root), and validates identity. Raw candidate gaps are evaluated before buffering; queue loading validates shape, format, binding identity, and metadata with fail-closed `ValidationError`. Focused run: `Ran 5 tests ... OK`.

## Reviewer Fix Round 3

RED: `test_nonfinite_candidate_rejected` failed before implementation. GREEN: candidate endpoints now reject non-finite values before sorting, and queue loading validates required segment fields, status/boundary enums, and crop shape with fail-closed errors. Focused run: `Ran 6 tests ... OK`.

## Reviewer Fix Round 4

RED (side/crop validation):

```powershell
$env:PYTHONPATH='E:\Spike-Trace\.worktrees\socal-independent-validation\src'; .venv\Scripts\python.exe -m unittest tests.test_validation_rallies -v
```

Result before the fix: `test_side_map_rejects_missing_or_nonfinite_interval_fields` raised `KeyError`; non-integer crop values were silently coerced and malformed crop strings raised `ValueError` instead of `ValidationError`.

RED (candidate shape):

```powershell
$env:PYTHONPATH='E:\Spike-Trace\.worktrees\socal-independent-validation\src'; .venv\Scripts\python.exe -m unittest tests.test_validation_rallies.RallyQueueTests.test_malformed_candidate_shape_rejected -v
```

Result before the fix: malformed candidate tuples raised uncaught `ValueError` during unpacking.

RED (boolean setting): `test_detection_rejects_malformed_settings` produced an uncaught `VideoError` for `sample_seconds=True` because booleans were accepted as numeric settings.

RED (queue numeric tamper): the new queue tamper test failed because a serialized `NaN` segment start was accepted by `load_rally_queue`.

GREEN:

```powershell
$env:PYTHONPATH='E:\Spike-Trace\.worktrees\socal-independent-validation\src'; .venv\Scripts\python.exe -m unittest tests.test_validation_rallies tests.test_validation_contract -v
```

Result: `Ran 26 tests ... OK` (16 rally-queue tests, 10 adjacent validation-contract tests).

Implementation: added deterministic synthetic-video regressions for motion/dead-ball ordering and buffer clamping; malformed settings/candidate shape checks; side interval, crop type/shape, and non-finite validation; queue round-trip/no-overwrite, binding/metadata/shape tamper checks; proxy manifest/source-binding/rollback and pre-existing-directory checks. Production validation now wraps malformed candidate shapes, rejects non-finite queue bounds and invalid enums on load, and fail-closes side intervals/crops without coercion.

Self-review:

- Tests use 20-frame synthetic MJPG videos and mocked proxy writes; no SoCal source is opened or recognition run.
- Explicit `binding` remains required for proxies and source resolution stays constrained to the binding path/root.
- Atomic no-overwrite queue/manifest behavior and rollback semantics remain unchanged.
- No player tracking, OCR, database, frontend, or statistics changes.

Implementation commit: `08d4384` (round-four changes, including boolean-setting validation). This report is committed separately.

## Reviewer Fix Round 5

RED command:

```powershell
$env:PYTHONPATH='E:\Spike-Trace\.worktrees\socal-independent-validation\src'; .venv\Scripts\python.exe -m unittest tests.test_validation_rallies.RallyQueueTests.test_proxy_rejects_same_path_source_with_changed_content -v
```

RED output:

```text
test_proxy_rejects_same_path_source_with_changed_content (tests.test_validation_rallies.RallyQueueTests.test_proxy_rejects_same_path_source_with_changed_content) ... FAIL

======================================================================
FAIL: test_proxy_rejects_same_path_source_with_changed_content (tests.test_validation_rallies.RallyQueueTests.test_proxy_rejects_same_path_source_with_changed_content)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "E:\Spike-Trace\.worktrees\socal-independent-validation\tests\test_validation_rallies.py", line 223, in test_proxy_rejects_same_path_source_with_changed_content
    with self.assertRaises(ValidationError):
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: ValidationError not raised

----------------------------------------------------------------------
Ran 1 test in 0.010s

FAILED (failures=1)
```

GREEN command:

```powershell
$env:PYTHONPATH='E:\Spike-Trace\.worktrees\socal-independent-validation\src'; .venv\Scripts\python.exe -m unittest tests.test_validation_rallies.RallyQueueTests.test_proxy_rejects_same_path_source_with_changed_content -v
```

GREEN output:

```text
test_proxy_rejects_same_path_source_with_changed_content (tests.test_validation_rallies.RallyQueueTests.test_proxy_rejects_same_path_source_with_changed_content) ... ok

----------------------------------------------------------------------
Ran 1 test in 0.007s

OK
```

Focused suite command:

```powershell
$env:PYTHONPATH='E:\Spike-Trace\.worktrees\socal-independent-validation\src'; .venv\Scripts\python.exe -m unittest tests.test_validation_rallies tests.test_validation_contract -v
```

Focused suite output:

```text
test_complete_coverage_and_overlap_validation (tests.test_validation_rallies.RallyQueueTests.test_complete_coverage_and_overlap_validation) ... ok
test_dead_ball_merging_precedes_buffer_expansion (tests.test_validation_rallies.RallyQueueTests.test_dead_ball_merging_precedes_buffer_expansion) ... ok
test_detection_rejects_malformed_settings (tests.test_validation_rallies.RallyQueueTests.test_detection_rejects_malformed_settings) ... ok
test_invalid_settings_rejected (tests.test_validation_rallies.RallyQueueTests.test_invalid_settings_rejected) ... ok
test_malformed_candidate_shape_rejected (tests.test_validation_rallies.RallyQueueTests.test_malformed_candidate_shape_rejected) ... ok
test_motion_candidates_are_deterministic_and_clamped (tests.test_validation_rallies.RallyQueueTests.test_motion_candidates_are_deterministic_and_clamped) ... ok
test_nonfinite_candidate_rejected (tests.test_validation_rallies.RallyQueueTests.test_nonfinite_candidate_rejected) ... ok
test_proxy_manifest_metadata_and_decode_failure_rollback (tests.test_validation_rallies.RallyQueueTests.test_proxy_manifest_metadata_and_decode_failure_rollback) ... ok
test_proxy_preserves_preexisting_output_directory (tests.test_validation_rallies.RallyQueueTests.test_proxy_preserves_preexisting_output_directory) ... ok
test_proxy_rejects_binding_root_mismatch (tests.test_validation_rallies.RallyQueueTests.test_proxy_rejects_binding_root_mismatch) ... ok
test_proxy_rejects_same_path_source_with_changed_content (tests.test_validation_rallies.RallyQueueTests.test_proxy_rejects_same_path_source_with_changed_content) ... ok
test_proxy_requires_explicit_binding (tests.test_validation_rallies.RallyQueueTests.test_proxy_requires_explicit_binding) ... ok
test_queue_rejects_binding_metadata_and_shape_tampering (tests.test_validation_rallies.RallyQueueTests.test_queue_rejects_binding_metadata_and_shape_tampering) ... ok
test_queue_round_trip_and_no_overwrite (tests.test_validation_rallies.RallyQueueTests.test_queue_round_trip_and_no_overwrite) ... ok
test_require_complete_rejects_internal_gap (tests.test_validation_rallies.RallyQueueTests.test_require_complete_rejects_internal_gap) ... ok
test_side_map_rejects_missing_or_nonfinite_interval_fields (tests.test_validation_rallies.RallyQueueTests.test_side_map_rejects_missing_or_nonfinite_interval_fields) ... ok
test_side_map_rejects_noninteger_and_malformed_crops (tests.test_validation_rallies.RallyQueueTests.test_side_map_rejects_noninteger_and_malformed_crops) ... ok
test_side_switch_splits_candidate (tests.test_validation_rallies.RallyQueueTests.test_side_switch_splits_candidate) ... ok
test_atomic_publication_and_round_trip (tests.test_validation_contract.ValidationContractTests.test_atomic_publication_and_round_trip) ... ok
test_canonical_json_is_stable (tests.test_validation_contract.ValidationContractTests.test_canonical_json_is_stable) ... ok
test_hash_binding_and_metadata (tests.test_validation_contract.ValidationContractTests.test_hash_binding_and_metadata) ... ok
test_manifest_missing_optional_values_fail_closed (tests.test_validation_contract.ValidationContractTests.test_manifest_missing_optional_values_fail_closed) ... ok
test_manifest_short_row_fails_with_validation_error (tests.test_validation_contract.ValidationContractTests.test_manifest_short_row_fails_with_validation_error) ... ok
test_manifest_split_must_be_allowed (tests.test_validation_contract.ValidationContractTests.test_manifest_split_must_be_allowed) ... ok
test_overlap_rejects_copy_match_selection_sha_and_allows_unrelated (tests.test_validation_contract.ValidationContractTests.test_overlap_rejects_copy_match_selection_sha_and_allows_unrelated) ... ok
test_rejects_absolute_binding_repo_path (tests.test_validation_contract.ValidationContractTests.test_rejects_absolute_binding_repo_path) ... ok
test_selection_missing_video_path_fails_closed (tests.test_validation_contract.ValidationContractTests.test_selection_missing_video_path_fails_closed) ... ok

----------------------------------------------------------------------
Ran 27 tests in 0.079s

OK
```

Implementation: valid proxy fixtures now bind the actual source SHA-256. Proxy generation re-hashes the resolved source and rejects a digest mismatch before calling the proxy encoder. The regression replaces the source bytes at the same path, requires `ValidationError`, verifies the encoder is not called, and verifies rollback leaves no output directory.

Self-review:

- Explicit binding and video-root path identity checks remain unchanged and precede proxy publication.
- The new digest check runs before any clip writer call; existing no-overwrite and rollback behavior remains unchanged.
- Only synthetic temporary video bytes are used. No SoCal source was touched, copied, decoded, or recognized.
- The deferred crop concern was not touched.
