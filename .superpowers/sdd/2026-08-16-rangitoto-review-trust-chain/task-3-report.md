# Task 3 Report: Deterministic Dual-Crop Merge and Verifier

## Scope

Implemented the Task 3 deterministic far/near inference JSON v2 merge,
self-contained verifier, JSON/CSV renderers, CLI commands, fixtures, tests, and
the required README synchronization on `codex/rangitoto-review` from base
`b1dbff6`.

## Implementation

- `src/spiketrace/dual_crop_review.py`
  - Exposes `build_dual_crop_review(...)` and `verify_dual_crop_review(...)`.
  - Strictly validates inference JSON v2, duplicate JSON keys, finite numeric
    values, exact crops and sampling contract, dense window indices, event member
    ownership/action/threshold/bounds, model/video/checkpoint identity, and
    cross-run settings.
  - Normalizes repository paths to POSIX repository-relative strings and external
    paths to basenames, then records source-byte and canonical normalized-payload
    SHA-256 values.
  - Converts seconds to integer milliseconds with Decimal half-up behavior and
    applies the exact duplicate/conflict predicates to half-open intervals.
  - Uses side-specific active sweep indexes, duplicate-only connected components,
    deterministic primary tie-breakers, stable IDs, canonical ordering, and exact
    derived review fields.
  - Writes deterministic UTF-8 JSON and UTF-8 BOM CSV, then verifies the emitted
    artifact before returning.
  - Rebuilds source candidates, links, components, primary selections, groups,
    events, hashes, and optional CSV bytes exclusively from embedded normalized
    inputs during verification.
- `src/spiketrace/cli.py`
  - Adds real dispatch for `build-dual-crop-review` and
    `verify-dual-crop-review`.
- `tests/fixtures/dual_crop_review/far.json` and `near.json`
  - Add the exact four-window v2 fixture with one far-only event, one duplicate,
    one retained conflict, and Windows-style paths that must be normalized.
- `tests/test_dual_crop_review.py`
  - Covers exact artifact structure and bytes, every primary tie-breaker,
    half-up milliseconds, code-point sorting, strict input rejection, independent
    tampering, self-contained verification, real CLI dispatch, Rangitoto-scale
    shape, and same-side sweep behavior.
- `README.md`
  - Documents the new module, tests, inference v2 member indexes, CLI commands,
    provenance limits, and invalidates the old floor-sampled Rangitoto review
    counts/artifacts until Task 5 rebuilds them.

## Files Changed

- `.superpowers/sdd/2026-08-16-rangitoto-review-trust-chain/task-3-report.md`
- `README.md`
- `src/spiketrace/dual_crop_review.py`
- `src/spiketrace/cli.py`
- `tests/fixtures/dual_crop_review/far.json`
- `tests/fixtures/dual_crop_review/near.json`
- `tests/test_dual_crop_review.py`

## TDD Evidence

### RED: module and public interfaces absent

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_dual_crop_review -v
```

Initial output included the expected import failure:

```text
ModuleNotFoundError: No module named 'spiketrace.dual_crop_review'
FAILED (errors=1)
```

After adding only public stubs, the same command reached the tests and failed on
the expected missing behavior:

```text
NotImplementedError
spiketrace: error: argument command: invalid choice: 'build-dual-crop-review'
```

### GREEN: core fixture and initial Task 3 suite

The exact four-window fixture passed after the minimal builder, normalization,
grouping, deterministic rendering, verifier, and CLI implementation. The first
complete Task 3 run then reported:

```text
Ran 10 tests in 1.4s
OK
```

### RED/GREEN: source event video identity

Self-review added a mutation whose source event `video_id` did not match the
normalized video path stem. Before validation it failed with:

```text
AssertionError: ValueError not raised
```

After adding the identity invariant, the invalid-input test passed.

### RED/GREEN: exact integer types

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_dual_crop_review.DualCropReviewBuildTests.test_rejects_invalid_or_mismatched_inference_inputs -v
```

Before the production fix, the `non-integer format` and `boolean crop coordinate`
subtests both failed with:

```text
AssertionError: ValueError not raised
```

Python equality had accepted `2.0 == 2` and `False == 0`. Exact `int` checks were
added at both schema boundaries. GREEN output:

```text
Ran 1 test in 0.028s
OK
```

### RED/GREEN: same-side sweep complexity

Independent review found that the first sweep retained both sides in one active
list and rescanned same-side candidates before skipping them. The focused
regression command was:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_dual_crop_review.DualCropReviewBuildTests.test_cross_side_sweep_does_not_rescan_same_side_candidates -v
```

RED output:

```text
AssertionError: same-side candidate was rescanned
Ran 1 test in 0.000s
FAILED (failures=1)
```

The sweep now stores far/near active candidates separately and cleans and
compares only the opposite side. GREEN output:

```text
Ran 1 test in 0.000s
OK
```

The exact fixture regression also remained green:

```text
Ran 1 test in 0.013s
OK
```

## Final Verification

Task 3 command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_dual_crop_review -v
```

Output:

```text
Ran 11 tests in 1.460s
OK
```

Full regression command:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Output:

```text
Ran 88 tests in 2.269s
OK
```

Style command:

```powershell
.venv\Scripts\ruff.exe check .
```

Output:

```text
All checks passed!
```

CLI command:

```powershell
.venv\Scripts\python.exe -m spiketrace --help
```

Output includes both commands:

```text
build-dual-crop-review
verify-dual-crop-review
```

Whitespace command:

```powershell
git diff --check
```

Output: exit code 0 with no whitespace errors. Git emitted only Windows
LF-to-CRLF working-copy warnings for `README.md` and `src/spiketrace/cli.py`.

## Self-Review

- Confirmed merged root, settings, source audit, event, group, link, CSV, return
  value, and field ordering against the clarification contract.
- Confirmed rule decisions use exact integer/rational quantities; rounded metrics
  are presentation-only.
- Confirmed normalized payload hashes use recursive key sorting, compact UTF-8
  JSON, no BOM, and no trailing newline.
- Confirmed the verifier rejects normalized-input, derived-event, grouping,
  metric, path, source-hash-format, deterministic-JSON, and CSV drift without
  reading original far/near files.
- Confirmed candidate/window lookup is direct and the cross-side sweep does not
  scan active same-side candidates. The generated Rangitoto-scale test completes
  below its 12-second ceiling.
- Confirmed `pyproject.toml` was not changed and no generated Rangitoto output is
  included in Task 3.

## Independent Review

The first review reported no Critical functional issue and one Important
complexity issue in the mixed-side active list. That issue was reproduced with a
failing regression and fixed with side-specific active indexes. Focused rereview
reported no remaining Critical or Important findings and measured linear
same-side scaling through 16,000 candidates. All 11 Task 3 tests passed during
the rereview.

## Concerns

- `source_file_sha256` remains provenance-only by contract because the
  self-contained verifier has no original source-file arguments.
- The existing untracked `outputs/rangitoto-r3d18-bootstrap-review/` directory
  predates Task 3 and remains untouched and unstaged. Its old floor-sampled
  artifacts are invalid for review until Task 5 rebuilds them from inference v2.
