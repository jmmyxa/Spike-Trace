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

## Fix Round 1 RED/GREEN

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& .venv\Scripts\python.exe -m unittest tests.test_active_learning_review_contract.ReviewContractTests.test_accepts_node_supplemental_timing_and_result_wide_visibility_refs -v
```

RED observed exit `1`: a `clip_bounds` supplemental row was incorrectly sent
through the timed relative-second validator.

After accepting Node's null clip-bounds timing shape, enforcing result-wide
visibility array indexes, checking clip-bound visibility equality, and making
the result-ID formula UTF-8 compatible, the focused command and the full
contract suite exit `0` (`Ran 6 tests ... OK`). The dual-crop/selection suite
also exits `0` (`Ran 75 tests ... OK`), and Ruff exits `0`.

## Fix Round 2 RED/GREEN

The source-repair regression first accepted a valid-looking top-level repair
that was not linked from its canonical source action. It now fails unless every
repair uses the exact workbook schema, canonical clip/slot/cell identity, and
the same ordered lineage in the source/action records. The default legacy
merged-source loader reads its artifact once and delegates to the byte core;
custom verifier injection remains on the compatibility path.

Fresh contract verification exits `0` (`Ran 7 tests ... OK`) and Ruff exits
`0`.

## Fix Round 3 RED/GREEN

The legacy-loader RED regression rejected any second raw open after its
selection/merged `read_bytes` snapshots. The old path re-hashed the merged
file through a second open. The default path now parses both frozen byte
snapshots once, validates the merged bytes through the byte core, and passes
the verified object into selection validation. The custom verifier and
previous-selection compatibility route stay available without changing their
legacy behavior.

The contract matrix now checks all five live snapshot mutations and a real
dangling outcome reference. Fresh verification exits `0`: contract (`Ran 9`),
dual-crop plus selection (`Ran 76`), combined (`Ran 85`), with Ruff clean.

## Fix Round 4 RED/GREEN

Focused RED tests first exposed four producer/consumer gaps: Node-shaped
supplementals used null raw/normalized values; repair linkage depended on the
top-level repair order; normalization audit entries could be unrelated to a
repair; and a syntactically valid review-set key could name another round.

The Python validator now preserves Node supplemental timing, requires exact
source row/action identities and value payloads, binds `review_set_key` to the
selected round, and validates complete repair/inheritance audit lineage in
source-row order. Repair linkage compares exact repair records independent of
top-level order. The Node workbook canonicalizer now emits Task 4 audit
records in source-row order, retaining repair-before-inheritance ordering for
the same row; its semantic test asserts the complete A16 repair and row-5
inheritance entries.

Fresh bounded contract verification passed all `17` methods (`8` plus `9`),
the dual-crop and selection suites passed (`Ran 76 ... OK`), and Ruff passed
for the modified Python files. The Node evidence test was started with the
bundled Node runtime, artifact loader, and synthetic-pipeline Python; it
reaches fixture construction but stops because `spiketrace build-review-clips`
is not implemented in this branch (Task 6 scope), so this round leaves that
unrelated command untouched.

## Fix Round 5 RED/GREEN

### RED

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& .venv\Scripts\python.exe -m unittest tests.test_active_learning_review_contract.ReviewContractTests.test_timed_visibility_must_stay_within_selected_clip -v
```

Observed exit `1`:

```text
label='below start' ... FAIL
label='past end' ... FAIL
AssertionError: ValueError not raised
```

The validator checked finite/positive timed visibility intervals but did not
compare them against the selected clip bounds.

### GREEN

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& .venv\Scripts\python.exe -m unittest tests.test_active_learning_review_contract.ReviewContractTests.test_timed_visibility_must_stay_within_selected_clip -v
```

Observed exit `0`:

```text
Ran 1 test in 8.393s
OK
```

The regression covers an interval exactly at both clip boundaries plus lower
and upper out-of-bounds mutations. The contract now rejects timed visibility
outside its selected clip.

The producer fixture at
`tests/fixtures/node_active_review_evidence_input_v2.json` was rendered with
the bundled Node runtime by calling the real
`composeEvidenceSynthesisInput` export. The positive contract test loads that
serialized producer payload (including supplemental null values and a
side-inheritance normalization audit) and replaces only test-local artifact
bindings and selected-clip absolute times. Participant coverage now exercises
valid confirmed/candidate/unresolved records and rejects missing confirmed
identity, candidate without a handle, unresolved identity claims, and a
duplicate action track.

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& .venv\Scripts\python.exe -m unittest tests.test_active_learning_review_contract -v
```

Observed exit `0`. The runner’s 30-second stream window truncated the final
summary, so the same 19 methods were also completed in bounded invocations:

```text
Ran 10 tests in 22.614s
OK
Ran 2 tests in 23.208s
OK
Ran 4 tests in 8.576s
OK
Ran 3 tests in 8.371s
OK
```

```powershell
$env:PYTHONPATH = (Resolve-Path 'src').Path
& .venv\Scripts\python.exe -m unittest tests.test_dual_crop_review tests.test_active_learning_selection -v
```

Observed exit `0`:

```text
Ran 76 tests in 11.866s
OK
```

```powershell
.venv\Scripts\ruff.exe check src\spiketrace\_active_learning_review_contract.py tests\test_active_learning_review_contract.py
```

Observed exit `0`:

```text
All checks passed!
```

```powershell
$env:NODE_OPTIONS = '--experimental-loader=file:///E%3A/Spike-Trace/.worktrees/rangitoto-active-learning-round-01/outputs/.rangitoto-review-build/artifact-loader.mjs'
$env:NODE_PATH = 'C:\Users\Fakelove\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
$env:SPIKETRACE_PYTHON = (Resolve-Path '.venv\Scripts\python.exe').Path
$env:PYTHONPATH = (Resolve-Path 'src').Path
& 'C:\Users\Fakelove\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' tools/test_active_review_evidence.mjs
& 'C:\Users\Fakelove\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' tools/test_active_review_batch.mjs
```

Both commands were rerun through an external wait wrapper after the stream
window elapsed. Observed explicit exit code for each command: `0`. Their only
stderr line was Node’s experimental-loader warning; the evidence output ended
with an inspection record under `.active-review-evidence-root-*`, and the
batch output ended with the rollback inspection record.
