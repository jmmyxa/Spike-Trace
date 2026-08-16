# Task 2 Report: Deterministic five-bucket selection

## Status

DONE_WITH_CONCERNS

## Implementation

- Added `select_review_batch()` with deterministic SHA-256 ranking, integer
  millisecond fitting, ten-or-more time strata, prior-round time exclusion,
  and chronological round-specific clip IDs.
- Implemented the five ordered buckets and 20/8/4/4/4 quotas, including
  minority-first clustering, full candidate-hint/resource preservation,
  confidence ordering, dual-view eligibility, stratified controls, paired
  background intersections, and forward-only quota transfer.
- Enforced exactly 40 non-overlapping clips, a minimum anchor gap, at least ten
  represented strata, all available minority IDs, and available far/near
  candidate evidence.
- Added exact Task 2 artifact validation for settings, prior artifact pins,
  quota summaries, coverage, clip/anchor/hint surfaces, derived times and
  strata, and globally unique reserved resources. Task 1's future nested
  containers remain extensible unless a Task 2 settings or clip field is
  present.
- Added `spiketrace select-review-batch` with the complete requested argument
  surface, repeatable previous selections, and parser-level range checks.
- Recomputed a merged non-required clip's stratum from its union anchor. This
  keeps serialized artifacts valid when an overlap merge crosses a stratum
  boundary.
- Corrected the short-pool test fixture so its eligible maximum is a
  hand-counted 39 clips; its previous mutation still left enough reserve
  candidates to produce 40.

## Files

- `src/spiketrace/active_learning_selection.py`
- `src/spiketrace/cli.py`
- `tests/test_active_learning_selection.py`
- `.superpowers/sdd/2026-08-16-rangitoto-active-learning-round-01/task-2-report.md`

## RED / GREEN Evidence

### Core selector cycles

The first exact-quota and deterministic-rerun tests failed before
`select_review_batch()` populated clips and quota data. Subsequent focused RED
/ GREEN cycles covered minority coalescing, prior-time exclusion, quota
transfer, coverage failures, strict serialization, CLI forwarding, and invalid
argument ranges.

Before the final regression fix, the focused cross-stratum merge test failed
at the intended validator boundary:

```powershell
$env:PYTHONPATH='src'
E:/Spike-Trace/.venv/Scripts/python.exe -m unittest tests.test_active_learning_selection.FiveBucketSelectionTests.test_merges_overlapping_nonrequired_clips_across_a_stratum_boundary -v
```

```text
spiketrace.errors.ActiveLearningError: clip time_stratum does not match its anchor.
Ran 1 test in 0.022s
FAILED (errors=1)
```

After recalculating the stratum from the merged union anchor, the same command
produced:

```text
Ran 1 test in 0.018s
OK
```

The first covering-suite run then exposed an invalid shortage fixture:

```text
test_fails_instead_of_returning_fewer_than_forty_clips ... FAIL
AssertionError: ActiveLearningError not raised
Ran 41 tests in 0.661s
FAILED (failures=1)
```

The fixture had retained tail and dual reserve events. Removing every reserve
event while keeping 20 first-bucket clips, 8 tail clips, 4 dual clips, 3 random
clips, and 4 background clips reproduced the required production error:

```text
ActiveLearningError: Could not select exactly 40 legal clips; selected 39.
```

With that test setup corrected, the selector module passed all 41 tests.

## Complete Verification

Run after implementation, regression repair, and manual self-review:

```powershell
$env:PYTHONPATH='src'
E:/Spike-Trace/.venv/Scripts/python.exe -m unittest tests.test_active_learning_selection -v
E:/Spike-Trace/.venv/Scripts/python.exe -m unittest discover -s tests -v
E:/Spike-Trace/.venv/Scripts/python.exe -m ruff check .
E:/Spike-Trace/.venv/Scripts/python.exe -m compileall -q src tests
git diff --check
```

Results:

```text
Selection module: Ran 41 tests in 0.696s, OK
Full suite: Ran 135 tests in 3.234s, OK
Ruff: All checks passed!
compileall: exit code 0 (no output)
git diff --check: exit code 0
```

## Self-review

- Confirmed stable ranking uses SHA-256 over seed, namespace, and stable ID;
  there is no `random.Random` or process-dependent hash ordering.
- Confirmed required minority clusters reserve resources only after the full
  cluster fits and retain every canonical ID as a separate candidate hint.
- Confirmed later buckets cannot reuse source, duplicate-group, or
  conflict-group reservations and that previous clip time excludes rewritten
  event IDs.
- Confirmed quota deficits transfer only to the next bucket and the final
  bucket cannot leave a deficit.
- Confirmed final clip IDs are assigned only after chronological sorting and
  the root and clip field orders match the selection-v1 / Task 2 contracts.
- Confirmed prior selection paths preserve caller order and store repository-
  relative paths, SHA-256 pins, batch IDs, and round IDs.
- Confirmed deterministic output contains no `generated_at` field.
- Confirmed only the three Task 2 source/test files and this required report
  are included in the intended commit.

## Concerns

The requested extension adds roughly 1,380 production lines to
`active_learning_selection.py` and about 820 test lines. The brief explicitly
required extending that existing module and restricted the implementation
files, so this task does not split selection generation from artifact
validation. A later maintenance task should consider separating those
responsibilities without changing the persisted contract.

## Fix Round 1 (2026-08-17)

### Status

DONE

### Findings and RED / GREEN evidence

1. Long paired-background runs were passed whole to clip fitting. The RED test
   failed on a 1,200-second continuous run with `The anchor cannot fit in a
   legal review clip.` The selector now divides long intersections into
   deterministic legal children; the new long-run test and existing short-run
   test pass.
2. Three overlapping non-required candidates appended the same merge reason
   twice. The RED artifact failed loader validation because
   `selection_reasons must be unique.` Merge reasons are now appended once;
   the three-way and cross-stratum merge tests pass.
3. The agreement pool incorrectly excluded high-confidence duplicates that
   remained after the tail bucket. The RED test selected zero agreement clips
   instead of four. Agreement eligibility now follows the duplicate/no-conflict
   rule while reservation sets preserve exclusivity; priority and transfer
   regressions pass.
4. Fit failure stopped at the first required candidate and also aborted on an
   optional candidate. The RED cases omitted `minority-block-05` from the
   aggregate error and raised for `tail-reserve-20`. Unfittable optional events
   are skipped, while every unfittable minority ID is collected into one
   deterministic error. Four focused fit/error tests pass.
5. Prior artifacts were trusted without a video-identity or loader-time interval
   check. Both RED tests loaded without error. Generation and loading now compare
   SHA-256-backed video identity and reject current/prior half-open interval
   overlap. The two new trust tests and the existing prior-exclusion test pass.
6. Task 2 loading trusted persisted semantic claims. Seven RED assertions loaded
   successfully after mutations to custom duration bounds, anchor gap, global
   hint identity, event semantics, minority coverage, or scene coverage. The
   loader now reconstructs eligible canonical events from the pinned merged
   payload and prior intervals, then validates every claim against that source.
   All seven focused semantic checks pass.
7. A generic Task 1 `settings.seed` or one clip `selection_bucket` activated
   Task 2 validation in RED. Task 2 detection now uses complete settings,
   quota, coverage, and clip-surface markers while still recognizing an artifact
   with one damaged surface. Task 1 generic, partial future, and lone clip
   extensions pass; malformed Task 2 surfaces remain rejected.

### Module boundaries

- `active_learning_selection.py` is the stable facade and retains the public
  imports plus the CLI patch surface
  `spiketrace.active_learning_selection.select_review_batch`.
- `_active_learning_selection_contract.py` owns constants and pure schema,
  settings, numeric, time, and interval helpers.
- `_active_learning_selection_artifact.py` owns merged/prior reconstruction,
  trust checks, paths, hashes, atomic JSON I/O, and loader validation.
- `_active_learning_selector.py` owns candidate construction, ranking, merging,
  five-bucket orchestration, and clip serialization.
- Dependencies are one-way: artifact depends on contract; selector depends on
  artifact and contract; the facade delegates to all three. The README program
  tree documents the internal modules.

### Complete verification

Fresh commands after the split and README update:

```text
Selection module: Ran 55 tests in 1.001s, OK
Full suite: Ran 149 tests in 3.454s, OK
Ruff: All checks passed!
compileall: exit code 0 (no output)
git diff --check: exit code 0 (line-ending warnings only)
```

### Self-review

- Confirmed deterministic JSON field order, SHA-256 ranking, integer-millisecond
  decisions, atomic no-overwrite writes, and the Task 1 trust chain are retained.
- Confirmed canonical hint IDs are unique across the batch and every hint matches
  the verified merged event's timing, action, confidence, scenes, groups, and
  source candidate IDs.
- Confirmed persisted minimum/maximum durations and anchor gap are authoritative,
  and prior intervals use half-open overlap semantics.
- Confirmed the split preserves public imports, verifier mocking, CLI selector
  mocking, and fault-injection coverage for atomic writes.

### Concerns

None remaining for Fix Round 1.
