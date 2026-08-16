# Active Learning Round Two Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让新模型的整场扫描保存完整类别概率，并从第二轮开始用 top-2 margin 或归一化 entropy 确定性选择真正靠近决策边界的 8 个短片，同时保留冲突、稀有类、随机和漏报对照。

**Architecture:** 新推理输出升级为 format 3，`labels` 在根节点定义概率数组顺序，窗口只保存完整概率而不冗余保存可重算指标。双裁剪合并器严格分版本验证 v2/v3；独立 `uncertainty.py` 计算 top-2、margin 和 entropy，第二轮选择器把原来的高置信度桶替换为决策边界桶。

**Tech Stack:** Python 3.10+、PyTorch、NumPy、标准库 `math/dataclasses/hashlib/json`、`unittest`、Ruff。

## Global Constraints

- 当前 Rangitoto format-2 工件已经独立验证：far `1,483` 事件、near `2,799` 事件、两侧各 `16,448` 窗口、`2,942` 合并候选；JSON SHA-256 为 `e79aba6e5eb3a1075819a290144198b7e393fceed94313cdf9fc171378a76e7e`。
- 当前 format-2 首轮工件不得重写或升级；它已经保存显式 `source_window_indices`，首轮无需再次推理。
- 只有使用首轮累计标签训练出的新 checkpoint 扫描才写 format 3。
- format 3 根节点 `labels[i]` 必须定义每个窗口 `class_probabilities[i]` 的含义。
- 每个概率必须有限且位于 `[0,1]`，数组长度等于标签数，总和误差不超过 `1e-6`。
- `action` 必须是稳定 argmax，`confidence` 必须等于 `round(max(class_probabilities), 6)`。
- 推理 JSON 不持久化 margin/entropy；它们必须从完整概率重算，避免冗余字段漂移。
- 双裁剪输入必须版本相同、标签顺序相同；v2/v3 混合必须失败。
- v2 工件继续逐字节兼容验证；v3 的任一概率或标签篡改必须被来源哈希及独立重算发现。
- margin 定义为 `top1_probability - top2_probability`，越小越不确定。
- entropy 使用自然对数，`0 * log(0) = 0`，并除以 `log(class_count)` 归一化到 `[0,1]`；越大越不确定。
- canonical candidate 的 margin 取所有来源成员窗口中的最小值；entropy 取最大归一化 entropy。
- 第二轮仍恰好选择 40 段，目标配额为 `20/8/4/4/4`；只把首轮的 8 个高置信度尾部替换为 8 个决策边界短片。任一桶供给不足时继续使用首轮的顺序转移规则并完整记录，不得因此少于 40 段。
- 第二轮必须排除第一轮所有已覆盖时间块，即使新扫描的 event/group ID 已变化。
- 第二轮 selection 必须直接保存新扫描 checkpoint 的 SHA-256，并证明它与最近一轮 selection 的 checkpoint SHA-256 和 model version 都不同。
- 第二轮自包含的 merged JSON/CSV 是 selection 的可移植验证来源，必须像首轮 merged 工件一样纳入版本控制；原始 far/near 扫描目录仍保持忽略。
- 不确定性批次是偏置训练样本，不能当作 Accuracy、Recall 或产品精度。
- 每次模块或目录结构变化必须同步 `README.md`，且 `pyproject.toml` 作者名保持 `jmmyxa`。
- 测试使用 `unittest`，不要引入 pytest。

---

### Task 1: Inference JSON format 3 with complete probabilities

**Files:**
- Modify: `src/spiketrace/domain.py`
- Modify: `src/spiketrace/inference.py`
- Modify: `src/spiketrace/outputs.py`
- Modify: `tests/test_inference.py`
- Modify: `tests/test_outputs.py`

**Interfaces:**
- Extends: `ActionWindow.class_probabilities: tuple[float, ...] | None = None`.
- Extends: `write_inference_outputs(output_dir, *, metadata, model_version, events, windows, settings, event_window_indices, labels: Sequence[str] | None = None) -> tuple[Path, Path]`.
- Produces: format 3 when labels and probabilities are present; preserves format 2 otherwise.
- Preserves: event CSV remains event-only and does not contain seven probability columns.

- [ ] **Step 1: Write failing inference probability tests**

Use fixed logits with a known seven-class distribution:

```python
class _FixedLogitModel:
    def __call__(self, batch):
        row = torch.tensor([[0.0, 1.0, 2.0, 3.0, 4.0, 0.5, -1.0]])
        return row.repeat(batch.shape[0], 1)


def test_inference_writes_format_three_complete_probabilities(self):
    result = infer_video(
        self.video_path,
        self.checkpoint_path,
        self.output_dir,
        stride_seconds=0.2,
        confidence_threshold=0.0,
        batch_size=4,
        device="cpu",
    )
    payload = json.loads(Path(result["events_json"]).read_text())
    self.assertEqual(payload["format_version"], 3)
    self.assertEqual(payload["labels"], list(ACTION_LABELS))
    for window in payload["windows"]:
        probabilities = window["class_probabilities"]
        self.assertEqual(len(probabilities), 7)
        self.assertAlmostEqual(sum(probabilities), 1.0, places=6)
        self.assertEqual(window["action"], "attack")
        self.assertEqual(window["confidence"], round(max(probabilities), 6))
```

Run the same fixture with batch sizes 1 and 4 and assert the entire ordered `windows` arrays are equal.

- [ ] **Step 2: Run inference tests and verify RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_inference -v
```

Expected: format remains 2 and `class_probabilities` is absent.

- [ ] **Step 3: Extend the domain object without breaking historical constructors**

Append the optional field:

```python
@dataclass(frozen=True, slots=True)
class ActionWindow:
    start_seconds: float
    end_seconds: float
    action: str
    confidence: float
    class_probabilities: tuple[float, ...] | None = None
```

All existing four-positional-argument call sites and fixtures must continue to work.

- [ ] **Step 4: Preserve the complete softmax row during inference**

Derive both legacy top-1 fields from the same stored tuple:

```python
for (start, end), probability_row in zip(batch_times, probabilities.tolist()):
    values = tuple(float(value) for value in probability_row)
    top_index = max(range(len(values)), key=values.__getitem__)
    windows.append(ActionWindow(
        start_seconds=round(start, 6),
        end_seconds=round(end, 6),
        action=labels[top_index],
        confidence=round(values[top_index], 6),
        class_probabilities=values,
    ))
```

Pass `labels=labels` to the writer. Do not round each probability to six places; JSON float serialization preserves enough information for the sum check.

- [ ] **Step 5: Write failing format-3 schema rejection tests**

In `tests/test_outputs.py`, assert rejection of duplicate/empty labels, missing distributions, one distribution with wrong length, bool/NaN/Infinity, negative or over-one values, sum outside `1e-6`, action not equal to stable argmax and confidence mismatch. Assert this compatibility case remains format 2:

```python
json_path, _ = write_inference_outputs(
    output,
    metadata=self.metadata,
    model_version="test-v1",
    events=[self.event],
    windows=self.legacy_windows,
    settings=self.settings,
    event_window_indices={"evt_000001": [0, 1]},
)
self.assertEqual(json.loads(json_path.read_text())["format_version"], 2)
```

- [ ] **Step 6: Implement strict v2/v3 serialization branching**

Use an all-or-nothing rule:

```python
has_probabilities = [window.class_probabilities is not None for window in window_items]
if labels is None and any(has_probabilities):
    raise ValueError("labels are required when window probabilities are present.")
if labels is not None and not all(has_probabilities):
    raise ValueError("Every format-3 window must contain class probabilities.")
format_version = 3 if labels is not None else 2
```

For format 3 use root field order `format_version, labels, video, model_version, settings, events, windows`. Validate each probability sequence before opening final output files. Keep format-2 bytes/schema unchanged for legacy calls.

- [ ] **Step 7: Run output/inference regressions and commit**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_outputs tests.test_inference tests.test_events -v
.venv\Scripts\python.exe -m unittest discover -s tests -v
git add src/spiketrace/domain.py src/spiketrace/inference.py src/spiketrace/outputs.py tests/test_inference.py tests/test_outputs.py
git commit -m "feat: preserve full inference probabilities"
```

Expected: new inference writes format 3, legacy writer tests stay format 2, and all tests pass.

---

### Task 2: Strict dual-crop format-3 merge with v2 compatibility

**Files:**
- Modify: `src/spiketrace/dual_crop_review.py`
- Modify: `tests/test_dual_crop_review.py`
- Create: `tests/fixtures/dual_crop_review/far_v3.json`
- Create: `tests/fixtures/dual_crop_review/near_v3.json`

**Interfaces:**
- Extends: `build_dual_crop_review()` to accept same-version v2 or v3 inputs.
- Extends: `verify_dual_crop_review()` to independently recompute v2 or v3 artifacts.
- Produces: v3 merged root `format_version: 3`, `merge_format_version: 3`, `labels`, and embedded complete probabilities.
- Preserves: current v2 merged presentation bytes and CSV bytes exactly.

- [ ] **Step 1: Add literal v3 far/near fixtures and failing merge tests**

Copy the small semantic scenarios from the v2 fixtures, add canonical seven labels and a valid seven-value probability array to every window. Assert:

```python
payload = build_dual_crop_review(far_v3, near_v3, output_dir, repo_root=root)
self.assertEqual(payload["format_version"], 3)
self.assertEqual(payload["merge_format_version"], 3)
self.assertEqual(payload["labels"], list(ACTION_LABELS))
self.assertEqual(
    payload["input_runs"]["far"]["windows"][0]["class_probabilities"],
    far_payload["windows"][0]["class_probabilities"],
)
self.assertNotIn("class_probabilities", (output_dir / "merged_candidates.csv").read_text())
```

Also run the current v2 fixtures twice and assert output bytes remain identical to the pre-change expected bytes.

- [ ] **Step 2: Run dual-crop tests and verify RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_dual_crop_review -v
```

Expected: v3 fixtures are rejected because normalization requires format 2 and exact v2 fields.

- [ ] **Step 3: Split strict schema constants by version**

Define separate roots and windows; do not weaken `_require_fields`:

```python
_INFERENCE_ROOT_FIELDS = {
    2: ("format_version", "video", "model_version", "settings", "events", "windows"),
    3: ("format_version", "labels", "video", "model_version", "settings", "events", "windows"),
}
_WINDOW_FIELDS = {
    2: ("window_index", "start_seconds", "end_seconds", "action", "confidence"),
    3: ("window_index", "start_seconds", "end_seconds", "action", "confidence", "class_probabilities"),
}
```

Normalize and validate format 3 with the same probability rules as `outputs.py`. Return `labels` in the normalized run only for v3.

- [ ] **Step 4: Enforce same version and label order across crops**

Extend `_validate_cross_run_contract`:

```python
if far["format_version"] != near["format_version"]:
    raise ValueError("Far and near inference inputs must use the same format version.")
if far.get("labels") != near.get("labels"):
    raise ValueError("Far and near inference label order must match.")
```

For v3 output use algorithm version `dual-crop-merge-v3` and include root `labels`; keep duplicate/conflict rules and primary confidence semantics unchanged. Do not average distributions across crop views.

- [ ] **Step 5: Add tamper and version-mix tests**

Reject v2/v3 mixing, reordered labels, changed probability, probability action mismatch, one side missing labels, and v3 merged artifact with any changed embedded probability. Confirm v2 verifier still returns format/merge version 2 and the real current artifact still verifies:

```powershell
.venv\Scripts\python.exe -m spiketrace verify-dual-crop-review `
  outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.json `
  --csv outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.csv
```

Expected current counts: `1483/2799` source events, `2942` canonical events, `823` duplicate groups and `495` conflict groups.

- [ ] **Step 6: Run dual-crop tests and commit**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_dual_crop_review tests.test_outputs -v
git add src/spiketrace/dual_crop_review.py tests/test_dual_crop_review.py tests/fixtures/dual_crop_review/far_v3.json tests/fixtures/dual_crop_review/near_v3.json
git commit -m "feat: merge probability aware dual crop scans"
```

---

### Task 3: Auditable top-2, margin and entropy scoring

**Files:**
- Create: `src/spiketrace/uncertainty.py`
- Create: `tests/test_uncertainty.py`

**Interfaces:**
- Produces: `WindowUncertainty` and `UncertaintyAnchor` frozen dataclasses.
- Produces: `summarize_probabilities(labels, probabilities) -> WindowUncertainty`.
- Produces: `rank_merged_candidates_by_uncertainty(merged_payload, *, metric: Literal["margin", "entropy"]) -> list[UncertaintyAnchor]`.
- Consumes: independently validated merged format-3 payload.

- [ ] **Step 1: Write failing known-distribution metric tests**

```python
class ProbabilitySummaryTests(unittest.TestCase):
    def test_computes_stable_top_two_and_margin(self):
        result = summarize_probabilities(
            ["background", "serve", "receive"],
            [0.1, 0.45, 0.45],
        )
        self.assertEqual(result.top1_label, "serve")
        self.assertEqual(result.top2_label, "receive")
        self.assertAlmostEqual(result.margin, 0.0)

    def test_uniform_distribution_has_normalized_entropy_one(self):
        result = summarize_probabilities(["a", "b", "c", "d"], [0.25] * 4)
        self.assertAlmostEqual(result.normalized_entropy, 1.0, places=12)

    def test_zero_probabilities_do_not_create_nan(self):
        result = summarize_probabilities(["a", "b", "c"], [1.0, 0.0, 0.0])
        self.assertEqual(result.entropy, 0.0)
        self.assertEqual(result.normalized_entropy, 0.0)
```

- [ ] **Step 2: Run metric tests and verify RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_uncertainty.ProbabilitySummaryTests -v
```

Expected: import failure because `uncertainty.py` does not exist.

- [ ] **Step 3: Implement exact metric dataclasses and validation**

```python
@dataclass(frozen=True, slots=True)
class WindowUncertainty:
    top1_label: str
    top1_probability: float
    top2_label: str
    top2_probability: float
    margin: float
    entropy: float
    normalized_entropy: float


@dataclass(frozen=True, slots=True)
class UncertaintyAnchor:
    canonical_event_id: str
    start_ms: int
    end_ms: int
    side: str
    source_event_id: str
    window_index: int
    summary: WindowUncertainty
```

Validate unique labels, at least two classes and the full probability contract. Stable top-1/top-2 ties use original label index. Compute:

```python
entropy = -sum(value * math.log(value) for value in probabilities if value > 0.0)
normalized = entropy / math.log(len(probabilities))
margin = top1_probability - top2_probability
```

Clamp only floating noise within `1e-12`; reject materially out-of-range metrics.

- [ ] **Step 4: Write failing canonical-event aggregation tests**

Create a merged v3 payload with three canonical events, including one event with three member windows across far/near and exact margin/entropy ties across different events. Assert margin selects each event's smallest-margin member, entropy selects each event's largest-entropy member, one and only one anchor is returned per canonical event, and ties use `(start_ms, side order far/near, source_event_id, window_index, canonical_event_id)`.

```python
margin_ranked = rank_merged_candidates_by_uncertainty(payload, metric="margin")
self.assertEqual(margin_ranked[0].window_index, 7)
self.assertEqual(margin_ranked[0].summary.margin, 0.02)

entropy_ranked = rank_merged_candidates_by_uncertainty(payload, metric="entropy")
self.assertEqual(entropy_ranked[0].window_index, 9)
self.assertGreater(entropy_ranked[0].summary.normalized_entropy, 0.9)

self.assertEqual(
    [
        (item.canonical_event_id, item.side, item.source_event_id, item.window_index)
        for item in margin_ranked
    ],
    [
        ("evt_merged_000002", "far", "evt_far_b", 7),
        ("evt_merged_000001", "near", "evt_near_a", 9),
        ("evt_merged_000003", "far", "evt_far_c", 12),
    ],
)
self.assertEqual(
    len({item.canonical_event_id for item in margin_ranked}),
    len(margin_ranked),
)
self.assertEqual(
    [
        (item.canonical_event_id, item.side, item.source_event_id, item.window_index)
        for item in entropy_ranked
    ],
    [
        ("evt_merged_000001", "near", "evt_near_a", 9),
        ("evt_merged_000003", "far", "evt_far_c", 12),
        ("evt_merged_000002", "far", "evt_far_b", 7),
    ],
)
```

- [ ] **Step 5: Implement strict merged traversal and deterministic ranking**

Build maps from `(side, source_event_id)` and `(side, window_index)` to embedded v3 objects. For every canonical event, traverse only the exact `source_event_refs[*].member_window_indices`; verify ownership again and retain one anchor according to the requested metric. Sort margin ascending and entropy descending, then use the fixed tie-break. Reject v2 payloads with `"format-3 probabilities are required"` rather than inventing scores from top-1 confidence.

- [ ] **Step 6: Add mutation and range tests**

Cover malformed refs, missing windows, duplicate ownership, unsupported metric, v2 input, uniform distribution, exact ties and all returned metric ranges. Assert ranking is identical after JSON serialize/reload.

- [ ] **Step 7: Run uncertainty tests and commit**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_uncertainty -v
.venv\Scripts\ruff.exe check src/spiketrace/uncertainty.py tests/test_uncertainty.py
git add src/spiketrace/uncertainty.py tests/test_uncertainty.py
git commit -m "feat: rank action windows by uncertainty"
```

---

### Task 4: Round-two decision-boundary bucket

**Prerequisite:** Complete Tasks 1-2 of `2026-08-16-rangitoto-active-learning-round-01.md` first, including the selection-v1 loader, deterministic selector, CLI and tests. Before this task, `python -m unittest tests.test_active_learning_selection -v` must pass. If those files do not exist, implement the Round 01 prerequisite rather than treating the `Modify` entries below as standalone creates.

**Files:**
- Modify: `src/spiketrace/active_learning_selection.py`
- Modify: `src/spiketrace/cli.py`
- Modify: `tests/test_active_learning_selection.py`

**Interfaces:**
- Extends: `select_review_batch(merged_json_path, output_path, *, repo_root, round_number=1, seed=42, preferred_clip_seconds=15.0, min_clip_seconds=5.0, max_clip_seconds=30.0, min_anchor_gap_seconds=5.0, time_strata=10, previous_selection_paths=(), uncertainty_metric: Literal["margin", "entropy"] = "margin") -> dict[str, object]`.
- CLI: `select-review-batch MERGED_JSON OUTPUT_JSON --repo-root ROOT --round-number 2 --previous-selection PATH --uncertainty-metric margin|entropy`.
- Produces: selection format version 1 with `selection_algorithm_version: active-learning-selection-v2` for rounds `>=2`.
- Preserves: after the Round 01 prerequisite establishes its deterministic fixture baseline, adding the round-two branch must leave those format-2 selection bytes and bucket decisions unchanged.

- [ ] **Step 1: Write failing round-two quota and exclusion tests**

```python
class RoundTwoSelectionTests(unittest.TestCase):
    def test_replaces_only_the_eight_item_tail_bucket(self):
        payload = select_review_batch(
            self.merged_v3,
            self.output,
            repo_root=self.root,
            round_number=2,
            previous_selection_paths=[self.round_one],
            uncertainty_metric="margin",
        )
        counts = Counter(clip["selection_bucket"] for clip in payload["clips"])
        self.assertEqual(counts, {
            "conflict_or_minority": 20,
            "decision_boundary": 8,
            "dual_view_agreement": 4,
            "random_candidate_control": 4,
            "dual_background_control": 4,
        })
        self.assertNotIn("high_confidence_tail", counts)
        self.assertTrue(all(
            not intervals_overlap(old, new)
            for old in self.round_one_payload["clips"]
            for new in payload["clips"]
        ))

    def test_margin_bucket_uses_smallest_event_margins(self):
        payload = self.select_round_two(metric="margin")
        boundary = [clip for clip in payload["clips"] if clip["selection_bucket"] == "decision_boundary"]
        self.assertEqual(
            sorted(
                (
                    clip["anchor"]["uncertainty"]["selected_rank"],
                    clip["anchor"]["uncertainty"]["margin"],
                )
                for clip in boundary
            ),
            [(index, margin) for index, margin in enumerate(self.expected_margins, 1)],
        )

    def test_transfers_a_short_boundary_quota_without_reducing_the_batch(self):
        payload = self.select_round_two_with_only_five_eligible_boundary_anchors()
        self.assertEqual(len(payload["clips"]), 40)
        boundary_summary = next(
            item for item in payload["quota_summary"]
            if item["bucket"] == "decision_boundary"
        )
        self.assertEqual(boundary_summary["planned"], 8)
        self.assertEqual(boundary_summary["selected"], 5)
        self.assertEqual(boundary_summary["transferred_out"], 3)
        self.assertEqual(boundary_summary["transferred_to"], "dual_view_agreement")
```

- [ ] **Step 2: Run round-two tests and verify RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_active_learning_selection.RoundTwoSelectionTests -v
```

Expected: function rejects the new keyword or continues using the high-confidence bucket.

- [ ] **Step 3: Branch selection rules only after source validation**

For `round_number == 1`, require the current v2/v3 source rules but keep `active-learning-selection-v1` and original quotas. For `round_number >= 2`, require merged format 3, canonical seven labels, metric `margin` or `entropy`, and exactly one valid previous selection for every round in `range(1, round_number)`. Load all previous selections first, require the same video ID and SHA, reject duplicate paths/hashes, duplicate round numbers, missing rounds, round zero/current/future rounds, then sort by numeric `round_number` regardless of CLI argument order. Use the maximum round, necessarily `round_number - 1`, as `latest_previous_selection`; use every sorted artifact for clip/source exclusion and serialize `previous_selections` in that canonical order.

Recompute the merged hash and call the Round 01 `validate_merged_review_source` boundary so v2 and v3 selections share the exact `source` keys `merged_json`, `merged_json_sha256`, `checkpoint`, `checkpoint_sha256`, `inference_runs`, `format_version`, `merge_format_version`, and `model_version`. Keep `inference_runs.far|near.source_file`, `source_file_sha256`, and `normalized_payload_sha256` unchanged from the verified merged payload; do not introduce an alternate `source_provenance` schema. Require far/near checkpoint path and SHA to agree, and require the new checkpoint SHA and model version to differ from `latest_previous_selection`. Then set:

```python
ROUND_TWO_QUOTAS = (
    ("conflict_or_minority", 20),
    ("decision_boundary", 8),
    ("dual_view_agreement", 4),
    ("random_candidate_control", 4),
    ("dual_background_control", 4),
)
```

The first, third, fourth and fifth buckets keep Task 2 behavior, including ordered quota transfer, on the new scan. The decision-boundary bucket iterates `rank_merged_candidates_by_uncertainty`, applying all current uniqueness/time filters before reserving an anchor. Record the rank in the full uncertainty pool and a consecutive rank among accepted boundary clips. Selection decisions follow bucket/ranking order, but the final `clips` array and IDs retain the Round 01 chronological ordering contract.

- [ ] **Step 4: Persist derived uncertainty evidence in selection only**

Add this object to a boundary anchor and its matching candidate hint:

```python
"uncertainty": {
    "metric": uncertainty_metric,
    "pool_rank": pool_rank,
    "selected_rank": selected_rank,
    "top1_label": summary.top1_label,
    "top1_probability": summary.top1_probability,
    "top2_label": summary.top2_label,
    "top2_probability": summary.top2_probability,
    "margin": summary.margin,
    "entropy": summary.entropy,
    "normalized_entropy": summary.normalized_entropy,
    "side": anchor.side,
    "source_event_id": anchor.source_event_id,
    "window_index": anchor.window_index,
}
```

Record `uncertainty_metric` in selection settings. This is derived audit evidence; do not copy it into inference windows.

- [ ] **Step 5: Extend selection loader and CLI compatibility tests**

Allow algorithm versions v1 and v2 with version-specific required fields. Both versions require the exact Round 01 `source` schema. Reject v2 algorithm without format-3 source, absent/incomplete/non-contiguous prior rounds, duplicate or out-of-range round numbers, previous selections from another video, unchanged model version or checkpoint SHA versus the numerically latest prior round, unknown metric, overlap with any previous clip, and duplicate source/group in the new batch. Test that shuffled `--previous-selection` argument order produces identical canonical JSON and that round three compares against round two while still excluding both rounds one and two. Add mutation tests for `source.merged_json_sha256`, `source.checkpoint`, `source.checkpoint_sha256`, and every `source.inference_runs.far|near.source_file_sha256` and `normalized_payload_sha256`; each mismatch against the newly validated merged input must fail. Capture the Round 01 fixture bytes after its prerequisite implementation passes, then assert the round-two extension reproduces those bytes exactly rather than claiming compatibility with a selector that did not previously exist.

Parse repeatable `--previous-selection` and `--uncertainty-metric`, and pass exact values to the selector.

- [ ] **Step 6: Run selection and full regression tests**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_active_learning_selection tests.test_uncertainty -v
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: both metrics produce deterministic 40-clip batches, the established Round 01 selection fixture is byte-identical, and the historical merged format-2 review artifact remains valid.

- [ ] **Step 7: Commit round-two selection**

```powershell
git add src/spiketrace/active_learning_selection.py src/spiketrace/cli.py tests/test_active_learning_selection.py
git commit -m "feat: select round two decision boundaries"
```

---

### Task 5: README pipeline, real-run gate and final verification

**Files:**
- Modify: `README.md`
- Modify: `.gitignore`
- Generated after Round 01 training, ignored: `outputs/rangitoto-active-round-01-v3/{far,near}/events.json`
- Created after prerequisites exist and tracked: `outputs/rangitoto-active-round-01-v3/merged/merged_candidates.json`
- Created after prerequisites exist and tracked: `outputs/rangitoto-active-round-01-v3/merged/merged_candidates.csv`
- Created after prerequisites exist: `data/active-learning/rangitoto/round-02-selection.json`

**Interfaces:**
- Documents: the exact dependency chain `Round 01 human labels -> staged checkpoint -> v3 rescan -> uncertainty selection -> Round 02 workbook`.
- Prevents: running a v3 rescan with the old bootstrap checkpoint and calling it a new learning round.

- [ ] **Step 1: Synchronize program structure and JSON-version documentation**

Add `uncertainty.py`, its test, v3 fixtures and the v3 inference/merge contract to the README tree. State that v2 remains the immutable first-round source and v3 is required only for margin/entropy selection. Extend `.gitignore` with this exact narrow allowlist; Git ignore files do not support brace expansion:

```gitignore
!outputs/rangitoto-active-round-01-v3/
outputs/rangitoto-active-round-01-v3/*
!outputs/rangitoto-active-round-01-v3/merged/
outputs/rangitoto-active-round-01-v3/merged/*
!outputs/rangitoto-active-round-01-v3/merged/merged_candidates.json
!outputs/rangitoto-active-round-01-v3/merged/merged_candidates.csv
```

Keep the far/near events, checkpoints, proxies and previews ignored. Add repository-policy tests showing `git check-ignore` exits `1` for both durable merged files and `0` for `outputs/rangitoto-active-round-01-v3/far/events.json`.

- [ ] **Step 2: Document prerequisite checks before any long scan**

Require all of the following before running the commands below:

```text
1. Round 01 workbook has all 40 clips filled and extracted.
2. Round 01 cumulative manifest passed validation.
3. An independent complete val match exists.
4. train-staged-r3d18 completed and its best.pt passed the declared val promotion gate.
5. The new checkpoint `model_version` and SHA-256 both differ from the bootstrap checkpoint and from the checkpoint recorded in Round 01 selection.
```

If any condition is false, stop before scanning and do not create `round-02-selection.json`.

- [ ] **Step 3: Document the exact v3 rescan and merge commands**

```powershell
.venv\Scripts\spiketrace.exe infer data\YTDown.com_YouTube_Rangitoto-vs-Taka-National-Final-Sets-1-_Media_k3PdQgm2jVs_001_1080p.mp4 `
  runs\action-active-round-01\best.pt `
  outputs\rangitoto-active-round-01-v3\far `
  --stride-seconds 0.4 --confidence-threshold 0.2 `
  --batch-size 8 --device cuda --crop 0,0,1920,645

.venv\Scripts\spiketrace.exe infer data\YTDown.com_YouTube_Rangitoto-vs-Taka-National-Final-Sets-1-_Media_k3PdQgm2jVs_001_1080p.mp4 `
  runs\action-active-round-01\best.pt `
  outputs\rangitoto-active-round-01-v3\near `
  --stride-seconds 0.4 --confidence-threshold 0.2 `
  --batch-size 8 --device cuda --crop 0,255,1920,1080

.venv\Scripts\spiketrace.exe build-dual-crop-review `
  outputs\rangitoto-active-round-01-v3\far\events.json `
  outputs\rangitoto-active-round-01-v3\near\events.json `
  outputs\rangitoto-active-round-01-v3\merged --repo-root .

.venv\Scripts\spiketrace.exe verify-dual-crop-review `
  outputs\rangitoto-active-round-01-v3\merged\merged_candidates.json `
  --csv outputs\rangitoto-active-round-01-v3\merged\merged_candidates.csv
```

Verify both inputs and merged output report format 3, the same seven-label order, equal window counts, complete probabilities and a checkpoint SHA distinct from the bootstrap scan. The merged JSON must remain self-contained so `verify-dual-crop-review` can recompute its canonical events and CSV bytes without the ignored far/near files. The generated Round 02 selection must copy that exact SHA into `source.checkpoint_sha256`.

- [ ] **Step 4: Document round-two selection and workbook commands**

```powershell
.venv\Scripts\spiketrace.exe select-review-batch `
  outputs\rangitoto-active-round-01-v3\merged\merged_candidates.json `
  data\active-learning\rangitoto\round-02-selection.json `
  --repo-root . --round-number 2 --seed 42 `
  --previous-selection data\active-learning\rangitoto\round-01-selection.json `
  --uncertainty-metric margin

node tools\build_active_review_batch.mjs `
  data\active-learning\rangitoto\round-02-selection.json `
  outputs\active-learning\rangitoto\round-02 `
  outputs\active-learning\rangitoto\round-02-previews
```

State that entropy is an explicit alternative experiment; do not generate both and choose whichever looks better without recording that choice as validation-driven tuning.

- [ ] **Step 5: Add real-run acceptance checks**

For a legitimate round-two run with at least eight eligible boundary anchors, assert exactly 40 unique non-overlapping clips, eight `decision_boundary` clips, no overlap with round one and at least 10 time strata. Require the Round 02 `source` video/checkpoint and every nested inference-run hash to match the newly verified format-3 merged artifact exactly. Separately require `previous_selections[0]` to contain the normalized Round 01 selection path and its exact file SHA-256, then load that artifact and verify its own source/video/checkpoint provenance. Every boundary hint must be traceable to a format-3 `(side, source_event_id, window_index)` probability row. Also run a shortage fixture proving quota transfer still returns 40 clips and records the deficit; do not require eight boundary clips when fewer than eight legal anchors exist. In an integration fixture repository containing only the allowlisted merged JSON/CSV, both selections and a content-matching source video, prove Round 01 and Round 02 selections load without either ignored far/near scan directory.

- [ ] **Step 6: Run all engineering gates**

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\ruff.exe check .
.venv\Scripts\python.exe -m compileall -q src tests
node tools\test_active_review_batch.mjs
.venv\Scripts\python.exe -m spiketrace verify-dual-crop-review `
  outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.json `
  --csv outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.csv
git diff --check
```

Expected: all tests and checks pass; the existing v2 artifact still reports its original counts and hashes. A real v3 scan is not required for engineering completion if the staged checkpoint prerequisite does not yet exist.

- [ ] **Step 7: Commit scoring documentation and, only when eligible, round-two selection**

```powershell
git add README.md .gitignore
git commit -m "docs: explain uncertainty driven review rounds"
```

After all real-run prerequisites and acceptance checks pass, first commit the independently verified, self-contained source:

```powershell
git add `
  outputs\rangitoto-active-round-01-v3\merged\merged_candidates.json `
  outputs\rangitoto-active-round-01-v3\merged\merged_candidates.csv
git commit -m "data: preserve round two scan evidence"
```

Then load the selection against those tracked source bytes and commit it separately:

```powershell
git add data\active-learning\rangitoto\round-02-selection.json
git commit -m "data: select round two review clips"
```

Finally repeat the loader check from a clean checkout of that commit with only the source video supplied externally. Never commit the raw far/near scan directories, checkpoint, proxy clips or previews.
