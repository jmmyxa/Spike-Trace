# Task 4 — Locked segmented validation inference

## RED

The Task 4 baseline (`ae1a18c129c91b86168bd897fac4b6ce78863358`) has no
`iter_window_times_range` helper or `validation_inference` module. The new
validation test module therefore exercises missing interfaces against that
baseline (expected import/helper failure).

## GREEN

Implemented `iter_window_times_range` with finite, positive, ordered argument
validation, half-open range containment, stride advancement, and a clamped
endpoint window. Added locked segmented inference with one independently
cropped sequential decode per segment, absolute source times, per-segment event
merging, stable prediction IDs, and remapped global source-window provenance.
The lock gate runs before torch/model loading. Source video/checkpoint hashes
are captured before decoding and checked after the final batch; metadata, crop
geometry, segment ordering, and overlap are fail-closed validated. `non_rally`
coverage is omitted from inference because it has no side/crop contract and is
recorded as such in settings. README and project plan document the new module
and data contract.

## Coverage

- Range containment, endpoint clamping, invalid finite/ordering arguments.
- Near/far segment crop isolation and absolute window times.
- Stable prediction ID prefix, side, and source-window provenance remapping.
- Lock gate before model loader invocation.
- Out-of-order and overlapping segments, adjacent interval support, invalid crop geometry.
- Video and checkpoint mutation detection after decoding begins.
- Existing full-video inference and video decoder tests remain unchanged.

## Commands and output

```powershell
$env:PYTHONPATH='E:\Spike-Trace\.worktrees\socal-independent-validation\src'
.venv\Scripts\python.exe -m unittest tests.test_validation_inference tests.test_inference tests.test_video -v
```

Output: `Ran 25 tests ... OK`.

## Commits

- `7a696a4 feat: add locked segmented validation inference` (implementation, tests, docs)
- `5f517cb docs: record Task 4 validation inference report` (this report)
- `19749bb fix: wrap validation metadata read failures` (validation error boundary)
- `0f8ecb3 feat: record excluded validation coverage` (explicit non-rally/unusable settings map)

## Concerns

- The public function intentionally omits `non_rally` intervals until a future
  evaluation contract supplies a side/crop mapping for them.
- Real SoCal video/checkpoint inference was not run; fixtures only use tiny
  synthetic video and a deterministic fake model as required.

## Fix round 1

### RED

Added regressions for missing/malformed side mappings, adjacent near/far
mapping, invalid public parameters, stride gaps, and decoder failures. These
cases exposed the prior silent side-record skip and leaked `ValueError`/video
pipeline exceptions.

### GREEN

Side intervals are now validated strictly, sorted per set, rejected when
overlapping, and required to fully cover every rally lacking its own valid
side/crop. Public inference parameters and checkpoint window compatibility are
validated before decoding; checkpoint/device, range, merge, and sequential
decoder failures are converted to `ValidationError` while preserving the early
locked-truth gate. The existing `stride_seconds > window_seconds` rejection is
retained to preserve the established sliding-window coverage contract and is
covered by regression tests. README and project plan document these fail-closed
boundaries.

### Fix round command/output

```powershell
$env:PYTHONPATH='src'
.venv\Scripts\python.exe -m unittest tests.test_validation_inference tests.test_inference tests.test_video -v
```

Output: `Ran 27 tests ... OK`.

Fix-round commits: `ec654b1 fix: harden locked segmented validation boundaries` (implementation, tests, docs), `820eab8 fix: accept mapping side interval records` (Mapping-compatible side records); report update follows.
