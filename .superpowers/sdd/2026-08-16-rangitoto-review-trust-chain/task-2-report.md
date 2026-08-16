# Task 2 Report: Event Member Provenance and Inference JSON v2

## Scope

Implemented the Task 2 provenance contract on `codex/rangitoto-review`.
The compatibility API remains `merge_action_windows(...) -> list[ActionEvent]`.
The new `merge_action_windows_with_provenance(...)` returns events and the exact
input-window positions captured by the existing event state machine.

## Implementation

- `src/spiketrace/events.py`
  - Materializes and enumerates input windows before sorting.
  - Stores each original `window_index` on `_EventCandidate` and appends it only
    when that candidate is extended.
  - Returns sorted, unique mappings only for retained events.
  - Keeps `merge_action_windows` as a wrapper that discards provenance.
- `src/spiketrace/outputs.py`
  - Requires `event_window_indices` and validates every event mapping.
  - Rejects missing/extra mappings, duplicate event IDs, empty or non-list
    mappings, non-integer, non-increasing, duplicate, and out-of-range indices,
    duplicate assignment across events, action mismatch, and confidence below the
    configured threshold.
  - Writes inference JSON `format_version: 2`, per-event
    `source_window_indices`, and per-window `window_index`.
  - Preserves the event-only CSV schema.
- `src/spiketrace/inference.py`
  - Uses the provenance-returning merger and passes its mapping to the writer.
  - Records stable checkpoint/video SHA-256 values, OpenCV/PyTorch/torchvision
    versions, video metadata, crop, model window configuration, stride,
    confidence/merge/minimum duration settings, batch size, device, and the
    existing `center-nearest-frame-v1` sampling contract.
- Tests add state-machine interruption/exclusion coverage, output validation
  coverage, and end-to-end inference v2/settings coverage.

## TDD Evidence

### RED: event provenance API

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_events -v
```

Output:

```text
ImportError: cannot import name 'merge_action_windows_with_provenance' from 'spiketrace.events'
Ran 1 test in 0.000s
FAILED (errors=1)
```

### GREEN: event provenance

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_events -v
```

Output:

```text
Ran 4 tests in 0.000s
OK
```

### RED: output JSON v2 and validation

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_outputs -v
```

Output:

```text
TypeError: write_inference_outputs() got an unexpected keyword argument 'event_window_indices'
Ran 7 tests in 0.004s
FAILED (errors=7)
```

### GREEN: output JSON v2 and validation

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_outputs -v
```

Output:

```text
Ran 7 tests in 0.006s
OK
```

### RED: inference handoff

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_inference -v
```

Output:

```text
Processed 5/5 windows
TypeError: write_inference_outputs() missing 1 required keyword-only argument: 'event_window_indices'
Ran 1 test in 0.018s
FAILED (errors=1)
```

### GREEN: inference handoff and settings

Command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_inference -v
```

Output:

```text
Processed 5/5 windows
Ran 1 test in 0.017s
OK
```

## Final Verification

Focused regression command:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_events tests.test_outputs tests.test_inference -v
```

Output:

```text
Ran 12 tests in 0.030s
OK
```

Full regression command:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Output:

```text
Ran 77 tests in 0.842s
OK
```

Style and diff check:

```powershell
.venv\Scripts\ruff.exe check src\spiketrace\events.py src\spiketrace\inference.py src\spiketrace\outputs.py tests\test_events.py tests\test_inference.py tests\test_outputs.py
git diff --check
```

Output: `All checks passed!`; `git diff --check` reported no whitespace errors.

## Self-Review

- The interrupted `attack -> set -> attack` case proves membership is retained
  from candidate construction, not reconstructed by later action/time overlap.
- Background, low-confidence, invalid-duration, and minimum-duration-filtered
  windows are excluded before retained-event mappings are emitted.
- Serialized indices are validated as increasing, unique, in-range, tied to the
  matching action, above the configured threshold, and globally non-overlapping.
- `window_index` is derived from the serialized input-list position and CSV does
  not expose provenance.
- Checkpoint format was not changed. `pyproject.toml` was not modified, so the
  author remains `jmmyxa`.
- No output directories, videos, checkpoints, raw inference artifacts, README,
  or unrelated files were staged.

## Concerns

No known implementation concerns. The untracked
`outputs/rangitoto-r3d18-bootstrap-review/` directory predates this task and is
intentionally excluded from the commit.
