# Task 1 Report: Seven-Class Contract and Compatibility Metrics

## Implementation Summary

- Appended `dig` to the native `ACTION_LABELS` tuple and added `ACTION_LABEL_SCHEMA_VERSION = 2`.
- New checkpoints and `training_config.json` include `action_label_schema_version`; checkpoint loading keeps the field optional and continues to size models from each checkpoint's embedded `labels` list.
- Defined the pretrained six-class capability as `PRETRAINED_ACTION_LABELS` and maps strict `dig` truth to `receive` only for `compatibility_metrics`.
- Kept pretrained review rows strict and unchanged. The evaluation report and command result now include both strict seven-class `metrics` and six-class `compatibility_metrics`.
- Added a synthetic `dig` drawing/pattern to the smoke-data generator and a `dig` example annotation.

## Files Changed

- `examples/annotations.example.csv`
- `src/spiketrace/constants.py`
- `src/spiketrace/ml.py`
- `src/spiketrace/pretrained.py`
- `src/spiketrace/training.py`
- `tests/__init__.py`
- `tests/test_manifest.py`
- `tests/test_ml.py`
- `tests/test_pretrained.py`
- `tests/test_smoke_dataset.py`
- `tests/test_training.py`
- `tools/generate_smoke_dataset.py`

`tests/__init__.py` is the minimal package marker required for the prescribed `python -m unittest tests.test_*` focused command to import the local test modules.

## TDD Evidence

### RED

```powershell
& 'E:\Spike-Trace\.venv\Scripts\python.exe' -m unittest tests.test_manifest tests.test_pretrained tests.test_ml -v
```

Result: failed with the intended missing-contract behavior. `dig` was rejected as an unknown manifest label, the pretrained `dig` evaluation could not load its manifest, and `ACTION_LABEL_SCHEMA_VERSION` could not be imported. `Ran 18 tests`; `FAILED (errors=3)`.

```powershell
& 'E:\Spike-Trace\.venv\Scripts\python.exe' -m unittest tests.test_smoke_dataset -v
```

Result: `test_draws_distinct_dig_pattern` failed because `dig` was absent from the smoke generator's `LABELS`. `Ran 1 test`; `FAILED (failures=1)`.

```powershell
& 'E:\Spike-Trace\.venv\Scripts\python.exe' -m unittest tests.test_training -v
```

Result: `test_writes_action_label_schema_version` failed with `KeyError: 'action_label_schema_version'`. `Ran 1 test`; `FAILED (errors=1)`.

### GREEN

```powershell
& 'E:\Spike-Trace\.venv\Scripts\python.exe' -m unittest tests.test_manifest tests.test_pretrained tests.test_ml -v
```

Result: `Ran 19 tests`; `OK`.

```powershell
& 'E:\Spike-Trace\.venv\Scripts\python.exe' -m unittest tests.test_manifest tests.test_pretrained tests.test_ml tests.test_smoke_dataset -v
```

Result: `Ran 20 tests`; `OK`.

```powershell
& 'E:\Spike-Trace\.venv\Scripts\python.exe' -m unittest tests.test_training -v
```

Result: `Ran 1 test`; `OK`.

## Final Verification

```powershell
& 'E:\Spike-Trace\.venv\Scripts\python.exe' -m unittest discover -s tests -v
```

Result: `Ran 27 tests in 1.648s`; `OK`.

```powershell
& 'E:\Spike-Trace\.venv\Scripts\python.exe' -m ruff check src\spiketrace\constants.py src\spiketrace\ml.py src\spiketrace\training.py src\spiketrace\pretrained.py tests\test_manifest.py tests\test_pretrained.py tests\test_ml.py tests\test_smoke_dataset.py tests\test_training.py tools\generate_smoke_dataset.py
```

Result: `All checks passed!`

`git diff --check` also completed with exit code 0.

## Self-Review

- The original six labels retain their order; `dig` is appended.
- The schema metadata is deliberately not a required checkpoint field, so old six-label checkpoints remain loadable.
- Strict metrics retain `dig`; compatibility metrics use only the explicit six-label capability and map `dig` to `receive`.
- The review CSV continues to compare its original strict target and predicted actions.
- No generated videos, checkpoints, outputs, workbooks, or other ignored artifacts were added.

## Concerns

None.
