# Task 5 Report — Validate the V2 Python Contract

## RED

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& .venv\Scripts\python.exe -m unittest tests.test_active_learning_review_contract -v
```

Observed exit `1`: `ModuleNotFoundError: No module named
'spiketrace._active_learning_review_contract'`. This was the expected missing
contract-module failure before production implementation.

## GREEN

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& .venv\Scripts\python.exe -m unittest tests.test_active_learning_review_contract -v
```

Observed exit `0`: `Ran 4 tests ... OK`.

```powershell
& .venv\Scripts\python.exe -m unittest tests.test_dual_crop_review tests.test_active_learning_selection -v
```

Observed exit `0`: `Ran 75 tests ... OK`.

```powershell
.venv\Scripts\ruff.exe check src\spiketrace\_active_learning_review_contract.py src\spiketrace\dual_crop_review.py src\spiketrace\_active_learning_selection_artifact.py tests\test_active_learning_review_contract.py tests\test_dual_crop_review.py tests\test_active_learning_selection.py
```

Observed exit `0`: `All checks passed!`.

## Self-review

- `derive_result_set_id` uses the Node v2 NUL-delimited canonical formula.
- Frozen snapshots bind and parse selection, review input, workbook, evidence overrides, and merged candidates once; a later live-byte change is rejected before publication.
- V2 validation rejects duplicate JSON keys, non-finite values, invalid paths/hashes/enums/review-set keys, broken source/action/observation references, non-contiguous supplemental refs, invalid whole-second timing, and invalid participant assignments.
- The legacy path-based selection verifier retains its injected-verifier seam and existing error behavior; byte/path equivalence is covered separately with the real Rangitoto artifact.
