# Task 3 Report: Locked Validation Truth Bundle

## RED

Command:

```powershell
$env:PYTHONPATH='src'; .venv\Scripts\python.exe -m unittest tests.test_validation_truth -v
```

Result: failed during import with `ModuleNotFoundError: No module named 'spiketrace.validation_truth'`.

## GREEN

Focused command:

```powershell
$env:PYTHONPATH='src'; .venv\Scripts\python.exe -m unittest tests.test_validation_truth -v
```

Result: `Ran 5 tests ... OK`.

Adjacent validation command:

```powershell
$env:PYTHONPATH='src'; .venv\Scripts\python.exe -m unittest tests.test_validation_contract tests.test_validation_rallies tests.test_validation_truth -v
```

Result: `Ran 32 tests ... OK`.

## Scope

- Added strict prediction-blind draft validation, raw `free_ball` preservation, and `background` CSV projection.
- Added visibility interval retention, coverage/no-action enforcement, whole-second bounds, empty player numbers, duplicate/unknown field rejection, and fail-closed parsing.
- Added immutable UTF-8 BOM CSV and canonical locked JSON publication with source/hash revalidation and verification counts.
- Updated `README.md` and `docs/PROJECT_PLAN.md`.

## Self-review / concerns

- The implementation intentionally does not inspect, decode, or recognize source video; it only re-hashes the frozen binding at lock/verify.
- CSV notes/evidence remain authority-only as required by the fixed compatibility header.
- Full repository discovery was not used as a release gate because several unrelated video/model tests are significantly longer; focused and adjacent validation suites are green.

## Commit

`0125471efe1c44a8cf4913db6e253772e7a9b1d4` (`feat: add locked validation truth bundle`)
