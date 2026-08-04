# Receive/Dig Second Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the internal action contract to seven classes without breaking six-class checkpoints or YOLO weights, then generate a validated 17-row second-review queue and workbook.

**Architecture:** Keep the native seven-class contract separate from the pretrained six-class capability contract. Build the second-review queue from a committed JSON request spec plus the reviewed annotation manifest, then render an ignored Excel view with `@oai/artifact-tool`.

**Tech Stack:** Python 3.10+, standard-library `csv`/`json`/`unittest`, PyTorch checkpoint loader, `@oai/artifact-tool` for the local workbook.

## Global Constraints

- USA is the only target team in the current match.
- `receive` means serve reception only; `dig` means defense against an opponent attack.
- Old six-class checkpoints and YOLO weights must keep loading without a `dig` class.
- Raw video, checkpoints, outputs, workbooks, previews, and generated clips stay Git-ignored.
- README must be synchronized whenever the module structure or workflow changes.
- Train, validation, and test data remain isolated by complete match.

---

### Task 1: Seven-Class Contract and Compatibility Metrics

**Files:**
- Modify: `src/spiketrace/constants.py`
- Modify: `src/spiketrace/ml.py`
- Modify: `src/spiketrace/training.py`
- Modify: `src/spiketrace/pretrained.py`
- Modify: `src/spiketrace/cli.py`
- Modify: `tests/test_manifest.py`
- Modify: `tests/test_pretrained.py`
- Create: `tests/test_ml.py`

**Interfaces:**
- Consumes: old checkpoints whose `labels` field contains six labels.
- Produces: `ACTION_LABELS` with `dig` appended, `ACTION_LABEL_SCHEMA_VERSION = 2`, strict `metrics`, and six-class `compatibility_metrics`.

- [ ] **Step 1: Write failing tests**

Add tests that load a manifest containing `dig`, verify a six-label checkpoint without a schema field still loads, and evaluate a `dig` target predicted as `receive`:

```python
self.assertEqual(records[0].label, "dig")
self.assertEqual(checkpoint["labels"], list(legacy_labels))
self.assertEqual(result["metrics"]["accuracy"], 0.0)
self.assertEqual(result["compatibility_metrics"]["accuracy"], 1.0)
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -m unittest tests.test_manifest tests.test_pretrained tests.test_ml -v
```

Expected: failures because `dig`, compatibility metrics, and the schema metadata do not exist yet.

- [ ] **Step 3: Implement the minimal contract change**

Append `dig` to `ACTION_LABELS`, add `ACTION_LABEL_SCHEMA_VERSION = 2`, and include the optional schema field in newly created checkpoints/training configs. Keep `load_checkpoint()` driven by each checkpoint's embedded labels.

Define the pretrained capability explicitly:

```python
PRETRAINED_ACTION_LABELS = ("background", "serve", "receive", "set", "attack", "block")
PRETRAINED_COMPATIBILITY_MAP = {"dig": "receive"}
```

Use seven labels for strict metrics and mapped six labels for compatibility metrics. Keep review rows unchanged.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the same focused test command and expect all tests to pass.

- [ ] **Step 5: Update smoke/example coverage**

Add a synthetic `dig` pattern to `tools/generate_smoke_dataset.py` and a `dig` row to `examples/annotations.example.csv`.

### Task 2: Structured Second-Review Queue

**Files:**
- Create: `src/spiketrace/timecode.py`
- Create: `src/spiketrace/review.py`
- Modify: `src/spiketrace/pretrained.py`
- Modify: `src/spiketrace/cli.py`
- Create: `tests/test_review.py`
- Create: `data/annotations/usa_germany_2024_second_review.json`
- Modify: `data/annotations/usa_germany_2024_match.json`

**Interfaces:**
- Consumes: `prepare_review_queue(manifest_path, spec_path, output_path, video_root=None, require_files=True)`.
- Produces: a UTF-8 BOM CSV containing source values, suggestions, and blank human-confirmation fields.

- [ ] **Step 1: Write failing queue tests**

Create a temporary three-row manifest and a two-request JSON spec. Assert source order, union-free unique records, note extraction, blank confirmation fields, and rejection of duplicate/out-of-range requests.

```python
self.assertEqual([row["record_index"] for row in rows], [1, 2])
self.assertEqual(rows[1]["reviewer_note"], "动作稍晚")
self.assertEqual(rows[0]["confirmed_action"], "")
```

- [ ] **Step 2: Run queue tests and verify RED**

Run:

```powershell
python -m unittest tests.test_review -v
```

Expected: import failure because `spiketrace.review` does not exist.

- [ ] **Step 3: Implement timecode and queue generation**

Move the generic time formatter to `timecode.py`, retain the import surface used by existing pretrained tests, validate the spec, and write queue rows in manifest order. Add the `prepare-review` CLI command with repeatable deterministic JSON output.

- [ ] **Step 4: Run queue tests and verify GREEN**

Run the focused queue and pretrained tests.

- [ ] **Step 5: Add the real second-review spec**

Encode the 17 reviewed record indices and only evidence-backed suggestions. Update match metadata from provisional frame review to first-pass reviewed with a pending 17-row second pass.

- [ ] **Step 6: Generate the real queue**

Run:

```powershell
spiketrace prepare-review `
  data\annotations\usa_germany_2024_annotations.csv `
  data\annotations\usa_germany_2024_second_review.json `
  outputs\second-review\usa_germany_2024_second_review.csv
```

Expected: 17 rows in original manifest order.

### Task 3: Review Workbook and Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/PROJECT_PLAN.md`
- Create locally: `outputs/second-review/usa_germany_2024_second_review.xlsx`

**Interfaces:**
- Consumes: the generated 17-row queue CSV.
- Produces: a two-sheet workbook with a review table and concise label guide.

- [ ] **Step 1: Update documentation**

Document the seven-class internal contract, six-class YOLO compatibility, `prepare-review` command, module structure, 17-row queue, and pending human action. Update project-plan label definitions and current state.

- [ ] **Step 2: Build the workbook with artifact-tool**

Create `动作二次复核` and `标签说明` sheets. Do not include model result, confidence, True/False, or a separate review-status column. Put human action/time/note columns in yellow and add seven-class validation to the action column.

- [ ] **Step 3: Inspect and render every sheet**

Inspect key table ranges and scan for formula errors. Render both sheets to PNG and visually check clipping, wrapping, alignment, and editable fields.

- [ ] **Step 4: Run full verification**

Run:

```powershell
python -m unittest discover -s tests -v
python -m ruff check .
python -m ruff format --check .
```

Also verify the generated CSV/workbook contains exactly 17 review records and all expected source record IDs.

- [ ] **Step 5: Commit and push**

Review `git diff`, commit the scoped source/data/docs changes, and push `main` to `origin` without adding ignored videos, weights, outputs, workbooks, or previews.
