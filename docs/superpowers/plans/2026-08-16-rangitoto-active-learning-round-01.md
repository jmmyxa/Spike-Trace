# Rangitoto Active Learning Round 01 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Rangitoto 的 2,942 条审计候选确定性压缩为 40 个可播放短片，生成无需审核状态列的复核工作簿，并把填写结果自动转换为新的累计训练清单。

**Architecture:** Python 负责验证双裁剪来源、选择短片、生成代理视频以及应用人工结果；Node.js 和 `@oai/artifact-tool` 只负责可移植 XLSX 的构建、验证和结果提取。所有持久 JSON/CSV 都保存来源 SHA-256 且拒绝覆盖，代理 MP4 和预览图保持为可重建的本地输出。

**Tech Stack:** Python 3.10+、OpenCV、标准库 `csv/json/hashlib`、`unittest`、Node.js、`@oai/artifact-tool`、Ruff。

## Global Constraints

- 首轮必须恰好选择 40 个唯一短片，不能把 2,942 条候选重新变成人工待办。
- 短片优先 10 至 20 秒，默认 15 秒；合法范围固定为 5 至 30 秒。
- 五桶目标配额固定为 `20/8/4/4/4`：冲突或少数类、高置信度尾部、双视角同意、确定性随机候选、双视角背景。
- 首桶必须先覆盖当前可用的全部 15 个 `receive`、`block`、`dig` 候选。
- 最终短片必须覆盖至少 10 个等长比赛时间层，并覆盖可用的 `far` 和 `near` 场景。
- 人工填写片段内相对整秒；程序使用 `clip_start_seconds + relative_seconds` 换算原视频绝对时间，并把输入精度记录为 `1` 秒。
- `receive` 只表示接对方发球；`dig` 只表示对方进攻后的防守起球。
- `far` 和 `near` 表示画面裁剪位置，不代表固定球队。
- 人工动作非空即表示该短片已复核；复核工作簿不得增加审核状态列、复选框或完成列。
- 无我方动作时只填一条 `background`，两个时间格必须留空；`background` 不得与正动作混填。
- 每条人工行都必须填写我方所在裁剪 `far` 或 `near`，包括无动作的 `background` 行。
- 代理片默认 `15 FPS`、最大宽度 `960`、`mp4v`、无音频；代理片只用于复核，绝不作为训练输入。
- 每轮自动背景窗口默认在人工动作前后保留 `0.5` 秒保护区，数量不超过本轮新增正动作数。
- Rangitoto 一旦加入 `train`，就不得出现在 `val` 或 `test`。
- 选择 JSON 和结果 JSON 纳入版本控制；代理 MP4、XLSX 预览和训练运行目录保持忽略。
- 不开始号码归属、前端、账户、数据库或球员统计。
- 每次模块或目录结构变化必须同步 `README.md`，且 `pyproject.toml` 作者名保持 `jmmyxa`。
- 测试使用 `unittest`，不要引入 pytest。

---

### Task 1: Selection v1 trust chain and validation boundary

**Files:**
- Create: `src/spiketrace/active_learning_selection.py`
- Modify: `src/spiketrace/errors.py`
- Create: `tests/test_active_learning_selection.py`

**Interfaces:**
- Produces: `ActiveLearningError(SpikeTraceError)`.
- Produces: `validate_merged_review_source(merged_json_path, *, repo_root, require_video=True) -> dict[str, object]`.
- Produces: `write_review_selection(payload, output_path, *, repo_root) -> dict[str, object]`.
- Produces: `load_review_selection(selection_path, *, repo_root, require_video=True) -> dict[str, object]`.
- Consumes: `verify_dual_crop_review()` from `src/spiketrace/dual_crop_review.py`.

- [ ] **Step 1: Write failing selection-contract tests**

Create a compact merged format-2 fixture in the test helper, then add these literal checks:

```python
from spiketrace.active_learning_selection import (
    load_review_selection,
    validate_merged_review_source,
    write_review_selection,
)
from spiketrace.errors import ActiveLearningError


class SelectionContractTests(unittest.TestCase):
    def test_writes_source_and_video_hashes_without_a_generated_timestamp(self):
        source = validate_merged_review_source(
            self.merged_json,
            repo_root=self.root,
        )
        payload = make_valid_selection_payload(source, clip_count=40)
        write_review_selection(
            payload,
            self.output_json,
            repo_root=self.root,
        )
        self.assertEqual(payload["format_version"], 1)
        self.assertEqual(
            payload["selection_algorithm_version"],
            "active-learning-selection-v1",
        )
        self.assertEqual(payload["source"]["merged_json_sha256"], sha256_file(self.merged_json))
        self.assertEqual(
            payload["source"]["checkpoint_sha256"],
            source["merged"]["input_runs"]["far"]["settings"]["checkpoint_sha256"],
        )
        self.assertIs(source["verification"]["checkpoint_file_checked"], True)
        self.assertNotIn("checkpoint_file_checked", payload["source"])
        self.assertEqual(payload["video"]["sha256"], sha256_file(self.video_path))
        self.assertNotIn("generated_at", payload)
        self.assertEqual(load_review_selection(self.output_json, repo_root=self.root), payload)

    def test_rejects_an_existing_output_without_changing_its_bytes(self):
        self.output_json.write_bytes(b"keep")
        with self.assertRaisesRegex(ActiveLearningError, "already exists"):
            write_review_selection(
                self.selection_payload,
                self.output_json,
                repo_root=self.root,
            )
        self.assertEqual(self.output_json.read_bytes(), b"keep")
```

Define `make_valid_selection_payload` in the test module with 40 literal, ordered non-overlapping clips. The fixture helper may patch `verify_dual_crop_review` only for small unit fixtures; a later integration test uses the real verifier.

- [ ] **Step 2: Run the contract tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_active_learning_selection.SelectionContractTests -v
```

Expected: import failure because `active_learning_selection.py` and `ActiveLearningError` do not exist.

- [ ] **Step 3: Define strict JSON helpers and the v1 schema**

Add the error class and implement duplicate-key rejection, finite-number checks, stable SHA-256, repository-relative POSIX paths and atomic JSON output. Import `os` and `tempfile`; use these exact root fields and reject extras on load:

```python
SELECTION_ROOT_FIELDS = (
    "format_version",
    "selection_algorithm_version",
    "batch_id",
    "round_id",
    "round_number",
    "source",
    "video",
    "settings",
    "previous_selections",
    "quota_summary",
    "coverage",
    "clips",
)


class ActiveLearningError(SpikeTraceError):
    """Raised when an active-learning artifact is invalid or unsafe to apply."""


def _write_new_json(path: Path, payload: object) -> None:
    if path.exists():
        raise ActiveLearningError(f"Output already exists: {path}")
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ActiveLearningError(f"Output already exists: {path}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
```

The hard-link publication is the commit point: it must fail rather than replace a file created after the initial `exists()` check. Add a losing-race test whose patched `os.link` first writes competitor bytes at the final path and then calls the real link, plus an `os.fsync` failure test. Both tests must assert the final winner bytes are preserved (or no final exists for sync failure) and that no `.<name>.*.tmp` sibling remains.

`load_review_selection` must recompute the selection file SHA where needed, resolve and hash the merged JSON and source video, call `verify_dual_crop_review`, verify all 40 clips in order, and reject any path escaping `repo_root`.

`validate_merged_review_source` must build these nested objects with exact field order so later tasks do not infer paths or crops:

```python
source = {
    "merged_json": normalized_merged_path,
    "merged_json_sha256": sha256_file(merged_path),
    "checkpoint": merged["input_runs"]["far"]["settings"]["checkpoint"],
    "checkpoint_sha256": merged["input_runs"]["far"]["settings"]["checkpoint_sha256"],
    "inference_runs": {
        "far": merged["settings"]["input_runs"]["far"],
        "near": merged["settings"]["input_runs"]["near"],
    },
    "format_version": merged["format_version"],
    "merge_format_version": merged["merge_format_version"],
    "model_version": merged["model_version"],
}
video = {
    "video_id": Path(merged["video"]["path"]).stem,
    "path": merged["video"]["path"],
    "sha256": merged["input_runs"]["far"]["settings"]["video_sha256"],
    "fps": merged["video"]["fps"],
    "frame_count": merged["video"]["frame_count"],
    "width": merged["video"]["width"],
    "height": merged["video"]["height"],
    "duration_seconds": merged["video"]["duration_seconds"],
    "crops": {
        "far": merged["input_runs"]["far"]["settings"]["crop"],
        "near": merged["input_runs"]["near"]["settings"]["crop"],
    },
}
```

Require the far/near checkpoint path and SHA to match before building `source`. The two `inference_runs` entries pin each source file SHA and normalized payload SHA from the independently verified merged artifact. When the checkpoint file is locally available, recompute its SHA; when it is absent on another device, still compare all pinned fields with the committed merged JSON. Return `{"merged": merged, "source": source, "video": video, "verification": {"checkpoint_file_checked": bool}}`, but pass only `source` and `video` into the persisted selection. The machine-dependent verification flag is command-return evidence only and must never enter the deterministic selection schema. `write_review_selection` validates the complete selection-v1 payload through the same loader rules before writing, so a malformed artifact can never be persisted by the public API.

- [ ] **Step 4: Run contract tests and verify GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_active_learning_selection.SelectionContractTests -v
```

Expected: all contract cases pass, including merged/video hash tampering, duplicate JSON keys, non-finite values, path escape, invalid format, and output collision.

- [ ] **Step 5: Commit the trust-chain boundary**

```powershell
git add src/spiketrace/active_learning_selection.py src/spiketrace/errors.py tests/test_active_learning_selection.py
git commit -m "feat: define active learning selection contract"
```

---

### Task 2: Deterministic five-bucket selection

**Files:**
- Modify: `src/spiketrace/active_learning_selection.py`
- Modify: `src/spiketrace/cli.py`
- Modify: `tests/test_active_learning_selection.py`

**Interfaces:**
- Consumes: validated merged format-2 payload and any prior selection-v1 artifacts.
- Produces: `select_review_batch(merged_json_path, output_path, *, repo_root, round_number=1, seed=42, preferred_clip_seconds=15.0, min_clip_seconds=5.0, max_clip_seconds=30.0, min_anchor_gap_seconds=5.0, time_strata=10, previous_selection_paths=()) -> dict[str, object]`.
- Produces: 40 time-ordered `clips` with IDs `round-01-clip-001` through `round-01-clip-040`.
- Produces: deterministic `quota_summary` and `coverage` suitable for byte-identical reruns.
- CLI: `spiketrace select-review-batch MERGED_JSON OUTPUT_JSON --repo-root ROOT [options]`.

- [ ] **Step 1: Write failing exact-quota and minority-coverage tests**

Build a synthetic 1200-second fixture with enough candidates in all five buckets. Give its 15 minority canonical events the literal IDs `minority-{receive|block|dig}-01` through `-05`, and place two receive/dig pairs in shared conflict groups so the test detects hint loss during coalescing. Assert:

```python
class FiveBucketSelectionTests(unittest.TestCase):
    def test_selects_exact_quotas_and_all_available_minority_candidates_first(self):
        payload = self.select_fixture(seed=42)
        counts = Counter(clip["selection_bucket"] for clip in payload["clips"])
        self.assertEqual(
            counts,
            {
                "conflict_or_minority": 20,
                "high_confidence_tail": 8,
                "dual_view_agreement": 4,
                "random_candidate_control": 4,
                "dual_background_control": 4,
            },
        )
        expected_minority_ids = {
            f"minority-{action}-{index:02d}"
            for action in ("receive", "block", "dig")
            for index in range(1, 6)
        }
        covered_minority_ids = {
            hint["canonical_event_id"]
            for clip in payload["clips"]
            for hint in clip["candidate_hints"]
            if hint["canonical_event_id"] in expected_minority_ids
        }
        self.assertEqual(covered_minority_ids, expected_minority_ids)
        self.assertEqual(len(payload["clips"]), 40)

    def test_same_seed_produces_byte_identical_json(self):
        first = self.select_fixture(seed=42, filename="first.json")
        second = self.select_fixture(seed=42, filename="second.json")
        self.assertEqual(first, second)
        self.assertEqual(self.first_path.read_bytes(), self.second_path.read_bytes())
```

- [ ] **Step 2: Run the bucket tests and verify RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_active_learning_selection.FiveBucketSelectionTests -v
```

Expected: failures because `select_review_batch` does not yet populate clips or quotas.

- [ ] **Step 3: Implement stable candidate ranking and clip fitting**

Use integer milliseconds for all overlap decisions. Do not use `random.Random`; define stable pseudo-random ranking as:

```python
def _stable_rank(seed: int, namespace: str, stable_id: str) -> str:
    value = f"{seed}\0{namespace}\0{stable_id}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _fit_clip_bounds(
    anchor_start_ms: int,
    anchor_end_ms: int,
    *,
    video_duration_ms: int,
    preferred_ms: int,
    min_ms: int,
    max_ms: int,
) -> tuple[int, int]:
    anchor_duration = anchor_end_ms - anchor_start_ms
    target = min(max_ms, max(min_ms, preferred_ms, anchor_duration))
    center_twice = anchor_start_ms + anchor_end_ms
    start = max(0, (center_twice - target) // 2)
    end = min(video_duration_ms, start + target)
    start = max(0, end - target)
    if start > anchor_start_ms or end < anchor_end_ms:
        raise ActiveLearningError("The anchor cannot fit in a legal review clip.")
    return start, end
```

Assign a stratum before any bucket ranking with the exact midpoint formula `min(time_strata - 1, midpoint_ms * time_strata // video_duration_ms)`. Require `time_strata >= 10` for the 40-clip selector.

For the required minority candidates, first compute the exact set of all canonical event IDs whose action is `receive`, `block` or `dig`. Coalesce required anchors when their proposed clips overlap or they share a duplicate/conflict group, fit one clip around the union, and retain every member as a separate `candidate_hints` entry with the union of source/group reservations. If a required cluster cannot fit within `max_clip_seconds`, raise `ActiveLearningError` naming every uncovered canonical ID; never resolve that case by silently keeping only the higher-priority member. Reserve source event IDs, duplicate groups and conflict groups only after the complete cluster is accepted. For non-required anchors, merge overlapping proposed clips only when the merged range remains at most 30 seconds; otherwise keep the higher-priority anchor and skip the later one.

- [ ] **Step 4: Implement the five exclusive buckets and quota transfer**

Use this exact bucket order and minority priority:

```python
ROUND_ONE_QUOTAS = (
    ("conflict_or_minority", 20),
    ("high_confidence_tail", 8),
    ("dual_view_agreement", 4),
    ("random_candidate_control", 4),
    ("dual_background_control", 4),
)
MINORITY_ACTIONS = ("receive", "block", "dig")
```

Within the first bucket, sort minority candidates by action order above, then start time and event ID; after all legal minority candidates, use distinct conflict groups ordered by start time. In the second bucket, take `set/attack/serve` at confidence `>= 0.4` first, then descending confidence. In the third bucket require `duplicate_group_id` and no `conflict_group_id`.

Before filling any later bucket, recompute the minority IDs present across all selected `candidate_hints` and require exact equality with the source minority-ID set. Multiple required events coalesced into one clip still count as multiple covered candidates but only one of the 20 first-bucket clips; fill the remaining first-bucket clip quota from distinct conflict groups.

The fourth and fifth buckets must be genuinely time-stratified rather than globally shuffled. Compute each anchor's stratum from its midpoint, sort each stratum by `_stable_rank(seed, bucket, stable_id)`, then emit one item from every nonempty stratum in numeric order before taking a second item from any stratum:

```python
def _interleave_time_strata(
    items,
    *,
    time_strata,
    covered_strata,
    rank_key,
    stratum_rank_key,
):
    queues = {index: [] for index in range(time_strata)}
    for item in items:
        queues[item["time_stratum"]].append(item)
    for queue in queues.values():
        queue.sort(key=rank_key)
    stratum_order = sorted(
        range(time_strata),
        key=lambda index: (index in covered_strata, stratum_rank_key(index)),
    )
    ordered = []
    while any(queues.values()):
        for index in stratum_order:
            if queues[index]:
                ordered.append(queues[index].pop(0))
    return ordered
```

The fourth bucket applies this ordering to all remaining canonical candidates. The fifth bucket first derives continuous intervals where paired far/near windows both have top-1 `background`, requires at least 5 seconds, then applies the same ordering. After all quota transfers, reject a result with fewer than 10 represented strata or, when both crop scenes are available, without both `far` and `near` evidence; never silently relax either coverage rule.

If a bucket is short, pass its deficit to the next bucket and record:

```json
{
  "bucket": "dual_view_agreement",
  "planned": 4,
  "selected": 3,
  "transferred_out": 1,
  "transferred_to": "random_candidate_control",
  "reason": "eligible_pool_exhausted"
}
```

Fail instead of silently returning fewer than 40 clips.

- [ ] **Step 5: Add time, uniqueness and prior-round exclusion tests**

Add these exact invariants:

```python
def test_enforces_time_and_source_uniqueness(self):
    payload = self.select_fixture(seed=42)
    clips = payload["clips"]
    self.assertTrue(all(5.0 <= clip["duration_seconds"] <= 30.0 for clip in clips))
    self.assertEqual(len({clip["clip_id"] for clip in clips}), 40)
    self.assertGreaterEqual(len({clip["time_stratum"] for clip in clips}), 10)
    self.assertFalse(any(
        left["start_seconds"] < right["end_seconds"]
        and right["start_seconds"] < left["end_seconds"]
        for index, left in enumerate(clips)
        for right in clips[index + 1:]
    ))
    source_ids = [
        source_id for clip in clips for source_id in clip["reserved_source_event_ids"]
    ]
    self.assertEqual(len(source_ids), len(set(source_ids)))

def test_excludes_prior_clip_time_even_when_new_event_ids_differ(self):
    previous = self.select_fixture(filename="round-01.json")
    current = self.select_fixture(
        filename="round-02.json",
        round_number=2,
        previous_selection_paths=[self.round_one_path],
        rewrite_event_ids=True,
    )
    self.assertTrue(all(
        not intervals_overlap(old, new)
        for old in previous["clips"]
        for new in current["clips"]
    ))
```

Also cover anchor gaps, quota transfer, fewer than 40 legal clips, fewer than 10 strata, both available crop scenes not being represented, and the legitimate case where only one crop scene is available.

- [ ] **Step 6: Serialize the exact clip surface**

Each clip must contain these fields in this order:

```python
clip = {
    "clip_id": clip_id,
    "ordinal": ordinal,
    "start_seconds": start_ms / 1000,
    "end_seconds": end_ms / 1000,
    "start_time": format_timecode(start_ms / 1000),
    "end_time": format_timecode(end_ms / 1000),
    "duration_seconds": (end_ms - start_ms) / 1000,
    "time_stratum": time_stratum,
    "selection_bucket": bucket,
    "selection_reasons": reasons,
    "proxy_filename": f"clips/{clip_id}.mp4",
    "anchor": anchor,
    "candidate_hints": hints,
    "reserved_source_event_ids": source_ids,
    "reserved_duplicate_group_ids": duplicate_ids,
    "reserved_conflict_group_ids": conflict_ids,
}
```

Each hint stores canonical event ID, absolute/relative bounds, action, confidence, observed sides, duplicate/conflict group IDs and source candidate IDs. Assign final clip IDs only after chronological sorting.

Build the root payload with `round_id=f"round-{round_number:02d}"`, `batch_id=f"{video['video_id']}-{round_id}"`, and these settings:

```python
settings = {
    "seed": seed,
    "preferred_clip_seconds": preferred_clip_seconds,
    "min_clip_seconds": min_clip_seconds,
    "max_clip_seconds": max_clip_seconds,
    "min_anchor_gap_seconds": min_anchor_gap_seconds,
    "time_strata": time_strata,
    "planned_quotas": {name: count for name, count in ROUND_ONE_QUOTAS},
}
```

Store each previous selection as repository-relative path, SHA-256, batch ID and round ID, then call `write_review_selection` from Task 1.

- [ ] **Step 7: Add and test the selection CLI**

Parse this exact surface and dispatch every value to `select_review_batch`:

```text
select-review-batch MERGED_JSON OUTPUT_JSON --repo-root ROOT
  --round-number 1 --seed 42
  --preferred-clip-seconds 15 --min-clip-seconds 5
  --max-clip-seconds 30 --min-anchor-gap-seconds 5
  --time-strata 10 --previous-selection PATH  # repeatable
```

Add a command test that patches `spiketrace.active_learning_selection.select_review_batch`, parses two `--previous-selection` values and asserts they arrive in order. Reject nonpositive duration/gap values and `time_strata < 10` in argparse before dispatch.

- [ ] **Step 8: Run selection tests and full regression suite**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_active_learning_selection -v
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: all tests pass; changing the seed only changes random/time-stratified tie-breaks, never schema or quotas.

- [ ] **Step 9: Commit deterministic selection**

```powershell
git add src/spiketrace/active_learning_selection.py src/spiketrace/cli.py tests/test_active_learning_selection.py
git commit -m "feat: select forty active review clips"
```

---

### Task 3: Silent proxy video writer

**Files:**
- Modify: `src/spiketrace/video.py`
- Modify: `tests/test_video.py`

**Interfaces:**
- Produces: `write_proxy_video(video_path, output_path, start_seconds, end_seconds, *, output_fps=15.0, max_width=960, codec="mp4v") -> VideoMetadata`.
- Preserves: existing sampling and sequential inference behavior.

- [ ] **Step 1: Write failing real-video proxy tests**

Use the existing OpenCV fixture style and assert:

```python
class ProxyVideoTests(unittest.TestCase):
    def test_writes_reopenable_silent_mp4_with_stable_geometry(self):
        metadata = write_proxy_video(
            self.source,
            self.destination,
            0.2,
            1.2,
            output_fps=10.0,
            max_width=6,
        )
        self.assertEqual(metadata.frame_count, 10)
        self.assertAlmostEqual(metadata.fps, 10.0, places=2)
        self.assertEqual((metadata.width, metadata.height), (6, 4))
        self.assertAlmostEqual(metadata.duration_seconds, 1.0, delta=0.11)
        self.assertTrue(self.destination.is_file())

    def test_refuses_bounds_errors_and_an_existing_destination(self):
        self.destination.write_bytes(b"keep")
        for start, end in ((-1, 1), (1, 1), (0, 99), (float("nan"), 1)):
            with self.subTest((start, end)), self.assertRaises(VideoError):
                write_proxy_video(self.source, self.destination, start, end)
        self.assertEqual(self.destination.read_bytes(), b"keep")
```

- [ ] **Step 2: Run proxy tests and verify RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_video.ProxyVideoTests -v
```

Expected: import failure because `write_proxy_video` does not exist.

- [ ] **Step 3: Implement sequential decode, resize and atomic rename**

Reject non-finite or nonpositive `output_fps`, `max_width < 2`, a codec other than four ASCII characters, and invalid clip bounds before creating a temporary file. Use `clip_sample_frame_indices` for `round(duration * output_fps)` output frames, seek once to the first source frame, decode forward, and reuse decoded frames for duplicate sample indices. Preserve aspect ratio and force even dimensions:

```python
target_width = min(source.width, max_width)
target_height = max(2, int(round(source.height * target_width / source.width)))
target_width -= target_width % 2
target_height -= target_height % 2
frame_total = max(1, floor((end_seconds - start_seconds) * output_fps + 0.5))
indices = clip_sample_frame_indices(
    start_seconds,
    end_seconds,
    num_frames=frame_total,
    fps=source.fps,
    frame_count=source.frame_count,
)
```

Write to a temporary sibling `.mp4`, release the writer, reopen it with `inspect_video`, validate frame count/FPS/geometry/duration tolerances, then rename. Delete the temporary file on every failure. Do not add FFmpeg or audio dependencies.

- [ ] **Step 4: Verify frame content and error cleanup**

Add first/last frame mean-color assertions with codec tolerance, invalid codec, write failure, missing source and no-leftover-temp cases. Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_video -v
```

Expected: all video tests pass.

- [ ] **Step 5: Commit proxy generation**

```powershell
git add src/spiketrace/video.py tests/test_video.py
git commit -m "feat: write active review proxy clips"
```

---

### Task 4: Proxy batch manifest and Python CLI

**Files:**
- Create: `src/spiketrace/review_batch.py`
- Modify: `src/spiketrace/cli.py`
- Create: `tests/test_review_batch.py`

**Interfaces:**
- Produces: `build_review_proxies(selection_path, output_dir, *, repo_root, output_fps=15.0, max_width=960, codec="mp4v") -> dict[str, object]`.
- CLI: `spiketrace build-review-clips SELECTION_JSON OUTPUT_DIR --repo-root ROOT --proxy-fps 15 --max-width 960`.
- Output: `OUTPUT_DIR/clips/*.mp4` and `OUTPUT_DIR/proxy-manifest.json`.

- [ ] **Step 1: Write failing ordered-manifest and CLI tests**

```python
class ReviewProxyBatchTests(unittest.TestCase):
    def test_builds_exact_ordered_proxy_manifest(self):
        result = build_review_proxies(
            self.selection,
            self.output_dir,
            repo_root=self.root,
            output_fps=15.0,
            max_width=960,
        )
        manifest = json.loads((self.output_dir / "proxy-manifest.json").read_text())
        self.assertEqual(manifest["format_version"], 1)
        self.assertEqual(manifest["selection_sha256"], sha256_file(self.selection))
        self.assertEqual(
            [item["clip_id"] for item in manifest["clips"]],
            [item["clip_id"] for item in self.selection_payload["clips"]],
        )
        self.assertTrue(all(len(item["sha256"]) == 64 for item in manifest["clips"]))
        self.assertEqual(result["clip_count"], 40)

class ReviewProxyCommandTests(unittest.TestCase):
    def test_dispatches_all_proxy_settings(self):
        args = build_parser().parse_args([
            "build-review-clips", "selection.json", "batch", "--repo-root", ".",
            "--proxy-fps", "12", "--max-width", "800",
        ])
        with mock.patch("spiketrace.review_batch.build_review_proxies", return_value={"ok": True}) as build:
            self.assertEqual(run_command(args), {"ok": True})
        self.assertEqual(build.call_args.kwargs["output_fps"], 12.0)
        self.assertEqual(build.call_args.kwargs["max_width"], 800)
```

- [ ] **Step 2: Run batch tests and verify RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_review_batch -v
```

Expected: import failure because `review_batch.py` and the command do not exist.

- [ ] **Step 3: Implement all-or-nothing batch generation**

Refuse an existing final directory. Build into a sibling directory named `.<output-name>.tmp-<pid>`, revalidate the selection/video hashes before every build, and write this exact manifest surface:

```python
manifest = {
    "format_version": 1,
    "batch_id": selection["batch_id"],
    "round_id": selection["round_id"],
    "selection": normalized_selection_path,
    "selection_sha256": sha256_file(selection_path),
    "video": {
        "path": selection["video"]["path"],
        "sha256": selection["video"]["sha256"],
    },
    "settings": {
        "codec": codec,
        "fps": output_fps,
        "max_width": max_width,
        "audio": False,
    },
    "clips": clip_metadata,
}
```

Each clip entry stores ID, ordinal, `clips/<id>.mp4`, SHA-256, source start/end and actual frame/FPS/width/height/duration. Validate all 40 proxy files, write the manifest atomically inside staging, then rename staging to the final directory.

- [ ] **Step 4: Implement CLI parsing and failure tests**

Add positive parsing for FPS/width, dispatch to `build_review_proxies`, and test missing selection, tampered video, one failed proxy write, existing output directory and cleanup of staging. Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_review_batch -v
```

Expected: all proxy batch and CLI tests pass.

- [ ] **Step 5: Commit the proxy batch command**

```powershell
git add src/spiketrace/review_batch.py src/spiketrace/cli.py tests/test_review_batch.py
git commit -m "feat: build active review clip batches"
```

---

### Task 5: Four-sheet review workbook builder and verifier

**Files:**
- Create: `tools/build_active_review_batch.mjs`
- Create: `tools/verify_active_review_batch.mjs`
- Create: `tools/test_active_review_batch.mjs`

**Interfaces:**
- Build tool: `node tools/build_active_review_batch.mjs SELECTION_JSON OUTPUT_DIR PREVIEW_DIR`.
- Verify tool: `node tools/verify_active_review_batch.mjs SELECTION_JSON OUTPUT_DIR/review.xlsx`.
- Consumes: selection v1 and `proxy-manifest.json` from Task 4.
- Produces: `review.xlsx` with sheets `短片清单`, `人工动作`, `候选提示`, `标签说明`.

- [ ] **Step 1: Write the failing executable workbook contract**

Create a synthetic production-shaped 40-clip selection and low-resolution source-video fixture through the Python selector. Assert exact sheet order, headers, 12 slots per clip and relative hyperlinks; do not add a production option that weakens the fixed count:

```javascript
assert.deepEqual(sheetNames(workbook), ["短片清单", "人工动作", "候选提示", "标签说明"]);
assert.deepEqual(actionSheet.getRange("A3:I3").values[0], [
  "短片ID", "动作序号", "播放短片", "片段长度(秒)", "人工确认动作",
  "片段内开始秒", "片段内结束秒", "人工侧别", "备注",
]);
assert.equal(actionSheet.getUsedRange().rowCount, 3 + 40 * 12);
assert.equal(
  actionSheet.getRange("C4").formulas[0][0],
  '=HYPERLINK("clips/round-01-clip-001.mp4","播放")',
);
assert.deepEqual(validationValues(actionSheet.getRange("E4:E483")), ACTIONS);
assertWholeNumberValidation(actionSheet.getRange("F4:G483"), {minimum: 0});
assert.deepEqual(validationValues(actionSheet.getRange("H4:H483")), ["far", "near"]);
assert.ok(actionSheet.getRange("E4:I483").values.flat().every((value) => value == null || value === ""));
```

Also assert no header matches `/审核|状态|确认栏|checkbox|review status/i`.

- [ ] **Step 2: Run the workbook test and verify RED**

Use the Node executable supplied by `codex_app__load_workspace_dependencies`, then run:

```powershell
node tools\test_active_review_batch.mjs
```

Expected: module-not-found failure because the build and verifier tools do not exist. Do not hardcode a user-specific Node or artifact-tool path in committed files.

- [ ] **Step 3: Implement source/proxy verification and workbook projections**

Export these constants from the verifier:

```javascript
export const SHEET_NAMES = ["短片清单", "人工动作", "候选提示", "标签说明"];
export const ACTIONS = ["background", "serve", "receive", "set", "attack", "block", "dig"];
export const SIDES = ["far", "near"];
export const ACTION_SLOTS_PER_CLIP = 12;
```

Before creating a workbook, recompute the selection hash, verify the proxy-manifest selection/video hashes, and verify every proxy file hash. The read-only sheets use these exact headers:

```javascript
export const CLIP_HEADERS = [
  "序号", "短片ID", "播放短片", "代理文件", "片段长度(秒)", "原视频开始",
  "原视频结束", "时间分层", "选择桶", "选择原因", "候选提示数",
];
export const HINT_HEADERS = [
  "短片ID", "候选ID", "相对开始秒", "相对结束秒", "预测动作", "置信度",
  "观察裁剪", "重复组", "冲突组", "来源候选ID",
];
```

The `标签说明` sheet must state the receive/dig boundary, integer relative-seconds rule, background-alone rule, that both time cells must remain blank for `background`, that `background` still requires the current `far|near` team crop, far/near meaning, lack of status checkbox, the 12-slot overflow safeguard, and the proxy files' silent/downscaled nature.

- [ ] **Step 4: Build, style, export, reimport and verify atomically**

Use `@oai/artifact-tool` through the workspace dependency runtime. Require the command to run from the repository root, verified by the presence of `pyproject.toml`. Build into a nonexistent sibling staging path and invoke `SPIKETRACE_PYTHON -m spiketrace build-review-clips ...` when that environment variable is set; otherwise resolve `.venv/Scripts/python.exe` on Windows or `.venv/bin/python` elsewhere. Pass the repository root explicitly through `--repo-root` and never hardcode a user-specific path.

Refuse both `OUTPUT_DIR` and `PREVIEW_DIR` before staging. After the Python command has atomically created the staging directory and all proxies, create and style all four sheets there, render every sheet into a temporary preview sibling, scan for formula errors, export to `review.xlsx`, reimport it, and run the verifier again. Only then rename staging to `OUTPUT_DIR`, followed by the preview sibling to `PREVIEW_DIR`. If the second rename fails, remove the just-created `OUTPUT_DIR` before rethrowing; this rollback is safe because both final paths were proven absent and this process created the first one. On every failure remove any remaining staging paths, and never replace either existing final path.

The verifier must compare every read-only value/formula/validation against the selection and proxy manifest while allowing the five manual columns to be either blank or valid user input:

```javascript
export async function verifyWorkbookFile(
  selectionPath,
  workbookPath,
  { allowManualValues = false } = {},
) {
  // Import, validate exact used ranges, formulas, read-only projections,
  // dropdowns, manual cell policy, sheet order, and formula-error scan.
}
```

- [ ] **Step 5: Add corruption and visual-layout tests**

The executable test must reject a changed clip ID, absolute hyperlink, missing proxy, proxy hash change, added sheet, appended row, changed hint, validation loss, prefilled manual cell during initial build and any formula error. Inject a failure on the second final-directory rename and assert neither `OUTPUT_DIR` nor `PREVIEW_DIR` remains. Render all sheets and assert the returned PNG blobs are non-empty. Inspect the rendered ranges so wrapped long video/selection reasons fit without overlapping adjacent rows.

- [ ] **Step 6: Run workbook tests and verify GREEN**

```powershell
node tools\test_active_review_batch.mjs
```

Expected: executable test exits `0`, all four sheets reimport cleanly, and all four preview renders are non-empty.

- [ ] **Step 7: Commit the workbook tools**

```powershell
git add tools/build_active_review_batch.mjs tools/verify_active_review_batch.mjs tools/test_active_review_batch.mjs
git commit -m "feat: build forty clip review workbooks"
```

---

### Task 6: Completed-workbook extraction to immutable review JSON

**Files:**
- Create: `tools/extract_active_review_results.mjs`
- Modify: `tools/verify_active_review_batch.mjs`
- Modify: `tools/test_active_review_batch.mjs`

**Interfaces:**
- CLI: `node tools/extract_active_review_results.mjs SELECTION_JSON REVIEW_XLSX OUTPUT_DRAFT_JSON`.
- Produces: review draft format v1 consumed by `apply_active_review`.
- Preserves: user-entered relative seconds and notes without mutating the workbook.

- [ ] **Step 1: Write failing extraction tests**

Fill one test clip with two positive actions, one with a single blank-time `background`, then assert:

```javascript
assert.deepEqual(draft.clips[0].actions, [
  { action: "receive", relative_start_seconds: 1, relative_end_seconds: 2,
    team_side: "far", note: "接发球" },
  { action: "set", relative_start_seconds: 3, relative_end_seconds: 4,
    team_side: "far", note: "" },
]);
assert.deepEqual(draft.clips[1].actions, [
  { action: "background", relative_start_seconds: 0,
    relative_end_seconds: draft.clips[1].source_end_seconds - draft.clips[1].source_start_seconds,
    team_side: "near", note: "" },
]);
assert.equal(draft.time_precision_seconds, 1);
assert.equal(draft.selection_sha256, sha256(selectionBytes));
assert.equal(draft.workbook.sha256, sha256(workbookBytes));
```

- [ ] **Step 2: Run extraction tests and verify RED**

```powershell
node tools\test_active_review_batch.mjs
```

Expected: failure because the extraction module does not exist.

- [ ] **Step 3: Implement exact read-only verification and row normalization**

First call `verifyWorkbookFile(selectionPath, workbookPath, {allowManualValues: true})`. Require all 40 clips in selection order and at least one populated action row per clip. An entirely blank slot is ignored; if action is blank but any of the other four manual cells is populated, reject it. Positive-action start/end values must be finite integer seconds with `0 <= start < end <= clip.duration`. Every populated row, including `background`, requires side `far` or `near`; a `background` row requires both time cells to be blank.

A lone blank-time background row is normalized to exact relative bounds `[0, clip.duration_seconds]`, meaning the entire clip was exhaustively reviewed with no team action. Reject any timed or partially timed background, positive/background mixing, duplicate action slots, invalid labels/sides, NaN/Infinity and missing/reordered clips. If all 12 action slots for one clip are populated, reject extraction with an explicit capacity error and require a deliberately expanded workbook version; this prevents a full sheet from being mistaken for an exhaustive review.

Serialize this exact top-level surface, but never stream bytes directly into the final path:

```javascript
const draft = {
  format_version: 1,
  batch_id: selection.batch_id,
  round_id: selection.round_id,
  selection: normalizeRepoPath(selectionPath),
  selection_sha256: sha256(selectionBytes),
  workbook: { path: normalizeRepoPath(workbookPath), sha256: sha256(workbookBytes) },
  video: { path: selection.video.path, sha256: selection.video.sha256 },
  time_precision_seconds: 1,
  clips,
};
```

Refuse an existing final path, write and `sync()` the complete UTF-8 JSON to a unique sibling opened with `wx`, close it, then atomically create the final name with `fs.link(tempPath, outputPath)`. The hard-link commit must fail on `EEXIST` rather than overwrite a racing writer. Remove the temporary link in `finally`; a write, sync or link failure must leave no final path created by this invocation.

- [ ] **Step 4: Add rejection and no-overwrite tests**

Cover incomplete clip, orphan manual cells, fractional manual seconds, missing side on background, all 12 slots populated, any timed or partially timed background, background mixed with an action, time outside clip, start equal to end, invalid side/action, tampered read-only cell, selection mismatch and existing output bytes. Inject temporary-write, sync and hard-link failures; assert each failure creates no final JSON, preserves an existing final file byte-for-byte and never edits the XLSX.

- [ ] **Step 5: Run executable tests and commit extraction**

```powershell
node tools\test_active_review_batch.mjs
git add tools/extract_active_review_results.mjs tools/verify_active_review_batch.mjs tools/test_active_review_batch.mjs
git commit -m "feat: extract active review workbook results"
```

Expected: test exits `0`; valid workbook extraction is deterministic except for the intentional workbook content hash.

---

### Task 7: Review application, hard negatives and cumulative manifest

**Files:**
- Create: `src/spiketrace/active_learning_review.py`
- Modify: `src/spiketrace/cli.py`
- Create: `tests/test_active_learning_review.py`

**Interfaces:**
- Produces: `apply_active_review(base_manifest_path, selection_path, review_input_path, output_manifest_path, output_results_path, *, repo_root, legacy_base_match_id, review_match_id, video_root=None, background_guard_seconds=0.5, max_background_windows=None, background_seed=None, require_files=True) -> dict[str, object]`.
- CLI: `spiketrace apply-active-review BASE_MANIFEST SELECTION REVIEW_INPUT OUTPUT_MANIFEST OUTPUT_RESULTS --repo-root . --legacy-base-match-id ID --review-match-id ID [options]`.
- Consumes: selection v1 and extracted review draft v1.
- Produces: an append-only cumulative training CSV and `round-01-results.json`.

- [ ] **Step 1: Write failing relative-to-absolute and crop tests**

```python
class ApplyActiveReviewTests(unittest.TestCase):
    def test_converts_relative_seconds_and_uses_the_selected_crop(self):
        result = apply_active_review(
            self.base_manifest,
            self.selection,
            self.review_draft,
            self.output_manifest,
            self.output_results,
            repo_root=self.root,
            legacy_base_match_id="usa-germany-2024-olympics",
            review_match_id="rangitoto-taka-national-final",
            require_files=False,
        )
        records = load_manifest(self.output_manifest, require_files=False)
        added = records[len(self.base_records):]
        self.assertEqual((added[0].start_seconds, added[0].end_seconds), (101.0, 102.0))
        self.assertEqual(added[0].label, "receive")
        self.assertEqual(added[0].team_side, "far")
        self.assertEqual(added[0].crop, (0, 0, 1920, 645))
        self.assertEqual(result["positive_action_count"], 1)
        self.assertEqual(result["settings"]["legacy_base_match_id"], "usa-germany-2024-olympics")
        self.assertEqual(result["settings"]["review_match_id"], "rangitoto-taka-national-final")
        self.assertEqual(
            result["settings"]["effective_video_root"],
            {"kind": "repo_relative", "path": "data/annotations"},
        )

    def test_does_not_modify_base_rows_or_source_files(self):
        before = self.base_manifest.read_bytes()
        apply_active_review(
            self.base_manifest,
            self.selection,
            self.review_draft,
            self.output_manifest,
            self.output_results,
            repo_root=self.root,
            legacy_base_match_id="usa-germany-2024-olympics",
            review_match_id="rangitoto-taka-national-final",
            require_files=False,
        )
        self.assertEqual(self.base_manifest.read_bytes(), before)
        output_rows = read_csv_rows(self.output_manifest)
        for source, migrated in zip(self.base_rows, output_rows[:len(self.base_rows)]):
            self.assertEqual(
                {key: value for key, value in migrated.items() if key != "match_id"},
                source,
            )
            self.assertEqual(migrated["match_id"], "usa-germany-2024-olympics")
        self.assertTrue(all(
            row["match_id"] == "rangitoto-taka-national-final"
            for row in output_rows[len(self.base_rows):]
        ))
```

- [ ] **Step 2: Run application tests and verify RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_active_learning_review.ApplyActiveReviewTests -v
```

Expected: import failure because `active_learning_review.py` does not exist.

- [ ] **Step 3: Implement strict draft validation and absolute action rows**

Verify selection, draft selection hash, workbook hash shape, video hash, exact 40-clip order, source boundaries and every relative action again in Python. Do not trust the Node extractor alone: positive inputs must still be integer seconds; the only legal `background` is a lone row with exact normalized relative bounds `[0, clip.duration_seconds]`. Reject partial background bounds and any background mixed with a positive action. For each positive action compute:

```python
absolute_start = round(clip["start_seconds"] + action["relative_start_seconds"], 6)
absolute_end = round(clip["start_seconds"] + action["relative_end_seconds"], 6)
crop = tuple(selection["video"]["crops"][action["team_side"]])
```

Validate both match IDs as distinct, nonempty text without control characters. Preserve any nonempty base `match_id`; fill blank legacy base rows with `legacy_base_match_id`, rejecting the migration if those blank rows resolve to more than one distinct base video. Append new rows with `match_id=review_match_id`, `split=train`, `team_side=far|near`, blank player number, crop coordinates, `review_status=reviewed`, and an audit note containing batch/clip/action slot and original relative seconds. Preserve every other base value and extra column; add missing canonical columns only at the end.

Define `effective_video_root = Path(video_root).resolve()` when supplied, otherwise `base_manifest_path.parent.resolve()`. Resolve the selection video from `repo_root / selection["video"]["path"]`, then serialize every new row's `video_path` relative to `effective_video_root` with POSIX separators. For the current layout this yields `../YTDown.com_YouTube_Rangitoto-...mp4`, not `data/...`; validate the completed output with `load_manifest(..., video_root=effective_video_root)`.

Persist the root through `effective_video_root_audit`: when the resolved root is inside `repo_root`, store `{"kind": "repo_relative", "path": relative_root.as_posix() or "."}`; otherwise store `{"kind": "absolute", "path": effective_video_root.as_posix()}`. The current application must therefore record exactly `{"kind": "repo_relative", "path": "data/annotations"}` and remain portable across checkout locations.

- [ ] **Step 4: Write failing hard-negative selection tests**

```python
class HardNegativeTests(unittest.TestCase):
    def test_uses_hard_negatives_first_and_respects_guard_and_cap(self):
        result = self.apply(background_guard_seconds=0.5, max_background_windows=9, background_seed=42)
        negatives = result["generated_background_windows"]
        self.assertLessEqual(len(negatives), result["positive_action_count"])
        self.assertTrue(negatives[0]["source_top1_action"] != "background")
        self.assertTrue(all(not overlaps_guard(window, self.actions, 0.5) for window in negatives))
        self.assertTrue(all(not overlaps(a, b) for i, a in enumerate(negatives) for b in negatives[i + 1:]))

    def test_zero_cap_is_valid(self):
        result = self.apply(max_background_windows=0)
        self.assertEqual(result["generated_background_windows"], [])
```

- [ ] **Step 5: Implement deterministic background extraction**

For each reviewed clip and its human side, inspect the corresponding embedded inference windows. A candidate window must be fully contained in the clip, not intersect any same-side positive action expanded by the guard, and not overlap another selected negative. Rank model non-background windows first by descending top-1 confidence, then background windows; within equal ranks use SHA-256 of `seed/clip_id/side/window_index`.

Use the effective cap:

```python
if (
    isinstance(background_guard_seconds, bool)
    or not isinstance(background_guard_seconds, (int, float))
    or not math.isfinite(float(background_guard_seconds))
    or background_guard_seconds < 0
):
    raise ActiveLearningError("background_guard_seconds must be finite and nonnegative.")
if max_background_windows is not None and (
    isinstance(max_background_windows, bool)
    or not isinstance(max_background_windows, int)
    or max_background_windows < 0
):
    raise ActiveLearningError("max_background_windows must be a nonnegative integer or None.")
requested_cap = positive_count if max_background_windows is None else max_background_windows
effective_cap = min(requested_cap, positive_count)
```

Record requested/effective cap, guard and actual seed in results. A human `background` row is review evidence, not itself a multi-second training record; training negatives come only from legal source windows.

- [ ] **Step 6: Enforce split isolation and atomic dual outputs**

Load the base manifest and reject if the Rangitoto source video already appears as `val` or `test`; allow later rounds if it already appears only as `train`. Refuse either output path if it exists, while treating those checks only as early diagnostics rather than concurrency protection. Build the CSV and results JSON in separate unique sibling temporary files opened exclusively, flush and `os.fsync()` both, validate the completed temporary CSV with `load_manifest`, and hash that exact temporary CSV into the results JSON before its final flush.

Publish with `os.link(temp_manifest, output_manifest_path)` followed by `os.link(temp_results, output_results_path)` so a racing writer causes `FileExistsError` rather than replacement. Track whether this invocation created the first final link. If the second link fails, remove the first final only after `os.path.samefile(temp_manifest, output_manifest_path)` proves it is still this invocation's hard link; then rethrow. Always unlink both temporary names in `finally`. Map either destination collision to `ActiveLearningError`, preserve pre-existing bytes, and never use `Path.replace()` or `os.replace()` for either final path.

The results JSON must include source/draft/base/output hashes, both match IDs, the effective video root, actual background parameters, preserved relative actions, absolute actions, generated backgrounds and per-label counts. Build `settings` explicitly before the result object so these application inputs cannot be lost behind defaults:

```python
settings = {
    "legacy_base_match_id": legacy_base_match_id,
    "review_match_id": review_match_id,
    "effective_video_root": effective_video_root_audit,
    "background_guard_seconds": background_guard_seconds,
    "requested_max_background_windows": requested_cap,
    "effective_max_background_windows": effective_cap,
    "background_seed": actual_background_seed,
    "require_files": require_files,
}
```

```python
results = {
    "format_version": 1,
    "batch_id": selection["batch_id"],
    "round_id": selection["round_id"],
    "selection_sha256": sha256_file(selection_path),
    "review_input_sha256": sha256_file(review_input_path),
    "base_manifest_sha256": sha256_file(base_manifest_path),
    "output_manifest": normalized_output_path,
    "output_manifest_sha256": sha256_file(temp_manifest),
    "settings": settings,
    "clips": audited_clips,
    "absolute_actions": absolute_actions,
    "generated_background_windows": negatives,
    "summary": summary,
}
```

- [ ] **Step 7: Add full validation and CLI tests**

Cover missing/reordered/duplicate clips, invalid action/side/time, timed or partial background, background mixing, NaN, invalid/missing/equal match IDs, multiple unresolved legacy base videos, wrong relative video-path base, exact repo-relative and external-absolute root audit objects, mismatched selection/draft/video hash, Rangitoto in val/test, output collisions, existing Rangitoto train rows, cap larger than positives and CLI `--allow-missing-videos`. Reject negative, NaN, positive/negative infinity and boolean background guards; reject boolean, fractional and negative background caps while preserving zero as valid. Inject temporary-write and `os.fsync()` failures before publication, a first-link collision, and a second-link failure after the manifest link succeeds. Assert every case removes unique temporary siblings, preserves any competing destination bytes, and leaves neither final created by this invocation after rollback. Assert the results settings persist both IDs and the effective root object exactly. Then run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_active_learning_review -v
.venv\Scripts\python.exe -m unittest tests.test_manifest -v
```

Expected: all application and command tests pass.

- [ ] **Step 8: Commit result application**

```powershell
git add src/spiketrace/active_learning_review.py src/spiketrace/cli.py tests/test_active_learning_review.py
git commit -m "feat: apply active review labels"
```

---

### Task 8: Versioned artifacts, real Rangitoto batch and README handoff

**Files:**
- Modify: `.gitignore`
- Modify: `README.md`
- Create: `data/active-learning/rangitoto/round-01-selection.json`
- Generated, ignored: `outputs/active-learning/rangitoto/round-01/review.xlsx`
- Generated, ignored: `outputs/active-learning/rangitoto/round-01/clips/*.mp4`

**Interfaces:**
- Produces: the real, verified 40-clip review batch that can be handed to the user.
- Preserves: full 2,942-candidate JSON/CSV/XLSX as audit evidence only.

- [ ] **Step 1: Add failing repository-policy assertions**

Extend the integration test to assert the README contains the three active-learning commands, `40`, `片段内`, `无音频`, `background`, `receive`, `dig`, and the independent-val limitation. Assert Git recognizes the selection JSON but continues ignoring proxy MP4 and previews:

```powershell
git check-ignore data/active-learning/rangitoto/round-01-selection.json
git check-ignore outputs/active-learning/rangitoto/round-01/clips/example.mp4
```

Expected before the ignore change: the selection JSON is ignored and the first command exits `0`; after the change it must exit `1`, while the proxy path remains ignored with exit `0`.

- [ ] **Step 2: Allowlist durable active-learning JSON only**

Change the data rules to:

```gitignore
data/*
!data/annotations/
!data/annotations/*.csv
!data/annotations/*.json
!data/active-learning/
!data/active-learning/**/
!data/active-learning/**/*.json
```

Do not unignore videos, workbooks, proxies or run directories.

- [ ] **Step 3: Generate and validate the real 40-clip selection**

Run from the repository root:

```powershell
.venv\Scripts\spiketrace.exe select-review-batch `
  outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.json `
  data\active-learning\rangitoto\round-01-selection.json `
  --repo-root . --round-number 1 --seed 42 `
  --preferred-clip-seconds 15 --min-clip-seconds 5 `
  --max-clip-seconds 30 --min-anchor-gap-seconds 5 --time-strata 10
```

Expected summary: exactly 40 unique clips, 15 available minority candidates covered before conflict fill, all five buckets represented, at least 10 time strata, legal durations, no source/group reuse, and source/video/checkpoint hashes equal the committed format-2 artifact.

- [ ] **Step 4: Build and independently verify the real workbook**

Use the workspace dependency loader to select Node and `@oai/artifact-tool`, then run:

```powershell
node tools\build_active_review_batch.mjs `
  data\active-learning\rangitoto\round-01-selection.json `
  outputs\active-learning\rangitoto\round-01 `
  outputs\active-learning\rangitoto\round-01-previews

node tools\verify_active_review_batch.mjs `
  data\active-learning\rangitoto\round-01-selection.json `
  outputs\active-learning\rangitoto\round-01\review.xlsx
```

Expected: 40 playable proxy files, exact hashes in `proxy-manifest.json`, four verified workbook sheets, 480 blank action slots, relative hyperlinks, no status/checkbox column and no formula errors.

- [ ] **Step 5: Synchronize README structure and user workflow**

Add `data/active-learning`, `active_learning_selection.py`, `active_learning_review.py`, `review_batch.py`, their tests, and the three new Node tools to the program tree. Document this exact user workflow:

```powershell
# 1. 生成选择和复核批次（程序执行）
spiketrace select-review-batch outputs\rangitoto-r3d18-bootstrap-review\merged_candidates.json data\active-learning\rangitoto\round-01-selection.json --repo-root .
node tools\build_active_review_batch.mjs data\active-learning\rangitoto\round-01-selection.json outputs\active-learning\rangitoto\round-01 outputs\active-learning\rangitoto\round-01-previews

# 2. 用户只填写 review.xlsx 的“人工动作”页

# 3. 提取并应用（程序执行）
node tools\extract_active_review_results.mjs data\active-learning\rangitoto\round-01-selection.json outputs\active-learning\rangitoto\round-01\review.xlsx data\active-learning\rangitoto\round-01-review-draft.json
spiketrace apply-active-review data\annotations\usa_germany_2024_annotations_expanded_batch_02.csv data\active-learning\rangitoto\round-01-selection.json data\active-learning\rangitoto\round-01-review-draft.json data\annotations\action_training_round_01.csv data\active-learning\rangitoto\round-01-results.json --repo-root . --legacy-base-match-id usa-germany-2024-olympics --review-match-id rangitoto-taka-national-final
```

State that proxies are silent, the user fills relative whole seconds, an explicit background means reviewed/no action, and the 40 clips are training-oriented biased samples rather than an accuracy test.

- [ ] **Step 6: Run all verification gates**

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\ruff.exe check .
.venv\Scripts\python.exe -m compileall -q src tests
node tools\test_active_review_batch.mjs
git diff --check
git status --short
```

Expected: all Python and Node tests pass, Ruff and compileall are clean, no whitespace errors, only intended source/docs/selection changes are tracked, and generated proxy/workbook files remain ignored.

- [ ] **Step 7: Commit the real review handoff**

```powershell
git add .gitignore README.md data/active-learning/rangitoto/round-01-selection.json
git commit -m "docs: hand off Rangitoto review round one"
```

Do not commit the completed results JSON or cumulative manifest until the user has filled all 40 clips and extraction/application have passed.
