# Efficient R3D-18 Fine-Tuning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在累计人工标签和独立完整比赛验证集就绪后，以分类头训练加 `layer4` 小学习率微调的两阶段流程，生成可复现且只能由跨比赛验证指标选出的 R3D-18 checkpoint。

**Architecture:** 旧 `train` 命令保持为历史 bootstrap 兼容入口；新的 `train-staged-r3d18` 使用严格的比赛级数据隔离、确定性轻量增强和两阶段冻结策略。训练配置、每阶段 checkpoint、数据及环境哈希全部落盘，任何没有独立 `val` 的运行在开始建模前失败。

**Tech Stack:** Python 3.10+、PyTorch、torchvision R3D-18、NumPy、OpenCV、标准库、`unittest`、Ruff。

## Global Constraints

- 训练模型始终使用一次全新加载的 Kinetics 预训练 R3D-18；不得从零训练，也不得用旧 bootstrap checkpoint 初始化训练参数。
- `baseline_checkpoint_path` 只加载到独立的对照模型并在同一 `val` 上评估；不得向训练模型复制任何 baseline 参数，配置必须分别记录 `initialization` 和 `baseline_comparison`。
- 新训练入口不提供 `--allow-train-only`，并强制存在来自不同完整比赛的 `train` 和 `val`。
- 如果 Rangitoto 标签进入 `train`，Rangitoto 不得出现在 `val` 或 `test`。
- 同一 `match_id` 不得跨 `train/val/test`；不同文件名、裁剪或重新编码不能绕过比赛级隔离。
- 阶段一只训练 `fc`；七分类 R3D-18 的可训练参数精确为 `512 * 7 + 7 = 3591`。
- 阶段二只训练 `layer4 + fc`，其中 `layer4` 默认学习率 `1e-5`，`fc` 默认学习率 `1e-4`。
- 冻结模块在训练 epoch 中保持 `eval()`，不得更新 BatchNorm running statistics。
- 阶段一默认 5 epoch，阶段二默认 5 epoch；阶段二必须从阶段一 `best.pt` 开始。
- 全局最佳 checkpoint 只按 `(val Macro F1 降序, val loss 升序, global epoch 升序)` 选择。
- 类别损失权重固定为 `N / (7 * class_count)`；任一训练类计数为零时拒绝训练，不叠加 `WeightedRandomSampler`。
- 训练增强只允许时间抖动、目标裁剪内部的空间裁剪和亮度抖动；不得水平翻转或引入对方半场。
- 验证集不使用随机增强，`test` 不进入 loader、checkpoint 选择或阈值调整。
- 固定 seed 必须同时控制 Python、NumPy、torch、CUDA、DataLoader shuffle 和 worker 初始化；启用 torch deterministic algorithms，并把实际 deterministic 后端设置写入配置。
- 输出目录已存在时拒绝覆盖；不得修改旧 bootstrap run 或任何历史训练清单。
- `training_config.json`、`metrics.json` 和 checkpoint 必须记录数据哈希、冻结范围、学习率、增强、随机种子和主动学习轮次。
- 每个主动学习轮次必须提供对应的不可变 results JSON；训练前必须哈希并记录其精确字节，验证权威累计清单 SHA，以及当前派生清单对最新权威清单的逐行前缀关系。
- 没有独立验证比赛真值时只实现和测试训练器，不实际启动真实两阶段训练，也不声明准确率。
- 每次模块或目录结构变化必须同步 `README.md`，且 `pyproject.toml` 作者名保持 `jmmyxa`。
- 测试使用 `unittest`，不要引入 pytest。

---

### Task 1: Match identity and strict split isolation

**Files:**
- Modify: `src/spiketrace/domain.py`
- Modify: `src/spiketrace/manifest.py`
- Modify: `tests/test_manifest.py`

**Interfaces:**
- Produces: optional CSV column `match_id` on `AnnotationRecord.match_id: str | None = None`.
- Produces: `summarize_manifest()` keys `matches` and `matches_by_split`.
- Preserves: all historical manifests without `match_id` remain readable by legacy commands.

- [ ] **Step 1: Write failing match-level leakage tests**

```python
class MatchIdentityTests(unittest.TestCase):
    def test_rejects_same_match_across_splits_even_with_different_video_files(self):
        manifest = self.write_manifest(
            "video_path,start_seconds,end_seconds,label,split,match_id\n"
            "camera-a.mp4,0,1,serve,train,final-2025\n"
            "camera-b.mp4,1,2,receive,val,final-2025\n"
        )
        with self.assertRaisesRegex(ManifestError, "match_id.*multiple dataset splits"):
            load_manifest(manifest, require_files=False)

    def test_allows_two_views_of_one_match_in_the_same_split(self):
        manifest = self.write_manifest(
            "video_path,start_seconds,end_seconds,label,split,match_id\n"
            "camera-a.mp4,0,1,serve,train,final-2025\n"
            "camera-b.mp4,1,2,receive,train,final-2025\n"
        )
        records = load_manifest(manifest, require_files=False)
        self.assertEqual([record.match_id for record in records], ["final-2025", "final-2025"])

    def test_old_manifest_without_match_id_still_loads(self):
        records = load_manifest(self.legacy_manifest, require_files=False)
        self.assertIsNone(records[0].match_id)
```

- [ ] **Step 2: Run manifest tests and verify RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_manifest.MatchIdentityTests -v
```

Expected: failure because `AnnotationRecord` and the loader do not expose `match_id`.

- [ ] **Step 3: Parse, normalize and summarize match identity**

Append the optional field to the dataclass so existing keyword construction remains compatible:

```python
@dataclass(frozen=True, slots=True)
class AnnotationRecord:
    video_path: Path
    start_seconds: float
    end_seconds: float
    label: str
    split: str
    team_side: str | None = None
    player_number: str | None = None
    crop: tuple[int, int, int, int] | None = None
    match_id: str | None = None
```

Read `match_id` through `_optional_text`, reject control characters, and group all non-null IDs by split after the existing path-based leakage check. Extend the summary without removing old keys:

```python
match_ids = {record.match_id for record in items if record.match_id is not None}
matches_by_split = {
    split: len({record.match_id for record in items if record.split == split and record.match_id})
    for split in sorted(ALLOWED_SPLITS)
}
```

- [ ] **Step 4: Add exact summary and malformed-ID tests**

Assert whitespace is trimmed, empty values normalize to `None`, counts are exact, and IDs containing newlines/tabs are rejected. Run:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_manifest -v
```

Expected: all manifest tests pass, including legacy files.

- [ ] **Step 5: Commit match-level isolation**

```powershell
git add src/spiketrace/domain.py src/spiketrace/manifest.py tests/test_manifest.py
git commit -m "feat: enforce match level split isolation"
```

---

### Task 2: Deterministic train-only augmentation

**Files:**
- Create: `src/spiketrace/augmentation.py`
- Create: `tests/test_augmentation.py`

**Interfaces:**
- Produces: `derive_record_seed(seed, epoch, record_index) -> int`.
- Produces: `jitter_clip_bounds(start_seconds, end_seconds, video_duration_seconds, *, max_jitter_seconds, seed) -> tuple[float, float]`.
- Produces: `augment_rgb_clip(frames, *, output_size, spatial_crop_scale, brightness_jitter, seed) -> np.ndarray`.
- Consumes: existing `sample_video_clip(video_path, start_seconds, end_seconds, *, num_frames, image_size=pre_resize_size, crop=record.crop)` so the opponent half is removed before augmentation.

- [ ] **Step 1: Write failing deterministic augmentation tests**

```python
class DeterministicAugmentationTests(unittest.TestCase):
    def test_same_record_seed_repeats_exactly_and_epoch_changes_it(self):
        first = derive_record_seed(42, 3, 7)
        self.assertEqual(first, derive_record_seed(42, 3, 7))
        self.assertNotEqual(first, derive_record_seed(42, 4, 7))

    def test_temporal_jitter_preserves_duration_and_clamps_to_video(self):
        start, end = jitter_clip_bounds(0.0, 1.0, 10.0, max_jitter_seconds=0.1, seed=3)
        self.assertEqual(end - start, 1.0)
        self.assertGreaterEqual(start, 0.0)
        self.assertLessEqual(end, 10.0)

    def test_spatial_and_brightness_transform_is_clip_consistent(self):
        frames = np.stack([self.gradient_frame] * 4)
        result = augment_rgb_clip(
            frames,
            output_size=8,
            spatial_crop_scale=0.8,
            brightness_jitter=0.1,
            seed=123,
        )
        self.assertEqual(result.shape, (4, 8, 8, 3))
        np.testing.assert_array_equal(result[0], result[1])
        repeated = augment_rgb_clip(
            frames,
            output_size=8,
            spatial_crop_scale=0.8,
            brightness_jitter=0.1,
            seed=123,
        )
        np.testing.assert_array_equal(result, repeated)
```

- [ ] **Step 2: Run augmentation tests and verify RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_augmentation -v
```

Expected: import failure because `augmentation.py` does not exist.

- [ ] **Step 3: Implement version-stable seed and bounded transforms**

Use SHA-256 rather than process-randomized `hash()` or version-dependent random sampling:

```python
def derive_record_seed(seed: int, epoch: int, record_index: int) -> int:
    raw = f"{seed}\0{epoch}\0{record_index}".encode("ascii")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")
```

Use NumPy `Generator(PCG64(seed))`. Draw one temporal delta in `[-max,+max]`, one square crop origin shared by all frames, and one brightness factor in `[1-jitter,1+jitter]` shared by all frames. Validate `0 < spatial_crop_scale <= 1`, `0 <= brightness_jitter <= 1`, finite values and positive image size. Never flip frames.

- [ ] **Step 4: Verify pre-resize sampling uses the existing crop boundary**

In the augmentation test, patch `sample_video_clip` and later assert the staged dataset calls it with `image_size=ceil(output_size / spatial_crop_scale)` and the unchanged `record.crop`. This uses the existing sampler's image-size argument and requires no video API change.

- [ ] **Step 5: Run augmentation and video regressions**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_augmentation -v
```

Expected: all tests pass, transforms remain deterministic, and no source crop expands beyond the annotation crop.

- [ ] **Step 6: Commit deterministic augmentation**

```powershell
git add src/spiketrace/augmentation.py tests/test_augmentation.py
git commit -m "feat: add deterministic video augmentation"
```

---

### Task 3: R3D-18 stage freezing and BatchNorm control

**Files:**
- Modify: `src/spiketrace/ml.py`
- Modify: `tests/test_ml.py`

**Interfaces:**
- Produces: `configure_r3d18_fine_tune_stage(model, stage: Literal["head", "layer4"]) -> dict[str, object]`.
- Produces: `set_r3d18_stage_train_mode(model, stage: Literal["head", "layer4"]) -> None`.
- Produces: metadata keys `trainable_modules`, `frozen_modules`, `trainable_parameters`, `frozen_parameters`.

- [ ] **Step 1: Write failing parameter-freeze tests on a real non-pretrained R3D-18**

```python
class R3D18FineTuneStageTests(unittest.TestCase):
    def setUp(self):
        self.model = create_model("r3d18", 7, pretrained=False)

    def test_head_stage_only_trains_fc(self):
        metadata = configure_r3d18_fine_tune_stage(self.model, "head")
        trainable = {name for name, parameter in self.model.named_parameters() if parameter.requires_grad}
        self.assertEqual(trainable, {"fc.weight", "fc.bias"})
        self.assertEqual(metadata["trainable_parameters"], 3591)

    def test_layer4_stage_only_trains_layer4_and_fc(self):
        configure_r3d18_fine_tune_stage(self.model, "layer4")
        trainable = {name for name, parameter in self.model.named_parameters() if parameter.requires_grad}
        self.assertTrue(all(name.startswith(("layer4.", "fc.")) for name in trainable))
        self.assertTrue(any(name.startswith("layer4.") for name in trainable))
```

- [ ] **Step 2: Run freeze tests and verify RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_ml.R3D18FineTuneStageTests -v
```

Expected: import failure because the stage helpers do not exist.

- [ ] **Step 3: Implement exact module policies**

Use these module sets and reject non-R3D-18 shapes or unknown stages:

```python
R3D18_STAGE_MODULES = {
    "head": {"trainable": ("fc",), "frozen": ("stem", "layer1", "layer2", "layer3", "layer4")},
    "layer4": {"trainable": ("layer4", "fc"), "frozen": ("stem", "layer1", "layer2", "layer3")},
}
```

Set every parameter false before enabling the selected modules. Count parameters from `numel()` and return sorted module names and counts.

- [ ] **Step 4: Write and satisfy frozen-BatchNorm tests**

```python
def test_training_mode_keeps_frozen_batchnorm_in_eval(self):
    configure_r3d18_fine_tune_stage(self.model, "head")
    set_r3d18_stage_train_mode(self.model, "head")
    self.assertTrue(self.model.fc.training)
    self.assertFalse(self.model.stem[1].training)
    before = self.model.stem[1].running_mean.clone()
    self.model(torch.randn(2, 3, 4, 32, 32))
    torch.testing.assert_close(self.model.stem[1].running_mean, before)
```

`set_r3d18_stage_train_mode` first calls `model.train()` and then calls `.eval()` on all frozen root modules, so every nested BatchNorm remains frozen while `layer4/fc` follow the active stage.

- [ ] **Step 5: Run ML tests and commit**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_ml -v
git add src/spiketrace/ml.py tests/test_ml.py
git commit -m "feat: configure staged r3d18 fine tuning"
```

---

### Task 4: Active training dataset, class weights and provenance metadata

**Files:**
- Create: `src/spiketrace/staged_training.py`
- Create: `tests/test_staged_training.py`
- Modify: `src/spiketrace/ml.py`
- Modify: `tests/test_ml.py`

**Interfaces:**
- Produces: `ActiveVideoClipDataset(records, *, labels, num_frames, image_size, training, augmentation, seed)` with `set_epoch(epoch)`.
- Produces: `validate_staged_records(records) -> dict[str, object]` with train/val/test match and video-hash evidence.
- Produces: `validate_active_learning_lineage(manifest_path, result_paths, active_learning_rounds, *, repo_root) -> list[dict[str, object]]`.
- Produces: `compute_balanced_class_weights(train_records, labels) -> tuple[list[float], dict[str, int]]`; every input record must have `split == "train"`.
- Extends: `make_checkpoint(*, model, model_name, labels, model_version, num_frames, image_size, window_seconds, epoch, metrics, sampling_contract=SAMPLING_CONTRACT, training_metadata=None) -> dict[str, Any]`.
- Preserves: checkpoint format version 1 and historical checkpoint loading.

- [ ] **Step 1: Write failing dataset determinism and validation tests**

```python
class ActiveVideoClipDatasetTests(unittest.TestCase):
    def test_train_sample_is_worker_order_independent(self):
        dataset = self.make_dataset(training=True, seed=42)
        dataset.set_epoch(3)
        first = dataset[0][0].clone()
        _ = dataset[1]
        torch.testing.assert_close(dataset[0][0], first)

    def test_validation_sample_never_changes_with_epoch(self):
        dataset = self.make_dataset(training=False, seed=42)
        first = dataset[0][0].clone()
        dataset.set_epoch(99)
        torch.testing.assert_close(dataset[0][0], first)

    def test_requires_match_id_for_every_train_val_and_test_record(self):
        with self.assertRaisesRegex(ValueError, "match_id"):
            validate_staged_records(self.records_with_missing_match_id)

    def test_rejects_train_test_match_reuse_even_when_video_files_differ(self):
        with self.assertRaisesRegex(ValueError, "match_id.*multiple dataset splits"):
            validate_staged_records(self.records_with_train_test_match_reuse)

    def test_binds_derived_manifest_to_the_audited_round_result(self):
        lineage = validate_active_learning_lineage(
            self.manifest_with_appended_val,
            [self.round_one_results],
            ["rangitoto:round-01"],
            repo_root=self.root,
        )
        self.assertEqual(lineage[0]["round"], "rangitoto:round-01")
        self.assertEqual(lineage[0]["results_sha256"], sha256_file(self.round_one_results))
        self.assertEqual(
            lineage[0]["reviewed_manifest_sha256"],
            sha256_file(self.round_one_manifest),
        )
        self.assertEqual(
            lineage[0]["derived_manifest_relation"],
            {
                "kind": "exact_csv_row_prefix",
                "parent_row_count": len(self.round_one_rows),
                "appended_non_train_rows": len(self.val_rows),
            },
        )
```

- [ ] **Step 2: Run dataset tests and verify RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_staged_training.ActiveVideoClipDatasetTests -v
```

Expected: import failure because `staged_training.py` does not exist.

- [ ] **Step 3: Implement staged-record validation and dataset sampling**

Require nonempty train and val splits and a nonempty `match_id` on every manifest record in every present split, including `test`. Across all present `train`/`val`/`test` pairs, require disjoint match IDs and no repeated video-content SHA, so alternate names, crops and re-encodings cannot bypass the staged gate. Ignore `test` records only when constructing loaders; they remain fully covered by identity and content-leakage validation. Cache `inspect_video` metadata by path. Train samples use derived record seed, bounded temporal jitter, pre-resize sampling inside `record.crop`, then the spatial/brightness transform. Validation samples use the legacy center sampling and no augmentation.

For lineage validation, require `len(result_paths) == len(active_learning_rounds)`, unique ordered paths and nonempty `name:round-id` labels. Load every results JSON with duplicate-key and non-finite-number rejection; require format 1 and require the label suffix after `:` to equal `round_id`. Recompute each results file SHA, resolve its `output_manifest` inside `repo_root`, and require the file SHA to equal `output_manifest_sha256`. For consecutive rounds, require the later `base_manifest_sha256` to equal the preceding result's `output_manifest_sha256`. Open the latest reviewed manifest and current derived manifest with `newline=""`, then pass each handle to `csv.DictReader`; require identical headers, require every parent row to be an exact ordered prefix of the derived rows, and require every appended row to have `split` equal to `val` or `test`, never `train`. Return normalized repo-relative paths, results SHA, reviewed-manifest SHA and the exact prefix/appended counts shown in the test. Any mismatch must fail before CUDA, dataset or output-directory creation.

Expose this immutable augmentation config:

```python
@dataclass(frozen=True, slots=True)
class AugmentationConfig:
    temporal_jitter_seconds: float = 0.1
    spatial_crop_scale: float = 0.9
    brightness_jitter: float = 0.1
```

- [ ] **Step 4: Write and satisfy class-weight tests**

```python
def test_computes_exact_balanced_weights(self):
    weights, counts = compute_balanced_class_weights(self.train_records, ACTION_LABELS)
    self.assertEqual(counts, {label: expected_counts[label] for label in ACTION_LABELS})
    self.assertEqual(
        weights,
        [len(self.train_records) / (7 * expected_counts[label]) for label in ACTION_LABELS],
    )

def test_rejects_a_missing_training_class_before_model_creation(self):
    with self.assertRaisesRegex(ValueError, "zero training samples.*dig"):
        compute_balanced_class_weights(self.train_records_without_dig, ACTION_LABELS)

def test_rejects_validation_rows_in_class_weight_input(self):
    with self.assertRaisesRegex(ValueError, "train records only"):
        compute_balanced_class_weights(
            self.train_records + [self.validation_record],
            ACTION_LABELS,
        )
```

Call this helper with the already separated `train_records`, never the full manifest. Do not create a sampler; only pass the returned tensor to `CrossEntropyLoss`.

- [ ] **Step 5: Add optional checkpoint training metadata**

Extend `make_checkpoint` as follows and deep-copy the metadata into the payload:

Add `training_metadata: dict[str, object] | None = None` after `sampling_contract` and refactor the existing literal return into this exact shape:

```python
checkpoint = {
    "format_version": CHECKPOINT_FORMAT_VERSION,
    "model_name": model_name,
    "model_version": model_version,
    "labels": list(labels),
    "action_label_schema_version": ACTION_LABEL_SCHEMA_VERSION,
    "sampling_contract": sampling_contract,
    "num_frames": num_frames,
    "image_size": image_size,
    "window_seconds": window_seconds,
    "epoch": epoch,
    "metrics": metrics,
    "normalization": {"mean": list(KINETICS_MEAN), "std": list(KINETICS_STD)},
    "model_state": model.state_dict(),
}
if training_metadata is not None:
    checkpoint["training_metadata"] = copy.deepcopy(training_metadata)
return checkpoint
```

Add tests that new metadata round-trips and old six/seven-class checkpoints without the field still load unchanged.

- [ ] **Step 6: Run focused tests and commit**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_staged_training tests.test_ml -v
git add src/spiketrace/staged_training.py src/spiketrace/ml.py tests/test_staged_training.py tests/test_ml.py
git commit -m "feat: prepare reproducible staged training data"
```

---

### Task 5: Two-stage training loop and checkpoint selection

**Files:**
- Modify: `src/spiketrace/staged_training.py`
- Modify: `tests/test_staged_training.py`

**Interfaces:**
- Produces: `train_staged_r3d18(manifest_path, output_dir, *, repo_root, video_root=None, model_version="action-r3d18-active-v0.1", active_learning_rounds=(), active_learning_result_paths=(), baseline_checkpoint_path=None, head_epochs=5, layer4_epochs=5, batch_size=4, head_learning_rate=1e-3, layer4_learning_rate=1e-5, layer4_head_learning_rate=1e-4, weight_decay=1e-4, num_frames=16, image_size=112, window_seconds=1.0, temporal_jitter_seconds=0.1, spatial_crop_scale=0.9, brightness_jitter=0.1, device="auto", seed=42, num_workers=0) -> dict[str, object]`.
- Produces: `_run_staged_epoch(model, loader, criterion, device, *, optimizer=None, train_mode_callback=None) -> tuple[float, list[int], list[int]]`.
- Produces: `training_config.json`, `metrics.json`, global `best.pt/latest.pt`, and per-stage checkpoints.

- [ ] **Step 1: Write failing stage-transition and optimizer tests**

Patch expensive decoding/epochs with deterministic small tensors and metrics:

```python
class StagedTrainingLoopTests(unittest.TestCase):
    def test_layer4_starts_from_head_best_not_head_latest(self):
        report = self.train_with_epoch_metrics([
            ("head", 1, 0.40, 0.8),
            ("head", 2, 0.30, 0.7),
            ("layer4", 1, 0.45, 0.6),
        ])
        self.assertEqual(report["stages"]["layer4"]["initialized_from"], "stages/head/best.pt")
        self.assertEqual(self.loaded_stage_two_epoch, 1)

    def test_layer4_optimizer_has_exact_learning_rates(self):
        groups = self.captured_layer4_optimizer.param_groups
        self.assertEqual([group["lr"] for group in groups], [1e-5, 1e-4])
        self.assertEqual([group["name"] for group in groups], ["layer4", "fc"])
```

- [ ] **Step 2: Run loop tests and verify RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_staged_training.StagedTrainingLoopTests -v
```

Expected: failures because `train_staged_r3d18` is not implemented.

- [ ] **Step 3: Validate provenance and establish deterministic runtime before model creation**

Validate the active-learning lineage before creating the output or staging directory and before any CUDA/model work. Then refuse an existing output directory and build in a sibling staging directory. Before `resolve_device`, `seed_everything` or any other call that may initialize CUDA/cuBLAS, fix the cuBLAS workspace contract; then resolve the device, validate records, hash the manifest and every distinct video, and compute counts/weights from `train_records` only. Before constructing datasets, loaders or the model, apply this deterministic setup:

```python
os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
seed_everything(seed)
torch.use_deterministic_algorithms(True)
if hasattr(torch.backends, "cudnn"):
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
loader_generator = torch.Generator()
loader_generator.manual_seed(seed)
```

Pass `generator=loader_generator` to the shuffled train DataLoader. Define a top-level, Windows-picklable `_seed_worker(worker_id)` that derives `worker_seed = torch.initial_seed() % 2**32` and seeds Python and NumPy; pass it as `worker_init_fn` to both loaders. The per-record augmentation still derives from `(seed, epoch, record_index)`, so worker scheduling cannot change a sample. Collect every deterministic input below in memory, but do not serialize the final configuration yet: `initialization_metadata` and `baseline_metadata` are only available after the isolated model setup in Step 4. Immediately after that setup and before the first training epoch, build this exact object once:

```python
config = {
    "strategy_version": "r3d18-two-stage-v1",
    "manifest": normalized_manifest_path,
    "manifest_sha256": sha256_file(manifest_path),
    "manifest_summary": summarize_manifest(records),
    "video_sha256": video_hashes,
    "model_name": "r3d18",
    "model_version": model_version,
    "initialization": initialization_metadata,
    "baseline_comparison": baseline_metadata,
    "active_learning_rounds": list(active_learning_rounds),
    "active_learning_lineage": active_learning_lineage,
    "stages": stage_configs,
    "class_counts": class_counts,
    "class_weights": dict(zip(ACTION_LABELS, class_weights)),
    "augmentation": asdict(augmentation),
    "seed": seed,
    "deterministic": {
        "algorithms": True,
        "cudnn_benchmark": False,
        "cudnn_deterministic": True,
        "cublas_workspace_config": os.environ["CUBLAS_WORKSPACE_CONFIG"],
        "dataloader_generator_seed": seed,
        "worker_seed_source": "torch.initial_seed_mod_2_32",
    },
    "environment": environment_versions,
    "generalization_metrics_available": True,
    "selection_split": "val",
}
```

Record Python, torch, torchvision, NumPy, OpenCV and selected device versions. After Step 4 supplies the two model-provenance fields, atomically write `training_config.json`, hash its exact bytes and never mutate it during the run. Pass `training_metadata` to every checkpoint with that config SHA, the derived manifest SHA, `active_learning_rounds` and a deep copy of `active_learning_lineage`, so the checkpoint remains directly traceable without relying on a nearby config file.

- [ ] **Step 4: Implement fresh initialization, isolated baseline comparison and both stages**

Always construct the training model first, immediately after deterministic seeding, from a fresh `r3d_18(weights=R3D_18_Weights.DEFAULT)` call. Replace only its final layer for the canonical seven labels and record the resolved torchvision weight enum under `initialization`. This rule and initialization order are unchanged when `baseline_checkpoint_path` is supplied, so constructing the comparison model cannot consume RNG before the training head is initialized.

When `baseline_checkpoint_path` is present, construct a second model, load the checkpoint only into that comparison model, require seven labels in canonical order and compatible preprocessing/sampling, record its normalized path and SHA-256 under `baseline_comparison`, evaluate it once on the unchanged val loader, then discard it before training. Never call `training_model.load_state_dict()` with baseline state and never reuse the baseline model object as the training model. When the option is absent, record `baseline_comparison: null` and promotion eligibility remains false.

Now finalize and atomically write the configuration object from Step 3, including the resolved Kinetics weight enum and the baseline's path, SHA and same-val metrics (or `null`). No optimizer step or training epoch may begin until this file has been reloaded, hashed and confirmed equal to the in-memory object.

For each stage:

```python
configure_r3d18_fine_tune_stage(model, stage_name)
for stage_epoch in range(1, epochs + 1):
    train_dataset.set_epoch(global_epoch)
    set_r3d18_stage_train_mode(model, stage_name)
    train_loss, train_targets, train_predictions = _run_staged_epoch(
        model,
        train_loader,
        criterion,
        selected_device,
        optimizer=optimizer,
        train_mode_callback=lambda: set_r3d18_stage_train_mode(model, stage_name),
    )
    val_loss, val_targets, val_predictions = _run_staged_epoch(
        model,
        val_loader,
        criterion,
        selected_device,
    )
    candidate_key = (-float(val_metrics["macro_f1"]), float(val_loss), global_epoch)
```

Save stage `latest.pt` every epoch, stage `best.pt` when the same key improves, and global `best.pt` across both stages. After head stage, load `stages/head/best.pt` into the same model before configuring layer4.

- [ ] **Step 5: Ensure frozen mode is reapplied after `_run_epoch` calls `model.train()`**

Change the staged loop's epoch helper to accept `train_mode_callback`; invoke it immediately after `model.train(True)`. Do not change legacy `_run_epoch` behavior. Test frozen BatchNorm running statistics across a real optimizer step and ensure layer4 changes only in stage two.

- [ ] **Step 6: Write exact metrics and promotion evidence**

`metrics.json` must include every epoch, stage best keys, global best path, baseline-on-same-val metrics when supplied, and:

```python
promotion = {
    "eligible": baseline_metrics is not None,
    "macro_f1_strictly_improved": new_macro_f1 > baseline_macro_f1,
    "minority_mean_f1_not_lower": new_minority_mean >= baseline_minority_mean,
    "recall_drop_within_0_05": recall_drop_ok,
    "passed": all(criteria) if baseline_metrics is not None else False,
}
```

Compute the minority mean over `receive/block/dig`. Apply the recall-drop rule only to validation classes with support at least 5. Label this as validation promotion evidence, never final test accuracy.

- [ ] **Step 7: Add failure, tie-break and atomic-output tests**

Cover absent val, missing match IDs in each of train/val/test, match leakage across every split pair, duplicate video SHA across every split pair, zero-count class, accidental val/test rows in class-weight input, baseline label mismatch, invalid epochs/LRs/augmentation, exact tie-break behavior, stage-two initialization, global-best selection, output collision, handled failure cleanup and test records never entering a loader. Add lineage failures for missing/extra/reordered results, round-label mismatch, duplicate JSON keys, non-finite JSON, required results-field mutation, missing/escaping reviewed manifest, `output_manifest_sha256` mismatch, broken consecutive-round hash chain, changed/reordered/deleted parent row, changed header and appended `train` row; assert they fail before output-directory or model creation. Assert config and checkpoint metadata contain the same results SHA, reviewed-manifest SHA and derived-prefix evidence. Patch Kinetics and baseline loading separately; assert the training model starts from the same fresh Kinetics state whether or not a baseline is supplied, the baseline uses a distinct model object, and no bootstrap backbone tensor enters training initialization. Run two patched miniature trainings with the same seed and assert identical loader index order, epoch metrics and checkpoint-selection keys; changing the seed may change train order but not validation order. Add an opt-in CUDA integration test that starts a fresh process, verifies `CUBLAS_WORKSPACE_CONFIG=:4096:8` before the first CUDA operation, runs one forward/backward optimizer step twice with the same seed, and compares the resulting metrics and trainable tensors exactly.

- [ ] **Step 8: Run staged training tests and full regression suite**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_staged_training -v
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: all tests pass without downloading Kinetics weights because unit tests patch initialization; the real path remains integration-tested behind an explicit opt-in.

When a CUDA development machine is available, run the deterministic integration gate in a fresh process:

```powershell
$env:SPIKETRACE_TEST_CUDA = "1"
.venv\Scripts\python.exe -m unittest tests.test_staged_training.CudaDeterminismIntegrationTests -v
Remove-Item Env:SPIKETRACE_TEST_CUDA
```

Expected: the test performs two real CUDA forward/backward steps and passes; when the opt-in variable is absent, the normal suite does not require a GPU.

- [ ] **Step 9: Commit the two-stage trainer**

```powershell
git add src/spiketrace/staged_training.py tests/test_staged_training.py
git commit -m "feat: train r3d18 in two fine tuning stages"
```

---

### Task 6: Strict training CLI

**Files:**
- Modify: `src/spiketrace/cli.py`
- Modify: `tests/test_staged_training.py`

**Interfaces:**
- CLI: `spiketrace train-staged-r3d18 MANIFEST OUTPUT_DIR [options]`.
- Preserves: legacy `spiketrace train` flags and behavior unchanged.

- [ ] **Step 1: Write failing parse and dispatch tests**

```python
class StagedTrainingCommandTests(unittest.TestCase):
    def test_parses_and_dispatches_all_stage_settings(self):
        args = build_parser().parse_args([
            "train-staged-r3d18", "annotations.csv", "runs/round-01",
            "--repo-root", ".",
            "--active-learning-round", "rangitoto:round-01",
            "--active-learning-result", "data/active-learning/rangitoto/round-01-results.json",
            "--head-epochs", "3", "--layer4-epochs", "4",
            "--head-learning-rate", "0.001",
            "--layer4-learning-rate", "0.00001",
            "--layer4-head-learning-rate", "0.0001",
            "--device", "cpu",
        ])
        with mock.patch("spiketrace.staged_training.train_staged_r3d18", return_value={"ok": True}) as train:
            self.assertEqual(run_command(args), {"ok": True})
        self.assertEqual(train.call_args.kwargs["active_learning_rounds"], ["rangitoto:round-01"])
        self.assertEqual(
            train.call_args.kwargs["active_learning_result_paths"],
            ["data/active-learning/rangitoto/round-01-results.json"],
        )
        self.assertEqual(train.call_args.kwargs["head_epochs"], 3)
        self.assertEqual(train.call_args.kwargs["layer4_epochs"], 4)
```

- [ ] **Step 2: Run command tests and verify RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_staged_training.StagedTrainingCommandTests -v
```

Expected: parser rejection because the command does not exist.

- [ ] **Step 3: Add the exact CLI surface**

Implement:

```text
train-staged-r3d18 MANIFEST OUTPUT_DIR
  --repo-root PATH
  --video-root PATH
  --model-version action-r3d18-active-v0.1
  --active-learning-round rangitoto:round-01   # repeatable
  --active-learning-result PATH                # repeatable; one per round in the same order
  --baseline-checkpoint PATH              # same-val comparison only; never training initialization
  --head-epochs 5 --layer4-epochs 5
  --head-learning-rate 0.001
  --layer4-learning-rate 0.00001
  --layer4-head-learning-rate 0.0001
  --batch-size 4 --weight-decay 0.0001
  --num-frames 16 --image-size 112 --window-seconds 1
  --temporal-jitter-seconds 0.1
  --spatial-crop-scale 0.9 --brightness-jitter 0.1
  --device auto --seed 42 --num-workers 0
```

Do not add `--allow-train-only`, `--pretrained`, arbitrary model names or test-set selection options.

- [ ] **Step 4: Run CLI and regression tests**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_staged_training tests.test_training -v
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: new CLI dispatches exact values and all legacy train tests remain green.

- [ ] **Step 5: Commit the strict command**

```powershell
git add src/spiketrace/cli.py tests/test_staged_training.py
git commit -m "feat: expose staged r3d18 training command"
```

---

### Task 7: README, data handoff and verification gates

**Files:**
- Modify: `README.md`
- Consume after Round 01 review exists: `data/annotations/action_training_round_01.csv`
- Consume after Round 01 review exists: `data/active-learning/rangitoto/round-01-results.json`
- Create only after complete independent val labels exist: `data/annotations/action_training_round_01_with_val.csv`
- Generated later, ignored: `runs/action-active-round-01/**`

**Interfaces:**
- Produces: a documented training command that is deliberately blocked until an independent validation match is fully labeled.
- Defines: `train = USA vs Germany + Rangitoto`, `val = SoCal Cup or another untouched complete match`, `test = a later fourth untouched match`.

- [ ] **Step 1: Synchronize the program structure**

Add `augmentation.py`, `staged_training.py`, their tests, the optional `match_id` manifest field, the new run directory shape and command. Preserve the explicit statement that frontend, identity, accounts and database work remain paused.

- [ ] **Step 2: Validate the Round 01 IDs and append an independent val manifest**

Treat `data/annotations/action_training_round_01.csv` as the immutable output of Round 01 `apply-active-review`. Load `data/active-learning/rangitoto/round-01-results.json`, recompute its own SHA-256, resolve its recorded `output_manifest`, and require that file's bytes to match `output_manifest_sha256`. That command already writes the authoritative metadata ID `match_id=usa-germany-2024-olympics` on preserved USA rows and `match_id=rangitoto-taka-national-final` on reviewed Rangitoto rows. Validate those exact IDs; if any path, hash or ID is absent or wrong, stop and rerun the Round 01 application task with its required ID arguments instead of manually editing or recreating the cumulative file.

Only after a complete independent SoCal/other validation match exists, create `action_training_round_01_with_val.csv` as a new file: keep the exact Round 01 header and rows as an ordered prefix, append only the full val annotations with a distinct stable ID such as `socal-cup-final-2025`, and validate every row before training. The staged command must recheck that prefix against the results-bound parent CSV; a manually changed/reordered/deleted parent row or appended `train` row is an error. Do not invent val rows merely to satisfy the command. Document that `match_id` represents the real-world match, not the camera file, and must be shared by alternate views/re-encodings.

- [ ] **Step 3: Document the future real training command without running it prematurely**

```powershell
.venv\Scripts\spiketrace.exe train-staged-r3d18 `
  data\annotations\action_training_round_01_with_val.csv `
  runs\action-active-round-01 `
  --repo-root . `
  --model-version action-r3d18-active-round-01 `
  --active-learning-round rangitoto:round-01 `
  --active-learning-result data\active-learning\rangitoto\round-01-results.json `
  --baseline-checkpoint runs\rangitoto-r3d18-bootstrap\best.pt `
  --head-epochs 5 --layer4-epochs 5 `
  --head-learning-rate 0.001 `
  --layer4-learning-rate 0.00001 `
  --layer4-head-learning-rate 0.0001 `
  --device cuda --seed 42
```

State immediately below it: do not run until every row in every present train/val/test split has `match_id`, the val match is complete and independent, and the manifest passes all split-pair match-ID and video-content leakage checks.

- [ ] **Step 4: Document interpretation and promotion limits**

Explain that val Macro F1, per-class Precision/Recall/F1 and confusion matrix may choose a checkpoint, but remain tuning evidence. Promotion requires Macro F1 strictly above the same-val bootstrap, no lower mean F1 for receive/block/dig, and no recall drop over 0.05 for a class with support at least 5. Only the later untouched `test` match can report final product accuracy.

- [ ] **Step 5: Run all engineering verification**

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\ruff.exe check .
.venv\Scripts\python.exe -m compileall -q src tests
git diff --check
```

Expected: tests, lint, compilation and whitespace checks pass. Do not claim a new model checkpoint or accuracy result unless the independent val manifest actually exists and the real command completed.

- [ ] **Step 6: Commit the training documentation**

```powershell
git add README.md
git commit -m "docs: explain staged action model training"
```
