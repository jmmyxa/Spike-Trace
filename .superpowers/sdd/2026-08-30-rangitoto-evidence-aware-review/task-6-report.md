# Task 6 Report: Compose Evidence-Aware Observations

## Implementation Summary

- Added immutable, slotted observation dataclasses for actions, outcomes, raw visibility sources, merged visibility events, and action-participant relations.
- Added `compose_observation_set(review, selection)` to preserve validated source order while representing participants only as relations; no action window is copied for multiple participants.
- Added `merge_visibility_events(observations, result_set_id, event_kind)` with the format-v2 fixed 1.0-second merge gap, independent event-kind and team-side groups, atomic source intervals, sorted reference unions, deduplicated source-ref-ordered notes, clip-bounds precedence, and stable refs.
- Updated the README program tree for the new module and test without claiming later projection or output work.

## TDD Evidence

### RED

Command (the requested command first exposed that `SPIKETRACE_PYTHON` was unset in this shell; the equivalent virtual-environment interpreter was then used):

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& .venv\Scripts\python.exe -m unittest tests.test_active_learning_review_observations -v
```

Actual failing output:

```text
ModuleNotFoundError: No module named 'spiketrace._active_learning_review_observations'
Ran 1 test in 0.000s
FAILED (errors=1)
```

### GREEN

Command:

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& .venv\Scripts\python.exe -m unittest tests.test_active_learning_review_observations -v
```

Actual output:

```text
Ran 4 tests in 0.000s
OK
```

## Regression and Lint

Commands:

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
$env:SPIKETRACE_PYTHON = (Resolve-Path '.venv\Scripts\python.exe').Path
& $env:SPIKETRACE_PYTHON -m unittest tests.test_active_learning_review_contract -v
.venv\Scripts\ruff.exe check src\spiketrace\_active_learning_review_observations.py tests\test_active_learning_review_observations.py
git diff --check
```

Actual output:

```text
Task 5 contract: Ran 19 tests in 61.869s
Task 5 contract: OK
Ruff: All checks passed!
git diff --check: exit 0
```

## Files

- `src/spiketrace/_active_learning_review_observations.py`
- `tests/test_active_learning_review_observations.py`
- `README.md`
- `.superpowers/sdd/2026-08-30-rangitoto-evidence-aware-review/task-6-report.md`

## Self-Review

- Action, outcome, and participant tuple order remains the validated source order.
- Multiple block participants remain relations and do not create duplicate actions or player-number broadcasts.
- Exact 1.0-second gaps merge transitively; a 1.01-second gap remains separate.
- Occlusion and off-camera sources cannot merge with each other, and far/near intervals cannot merge with each other.
- Merged event authority preserves every atomic interval and source reference.

## Concerns

- `ActionObservation` retains the brief's exact `dict[str, object]` annotations for raw and normalized values, while the validated v2 supplemental-action contract can supply `None` at runtime. This preserves the v2 source value unchanged; no coercion or contract change was made.
- The provided `SPIKETRACE_PYTHON` environment variable was unset locally, so tests use the repository's `.venv\\Scripts\\python.exe` explicitly.

## Review Fix Round 1

### Scope

- Deeply freeze authority mappings at the composition boundary so source mutations after composition and direct observation mutations cannot alter composed data.
- Add the derived `ObservationSet.affected_action_count` property, counting the distinct union of action references across both merged visibility event kinds without storing a second summary value.

### TDD RED

Command:

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& .venv\Scripts\python.exe -m unittest tests.test_active_learning_review_observations -v
```

Actual failing output:

```text
FAIL: test_composition_deeply_freezes_nested_authority_data
AssertionError: ['raw', 'changed'] != ('raw',)

ERROR: test_counts_distinct_affected_actions_across_visibility_kinds
AttributeError: 'ObservationSet' object has no attribute 'affected_action_count'

Ran 6 tests in 0.001s
FAILED (failures=1, errors=1)
```

### TDD GREEN and Verification

Commands:

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& .venv\Scripts\python.exe -m unittest tests.test_active_learning_review_observations -v
.venv\Scripts\ruff.exe check src\spiketrace\_active_learning_review_observations.py tests\test_active_learning_review_observations.py
git diff --check
```

Actual output:

```text
Ran 6 tests in 0.000s
OK
Ruff: All checks passed!
git diff --check: exit 0
```

### Regression

Command:

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& .venv\Scripts\python.exe -m unittest tests.test_active_learning_review_contract -v
```

Actual output:

```text
Ran 19 tests in 63.092s
OK
```

### Self-Review

- `_FrozenDict` remains a `dict` subclass, so standard JSON encoding retains normal object representation; nested arrays become tuples, which the standard JSON encoder renders as arrays.
- `json.dumps(_freeze({'nested': {'values': ['value']}}))` produced `{"nested": {"values": ["value"]}}`.
- The freeze recursively copies mappings and sequence values before storing them, so neither source aliases nor direct nested mutation survive the composition boundary.
- `affected_action_count` is a read-only property calculated from event tuples, preventing a duplicated count from drifting from authority events.

### Concerns

- Only JSON-shaped mapping and sequence inputs are recursively frozen, matching the strict v2 contract. No new serializer or output module was introduced.
