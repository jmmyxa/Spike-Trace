# Rangitoto Review Trust Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一训练与整场推理的帧采样，保存真实事件窗口来源，并生成可在其他设备独立审计的 Rangitoto 双裁剪复核材料。

**Architecture:** `video.py` 提供唯一的 CFR 帧号合同；`events.py` 在首次事件合并时保存成员窗口索引；`dual_crop_review.py` 从两路 inference JSON v2 确定性生成并验证自包含 JSON/CSV。受版本控制的 Node 工具只把已验证 JSON 渲染为四页 XLSX，最终使用原 checkpoint 重新扫描 far/near 后再更新 README 和复核工件。

**Tech Stack:** Python 3.10+、NumPy、OpenCV、PyTorch/torchvision、`unittest`、Ruff、Node.js、`@oai/artifact-tool`。

## Global Constraints

- 采样合同精确命名为 `center-nearest-frame-v1`。
- 帧号必须使用 `clamp(floor(sample_time * fps + 0.5), 0, frame_count - 1)`；不得使用 banker rounding。
- 本规则只覆盖恒定帧率视频的 frame ordinal；不得把它描述为 VFR 合同。
- 历史 checkpoint 缺少 `sampling_contract` 时必须兼容并解释为 `center-nearest-frame-v1`；不提高 checkpoint format 版本。
- 旧 floor-sampling Rangitoto 输出不得作为人工复核输入；无需重训 checkpoint，但必须重新跑 far/near 全场推理。
- far crop 固定为 `0,0,1920,645`；near crop 固定为 `0,255,1920,1080`。
- 重跑固定使用 `1.0s` 窗、`0.4s` 步长、`0.2` 置信阈值和现有 `runs/rangitoto-r3d18-bootstrap/best.pt`。
- 最终 JSON 必须自包含两路完整输入和全部窗口；完整窗口不得加入 XLSX。
- 工作簿只保留 `概览`、`候选动作`、`来源事件`、`标签说明` 四页。
- 工作簿人工输入只有 `人工确认动作`、`人工开始时间`、`人工结束时间`、`人工侧别`、`备注`；不新增审核状态、复选框或完成列。
- `人工确认动作` 非空即表示已复核；`background` 表示误检；人工时间允许精确到秒。
- `receive` 仅指接发球；`dig` 指针对进攻的防守起球；`far`/`near` 只表示画面远近，不代表球队。
- 不开始号码归属、前端、账户、数据库或球员统计功能。
- 每次程序结构变化同步 README，且 `pyproject.toml` 作者名必须保持 `jmmyxa`。
- XLSX 必须使用 `@oai/artifact-tool`；不要再次运行已经成功执行过的 spreadsheet artifact marker。
- 不提交视频、checkpoint、`runs/`、`node_modules`、预览图或旧 floor-sampling 原始输出。

---

### Task 1: Center-nearest frame sampling contract

**Files:**
- Modify: `src/spiketrace/constants.py`
- Modify: `src/spiketrace/video.py`
- Modify: `src/spiketrace/ml.py`
- Modify: `src/spiketrace/training.py`
- Modify: `src/spiketrace/inference.py`
- Modify: `tests/test_video.py`
- Modify: `tests/test_ml.py`
- Modify: `tests/test_training.py`
- Modify: `tests/test_inference.py`

**Interfaces:**
- Produces: `SAMPLING_CONTRACT = "center-nearest-frame-v1"`.
- Produces: `clip_sample_frame_indices(start_seconds, end_seconds, *, num_frames, fps, frame_count) -> tuple[int, ...]`.
- Produces: every newly written config, checkpoint and inference settings object contains `sampling_contract`.
- Compatibility: `load_checkpoint` returns historical checkpoints with the missing field normalized to `SAMPLING_CONTRACT`.

- [ ] **Step 1: Write the failing frame-index tests**

Add literal, hand-derived expectations to `tests/test_video.py`:

```python
from spiketrace.video import clip_sample_frame_indices, sample_video_clip


class ClipSampleFrameIndexTests(unittest.TestCase):
    def test_uses_half_up_nearest_frames_for_thirty_fps_window(self):
        self.assertEqual(
            clip_sample_frame_indices(
                0.0, 1.0, num_frames=16, fps=30.0, frame_count=120
            ),
            (1, 3, 5, 7, 8, 10, 12, 14, 16, 18, 20, 22, 23, 25, 27, 29),
        )

    def test_preserves_duplicate_indices_and_clamps_the_tail(self):
        self.assertEqual(
            clip_sample_frame_indices(
                0.0, 0.2, num_frames=4, fps=10.0, frame_count=2
            ),
            (0, 1, 1, 1),
        )

    def test_supports_non_integer_fps_and_non_integer_window(self):
        self.assertEqual(
            clip_sample_frame_indices(
                0.2, 0.7, num_frames=3, fps=29.97, frame_count=100
            ),
            (8, 13, 18),
        )

    def test_rejects_invalid_sampling_parameters(self):
        invalid = [
            (-0.1, 1.0, 1, 30.0, 30),
            (1.0, 1.0, 1, 30.0, 30),
            (0.0, 1.0, 0, 30.0, 30),
            (0.0, 1.0, 1, 0.0, 30),
            (0.0, 1.0, 1, 30.0, 0),
        ]
        for start, end, frames, fps, count in invalid:
            with self.subTest((start, end, frames, fps, count)), self.assertRaises(
                VideoError
            ):
                clip_sample_frame_indices(
                    start,
                    end,
                    num_frames=frames,
                    fps=fps,
                    frame_count=count,
                )
```

The mutation caught is replacing `+ 0.5` with floor or banker rounding.

- [ ] **Step 2: Run the helper tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_video.ClipSampleFrameIndexTests -v
```

Expected: import failure because `clip_sample_frame_indices` does not exist.

- [ ] **Step 3: Implement the single frame-index helper and use it in both decoders**

Add `SAMPLING_CONTRACT` in `constants.py`. In `video.py`, compute midpoint sample times without `np.round`, use `floor(value + 0.5)`, and clamp each result. Change `sample_video_frames` to inspect FPS/frame count once and seek with `CAP_PROP_POS_FRAMES`; change `iter_sequential_video_clip_batches.next_clip` to call the same helper. Keep crop, RGB conversion, resize, duplicate-slot handling and one-pass decoding behavior unchanged.

```python
def clip_sample_frame_indices(
    start_seconds: float,
    end_seconds: float,
    *,
    num_frames: int,
    fps: float,
    frame_count: int,
) -> tuple[int, ...]:
    if (
        not isfinite(start_seconds)
        or not isfinite(end_seconds)
        or not isfinite(fps)
        or start_seconds < 0
        or end_seconds <= start_seconds
        or num_frames <= 0
        or fps <= 0
        or frame_count <= 0
    ):
        raise VideoError("Sampling parameters are invalid.")
    duration = end_seconds - start_seconds
    return tuple(
        min(
            frame_count - 1,
            max(0, floor((start_seconds + (index + 0.5) * duration / num_frames) * fps + 0.5)),
        )
        for index in range(num_frames)
    )
```

- [ ] **Step 4: Verify helper GREEN and add byte-equality integration test**

Run the helper test, then add a real MJPG fixture assertion to `SequentialClipBatchTests`:

```python
def test_random_and_sequential_decoders_return_identical_rgb_clips(self):
    expected = sample_video_clip(
        self.video_path,
        0.2,
        0.8,
        num_frames=6,
        image_size=4,
        crop=(1, 1, 7, 5),
    )
    batches = list(
        iter_sequential_video_clip_batches(
            self.video_path,
            [(0.2, 0.8)],
            num_frames=6,
            image_size=4,
            batch_size=1,
            crop=(1, 1, 7, 5),
        )
    )
    np.testing.assert_array_equal(batches[0][1][0], expected)
```

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_video -v
```

Expected: all `tests.test_video` cases pass and the equality test would fail if either decoder changed frame ordinals.

- [ ] **Step 5: Write failing serialization and compatibility tests**

In `tests/test_ml.py`, assert a new checkpoint contains the contract and a saved historical checkpoint without the field loads with the contract. In `tests/test_training.py`, assert `training_config.json["sampling_contract"] == SAMPLING_CONTRACT`. In `tests/test_inference.py`, assert `events.json["settings"]["sampling_contract"] == SAMPLING_CONTRACT` even when the mocked checkpoint omits the field.

```python
self.assertEqual(checkpoint["sampling_contract"], SAMPLING_CONTRACT)
self.assertEqual(loaded["sampling_contract"], SAMPLING_CONTRACT)
self.assertEqual(config["sampling_contract"], SAMPLING_CONTRACT)
self.assertEqual(payload["settings"]["sampling_contract"], SAMPLING_CONTRACT)
```

- [ ] **Step 6: Run serialization tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_ml tests.test_training tests.test_inference -v
```

Expected: failures because config/checkpoint/inference payloads do not yet contain `sampling_contract`.

- [ ] **Step 7: Write and normalize sampling metadata**

Extend `make_checkpoint` with a keyword argument defaulting to `SAMPLING_CONTRACT` and include it in the payload. In `load_checkpoint`, copy the loaded dict before `setdefault("sampling_contract", SAMPLING_CONTRACT)` and reject any explicitly different contract with `CheckpointError`. Add the constant to the training config and inference settings; keep checkpoint format version `1`.

- [ ] **Step 8: Run Task 1 tests and full regression suite**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_video tests.test_ml tests.test_training tests.test_inference -v
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: all tests pass with no warnings or errors.

- [ ] **Step 9: Commit Task 1**

```powershell
git add src/spiketrace/constants.py src/spiketrace/video.py src/spiketrace/ml.py src/spiketrace/training.py src/spiketrace/inference.py tests/test_video.py tests/test_ml.py tests/test_training.py tests/test_inference.py
git commit -m "fix: unify video frame sampling"
```

---

### Task 2: Event member provenance and inference JSON v2

**Files:**
- Modify: `src/spiketrace/events.py`
- Modify: `src/spiketrace/inference.py`
- Modify: `src/spiketrace/outputs.py`
- Modify: `tests/test_events.py`
- Modify: `tests/test_inference.py`
- Create: `tests/test_outputs.py`

**Interfaces:**
- Produces: `merge_action_windows_with_provenance(...) -> tuple[list[ActionEvent], dict[str, list[int]]]`.
- Preserves: `merge_action_windows(...) -> list[ActionEvent]` as a compatibility wrapper.
- Produces: inference JSON `format_version: 2` and event field `source_window_indices`.
- Produces: every serialized window has stable `window_index` equal to its position in `windows`.

- [ ] **Step 1: Write the failing interrupted-same-action provenance test**

Add this method to the existing `MergeActionWindowsTests` class in `tests/test_events.py`:

```python
from spiketrace.events import merge_action_windows_with_provenance


class MergeActionWindowsTests(unittest.TestCase):
    def test_provenance_does_not_reassign_windows_across_an_interruption(self):
        windows = [
            ActionWindow(0.0, 1.0, "attack", 0.9),
            ActionWindow(0.4, 1.4, "set", 0.8),
            ActionWindow(0.8, 1.8, "attack", 0.7),
        ]
        events, provenance = merge_action_windows_with_provenance(
            windows,
            video_id="match",
            model_version="test-v1",
            confidence_threshold=0.5,
            merge_gap_seconds=0.25,
        )
        self.assertEqual(
            [event.action for event in events], ["attack", "set", "attack"]
        )
        self.assertEqual(
            provenance,
            {"evt_000001": [0], "evt_000002": [1], "evt_000003": [2]},
        )
        self.assertEqual(
            len({index for values in provenance.values() for index in values}), 3
        )
```

The mutation caught is reconstructing membership later from action/time overlap.

- [ ] **Step 2: Run provenance test and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_events -v
```

Expected: import failure because the provenance API does not exist.

- [ ] **Step 3: Implement provenance in the existing state machine**

Materialize the input iterable once, enumerate it before sorting, and store each original position in `_EventCandidate.window_indices`. When extending a candidate, append only that exact index. Build the mapping only for events that survive `min_event_seconds`; return sorted, unique indices. Implement `merge_action_windows` by discarding the mapping from the new API so its public behavior remains unchanged.

- [ ] **Step 4: Verify provenance GREEN and edge exclusions**

Add assertions that background, low-confidence, invalid-duration and minimum-duration-filtered windows never appear in the mapping, then run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_events -v
```

Expected: all event tests pass.

- [ ] **Step 5: Write failing JSON v2 output tests**

Create `tests/test_outputs.py` using temporary paths and real `ActionEvent`, `ActionWindow` and `VideoMetadata` values. Assert:

```python
self.assertEqual(payload["format_version"], 2)
self.assertEqual(payload["events"][0]["source_window_indices"], [0, 1])
self.assertEqual(
    [window["window_index"] for window in payload["windows"]],
    [0, 1],
)
self.assertNotIn("source_window_indices", csv_text.splitlines()[0])
```

Also assert `write_inference_outputs` rejects a missing event mapping, out-of-range index, duplicate member, action mismatch, confidence below threshold, and one window assigned to two events.

- [ ] **Step 6: Run output tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_outputs -v
```

Expected: failure because `write_inference_outputs` has no provenance parameter and writes format version 1.

- [ ] **Step 7: Upgrade inference serialization with strict invariants**

Change `write_inference_outputs` to accept `event_window_indices: dict[str, list[int]]`. Validate every event ID has one mapping; indices are integer, increasing, unique and in range; members match event action and threshold from settings; global member indices do not repeat. Serialize `source_window_indices` on each event and `window_index` on every window. Keep CSV as the existing event-only view.

Change `infer_video` to call `merge_action_windows_with_provenance` and pass the mapping to the writer. Add `checkpoint_sha256`, `video_sha256`, `opencv_version`, `torch_version`, `torchvision_version`, video metadata, crop, window, stride, threshold, batch size, device and sampling contract to settings using stable string values.

- [ ] **Step 8: Run Task 2 tests and full regression suite**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_events tests.test_outputs tests.test_inference -v
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: all tests pass; inference JSON v2 retains complete windows and explicit source indices.

- [ ] **Step 9: Commit Task 2**

```powershell
git add src/spiketrace/events.py src/spiketrace/inference.py src/spiketrace/outputs.py tests/test_events.py tests/test_inference.py tests/test_outputs.py
git commit -m "feat: preserve inference window provenance"
```

---

### Task 3: Deterministic dual-crop merge and verifier

**Files:**
- Create: `src/spiketrace/dual_crop_review.py`
- Modify: `src/spiketrace/cli.py`
- Create: `tests/fixtures/dual_crop_review/far.json`
- Create: `tests/fixtures/dual_crop_review/near.json`
- Create: `tests/test_dual_crop_review.py`

**Interfaces:**
- Produces: `build_dual_crop_review(far_path, near_path, output_dir, *, repo_root) -> dict[str, object]`.
- Produces: `verify_dual_crop_review(json_path, *, csv_path=None) -> dict[str, object]`.
- CLI: `spiketrace build-dual-crop-review FAR_JSON NEAR_JSON OUTPUT_DIR --repo-root ROOT`.
- CLI: `spiketrace verify-dual-crop-review MERGED_JSON --csv MERGED_CSV`.
- Output: `merged_candidates.json` and UTF-8 BOM `merged_candidates.csv`, both deterministically ordered.

- [ ] **Step 1: Add minimal inference JSON v2 fixtures**

Create two small fixtures for one video and model. Each input must include four indexed windows and explicit event member indices. The fixture must produce:

- one far-only `serve`;
- one far/near same-action `attack` duplicate with at least `400ms` overlap;
- one far `block` versus near `attack` conflict retained as two candidates;
- absolute Windows-style source paths that must not survive normalization.

Use `center-nearest-frame-v1`, format version `2`, half-open windows and unique indices. Set the fixture video SHA to 64 `0` characters and checkpoint SHA to 64 `1` characters; the builder separately computes each fixture JSON file hash.

- [ ] **Step 2: Write failing merge and normalization tests**

In `tests/test_dual_crop_review.py`, call the public builder and assert:

```python
self.assertEqual(payload["format_version"], 2)
self.assertEqual(payload["merge_format_version"], 2)
self.assertEqual(set(payload["input_runs"]), {"far", "near"})
self.assertEqual(len(payload["input_runs"]["far"]["windows"]), 4)
self.assertEqual(len(payload["input_runs"]["near"]["windows"]), 4)
self.assertEqual(len(payload["duplicate_groups"]), 1)
self.assertEqual(len(payload["conflict_groups"]), 1)
self.assertEqual(len(payload["events"]), 4)
self.assertNotIn("E:\\\\", json_path.read_text(encoding="utf-8"))
self.assertEqual(len(csv_rows), len(payload["events"]))
```

Assert the duplicate primary uses highest event confidence, then maximum member-window confidence, member count, shorter duration and lexical `far` order as successive tie-breakers. Assert all different-action conflict candidates remain present.

- [ ] **Step 3: Run merge tests and verify RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_dual_crop_review -v
```

Expected: import failure because `dual_crop_review.py` does not exist.

- [ ] **Step 4: Implement strict input validation and path normalization**

In `dual_crop_review.py`, validate format `2`, exact sampling contract, unique complete window indices, source-event member invariants, matching video SHA/metadata/model/checkpoint SHA and matching settings except crop/device. Convert paths under `repo_root` to POSIX repository-relative strings. For external paths store only basename plus SHA; do not serialize parent directories. Store each source file's byte SHA-256 and the stable-JSON SHA-256 of its normalized embedded payload.

- [ ] **Step 5: Implement deterministic duplicate/conflict grouping and outputs**

Use integer milliseconds and half-open intervals. Apply these exact rules:

```python
same_action_duplicate = (
    sides_differ
    and actions_equal
    and overlap_ms >= 400
    and (coverage_shorter >= 0.5 or center_gap_ms <= 500)
)
different_action_conflict = (
    sides_differ
    and not actions_equal
    and (overlap_ms >= 400 or center_gap_ms <= 500)
)
```

Build connected components only from duplicate links. Keep conflict links as annotations. Assign stable `evt_merged_000001`, `dg_000001` and `cg_000001` IDs after sorting by `(start_ms, end_ms, action, side, source_event_id)`. Embed the full normalized far/near payloads under `input_runs`; candidates reference `side`, source event ID and exact member indices instead of copying window objects. Record `algorithm_version: "dual-crop-merge-v2"`, `time_unit: "ms"`, `interval_semantics: "half_open"`, the exact duplicate/conflict thresholds and both source/normalized SHA-256 values under `settings`.

Write JSON with `ensure_ascii=False`, `indent=2`, LF newlines and a final newline. Write CSV with UTF-8 BOM, the existing event fields first, then merge-specific provenance fields.

- [ ] **Step 6: Implement independent recomputation verifier**

`verify_dual_crop_review` must validate the embedded inputs, recompute all candidate IDs, primary selections, duplicate groups, conflict groups, source references and CSV rows in memory, then compare them to the artifact. It must reject tampered member indices, missing windows, duplicated `(side, window_index)`, altered grouping metrics, absolute paths and CSV row drift. Return literal counts and SHA-256 values only after all checks pass.

- [ ] **Step 7: Add tamper tests and verify GREEN**

Deep-copy the generated payload, independently mutate one property per subtest, write it, and assert `ValueError` for:

```text
deleted input window
duplicate input window_index
source event member moved to a neighboring event
event confidence changed
duplicate group link removed
conflict candidate removed
absolute path inserted
CSV candidate_id changed
```

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_dual_crop_review -v
```

Expected: the valid fixture passes and every tampered artifact is rejected.

- [ ] **Step 8: Add and test both CLI commands**

Extend `build_parser` and `run_command`, then test real temporary-file dispatch rather than only parser fields:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_dual_crop_review -v
.venv\Scripts\python.exe -m spiketrace --help
```

Expected: help lists `build-dual-crop-review` and `verify-dual-crop-review`; tests pass.

- [ ] **Step 9: Run full Python regression suite and commit Task 3**

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
git add src/spiketrace/dual_crop_review.py src/spiketrace/cli.py tests/fixtures/dual_crop_review tests/test_dual_crop_review.py
git commit -m "feat: add auditable dual crop review artifacts"
```

---

### Task 4: Versioned XLSX review builder and README structure

**Files:**
- Create: `tools/build_rangitoto_review.mjs`
- Create: `tools/verify_rangitoto_review.mjs`
- Create: `tools/test_rangitoto_review.mjs`
- Modify: `README.md`
- Modify: `.gitignore`

**Interfaces:**
- Build command: `node tools/build_rangitoto_review.mjs MERGED_JSON OUTPUT_XLSX PREVIEW_DIR`.
- Verify command: `node tools/verify_rangitoto_review.mjs MERGED_JSON OUTPUT_XLSX`.
- Input: a merged JSON that has already passed Python verification.
- Output: exactly four sheets in order: `概览`, `候选动作`, `来源事件`, `标签说明`.

- [ ] **Step 1: Load the spreadsheet runtime and read the required API references**

Call the workspace dependency loader once, use only its Node executable and `node_modules`, and read `style_guidelines.md` plus `artifact_tool_docs/API_QUICK_START.md` completely. Create a junction to the loader-provided `node_modules` inside the existing ignored conversation build directory. Do not run `mark_artifact_operation_started.mjs` again because it has already succeeded for this workbook operation.

- [ ] **Step 2: Write failing executable workbook tests**

Create `tools/test_rangitoto_review.mjs` to run the builder against the small Task 3 merged fixture in a temporary directory, import the result with `SpreadsheetFile.importXlsx`, and assert:

```text
sheet order is exactly 概览,候选动作,来源事件,标签说明
候选动作 has exactly five editable headers
editable headers are 人工确认动作,人工开始时间,人工结束时间,人工侧别,备注
all editable cells are blank
人工确认动作 validation contains the seven labels including background
人工侧别 validation contains far,near,不确定
candidate row count equals merged JSON events length
source row count equals the sum of embedded input source events
formula scan returns zero errors
```

- [ ] **Step 3: Run workbook tests and verify RED**

Run with the loader-provided Node executable and build-directory dependency junction:

```powershell
node tools/test_rangitoto_review.mjs
```

Expected: module-not-found failure because the versioned builder and verifier do not exist.

- [ ] **Step 4: Move only presentation logic into the versioned builder**

Adapt the existing `outputs/.rangitoto-review-build/build_review.mjs` styles, widths, validations and four-sheet layout, but remove its JSON merge, source-window reconstruction, hard-coded input paths and hard-coded output paths. Import JSON, reject format versions other than `2`, and create the workbook only after invoking the Python verifier command successfully. Do not include a 32,896-row window sheet.

Keep the candidate sheet's model/provenance columns read-only and the five manual columns yellow. Use typed numeric seconds for start/end values and text only for event IDs, labels, sides and readable timecodes.

- [ ] **Step 5: Implement compact read-only workbook verification and render all sheets**

Use artifact-tool inspection to check sheet names, key ranges, candidate/source counts, blank input cells and formula errors. Render one PNG per sheet into the ignored preview directory. The build fails before export if any invariant fails.

- [ ] **Step 6: Run workbook tests and visual verification**

```powershell
node tools/test_rangitoto_review.mjs
```

Open all four generated PNGs and check that headers, values and manual columns are visible without clipping or overlap. Patch only the affected widths/heights/styles, rerun, and stop when all four views are legible.

- [ ] **Step 7: Synchronize README structure and commands**

Update the repository tree to list `dual_crop_review.py`, the two versioned workbook tools, tests and final review artifacts. Document the two Python CLI commands and the Node workbook build/verify commands. Replace all old Rangitoto counts with a warning that floor-sampling results are invalid until Task 5 recomputes them. State that JSON contains the full window evidence, XLSX contains only human review rows, and full inference needs local video/checkpoint while merged JSON audit does not.

- [ ] **Step 8: Tighten ignore rules and commit Task 4**

Keep only the final JSON/CSV/XLSX allowlisted under `outputs/rangitoto-r3d18-bootstrap-review/`. Continue ignoring raw inference runs, preview PNGs, hidden build scratch and `node_modules`.

```powershell
node tools/test_rangitoto_review.mjs
.venv\Scripts\python.exe -m unittest discover -s tests -v
git diff --check
git add tools/build_rangitoto_review.mjs tools/verify_rangitoto_review.mjs tools/test_rangitoto_review.mjs README.md .gitignore
git commit -m "feat: version Rangitoto review workbook builder"
```

---

### Task 5: Re-run Rangitoto and rebuild final artifacts

**Files:**
- Replace: `outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json`
- Replace: `outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.csv`
- Replace: `outputs/rangitoto-r3d18-bootstrap-review/rangitoto_action_review.xlsx`
- Modify: `README.md`

**Interfaces:**
- Local raw output root: `outputs/rangitoto-r3d18-bootstrap-center-nearest-v1/`.
- Final tracked output root: `outputs/rangitoto-r3d18-bootstrap-review/`.
- Both raw inputs must be inference JSON v2 using `center-nearest-frame-v1`.

- [ ] **Step 1: Resolve and hash the exact local video and checkpoint**

Read the Rangitoto intake metadata and locate the single full-match video. Confirm OpenCV reports 1920x1080, 30 FPS and 197,384 frames. Compute video and checkpoint SHA-256 once and record the values for comparison with both inference outputs. Abort if either file changes between far and near scans.

```powershell
.venv\Scripts\spiketrace.exe inspect-video "data\YTDown.com_YouTube_Rangitoto-vs-Taka-National-Final-Sets-1-_Media_k3PdQgm2jVs_001_1080p.mp4"
Get-FileHash -Algorithm SHA256 -LiteralPath "data\YTDown.com_YouTube_Rangitoto-vs-Taka-National-Final-Sets-1-_Media_k3PdQgm2jVs_001_1080p.mp4"
Get-FileHash -Algorithm SHA256 -LiteralPath "runs\rangitoto-r3d18-bootstrap\best.pt"
```

- [ ] **Step 2: Run far inference on the GPU**

```powershell
.venv\Scripts\spiketrace.exe infer `
  "data\YTDown.com_YouTube_Rangitoto-vs-Taka-National-Final-Sets-1-_Media_k3PdQgm2jVs_001_1080p.mp4" `
  runs\rangitoto-r3d18-bootstrap\best.pt `
  outputs\rangitoto-r3d18-bootstrap-center-nearest-v1\far `
  --stride-seconds 0.4 `
  --confidence-threshold 0.2 `
  --batch-size 8 `
  --device cuda `
  --crop 0,0,1920,645
```

Expected: 16,448 windows, JSON format version 2, sampling contract `center-nearest-frame-v1`, and no decoding interruption.

- [ ] **Step 3: Run near inference on the GPU**

```powershell
.venv\Scripts\spiketrace.exe infer `
  "data\YTDown.com_YouTube_Rangitoto-vs-Taka-National-Final-Sets-1-_Media_k3PdQgm2jVs_001_1080p.mp4" `
  runs\rangitoto-r3d18-bootstrap\best.pt `
  outputs\rangitoto-r3d18-bootstrap-center-nearest-v1\near `
  --stride-seconds 0.4 `
  --confidence-threshold 0.2 `
  --batch-size 8 `
  --device cuda `
  --crop 0,255,1920,1080
```

Expected: 16,448 windows and the same video/checkpoint/sampling identities as far; only crop and permitted runtime device fields may differ.

- [ ] **Step 4: Build and independently verify JSON/CSV**

```powershell
.venv\Scripts\spiketrace.exe build-dual-crop-review `
  outputs\rangitoto-r3d18-bootstrap-center-nearest-v1\far\events.json `
  outputs\rangitoto-r3d18-bootstrap-center-nearest-v1\near\events.json `
  outputs\rangitoto-r3d18-bootstrap-review `
  --repo-root .

.venv\Scripts\spiketrace.exe verify-dual-crop-review `
  outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.json `
  --csv outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.csv
```

Expected: 32,896 unique embedded input windows, zero cross-event member duplicates, both source-event sets complete, and CSV rows exactly equal candidate count.

- [ ] **Step 5: Build and verify the final workbook**

```powershell
node tools/build_rangitoto_review.mjs `
  outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json `
  outputs/rangitoto-r3d18-bootstrap-review/rangitoto_action_review.xlsx `
  outputs/.rangitoto-review-build/previews

node tools/verify_rangitoto_review.mjs `
  outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json `
  outputs/rangitoto-r3d18-bootstrap-review/rangitoto_action_review.xlsx
```

Expected: four sheets, all manual cells blank, no formula errors and all four previews readable.

- [ ] **Step 6: Update README with measured nearest-v1 results**

Replace the invalid old counts with the verifier's exact event, candidate, duplicate, conflict and action-distribution counts. Record both input window counts, hashes, sampling contract and the commands above. Keep the statement that this bootstrap checkpoint is not a generalization score.

- [ ] **Step 7: Commit final artifacts and measured documentation**

```powershell
git add outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.csv outputs/rangitoto-r3d18-bootstrap-review/rangitoto_action_review.xlsx README.md
git commit -m "data: rebuild Rangitoto nearest-frame review set"
```

---

### Task 6: Final verification, independent review and publication

**Files:**
- Verify: all tracked source, tests, docs and three final artifacts

**Interfaces:**
- Final branch: `codex/rangitoto-review`.
- Handoff point: user begins manual action review only after every gate passes.

- [ ] **Step 1: Run the complete Python quality gate**

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\ruff.exe check .
.venv\Scripts\python.exe -m compileall -q src tests tools
git diff --check
```

Expected: zero test failures, zero Ruff errors, compile exit code 0 and no whitespace errors.

- [ ] **Step 2: Run artifact invariants again from final files**

```powershell
.venv\Scripts\spiketrace.exe verify-dual-crop-review outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.json --csv outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.csv
node tools/verify_rangitoto_review.mjs outputs/rangitoto-r3d18-bootstrap-review/merged_candidates.json outputs/rangitoto-r3d18-bootstrap-review/rangitoto_action_review.xlsx
```

Expected: complete 32,896-window audit, zero duplicate member assignments, JSON/CSV parity, four workbook sheets, blank manual inputs and zero formula errors.

- [ ] **Step 3: Perform final visual pass**

Render all four workbook sheets at readable scale and inspect every image. Confirm no clipped headers, overlapping text, unreadable validation cells, blank/broken sections or hidden manual columns.

- [ ] **Step 4: Dispatch a whole-branch independent review**

Generate a review package from the branch merge base through `HEAD`. The reviewer must check the design document, this plan, source diff, tests, README contracts and final artifact invariants. Any Critical or Important finding receives one consolidated fix round and one scoped re-review before publication.

- [ ] **Step 5: Confirm git scope and commit any review fixes**

```powershell
git status --short
git log --oneline --decorate -8
```

Expected: no unintended video/checkpoint/raw-run/build-scratch files are staged or tracked; `pyproject.toml` still names `jmmyxa`.

- [ ] **Step 6: Push the branch and create a draft PR when authentication is available**

```powershell
git push -u origin codex/rangitoto-review
gh pr create --draft --title "Rebuild Rangitoto review trust chain" --body "Unifies CFR frame sampling, preserves event window provenance, and rebuilds the auditable Rangitoto review artifacts."
```

If `gh auth status` remains unauthenticated, push if Git credentials permit and report the exact authentication blocker without changing repository state.

- [ ] **Step 7: Hand off the workbook for human review**

Give the user the single final XLSX path and the newly measured candidate count. State that only the five yellow columns need input and that `background` rejects a false positive. Stop here for human review; do not begin player-number attribution or frontend work.
