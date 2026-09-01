# SoCal Cup Independent Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a prediction-blind, full-match SoCal Cup validation workflow that freezes the source video, records complete C2 rally truth, runs segmented seven-class inference only after truth is locked, and publishes reproducible event- and window-level metrics.

**Architecture:** Add a validation-only contract layer beside the existing training and Rangitoto active-learning code. The contract layer owns source hashes, rally coverage, truth JSON, and CSV projection; segmented inference consumes only a locked truth bundle and preserves absolute-time/half-court provenance. A deterministic evaluator performs strict same-label event matching plus a separate all-label confusion pass, and an atomic output writer binds every result to the video, truth, checkpoint, parameters, and source code.

**Tech Stack:** Python 3.10+, existing OpenCV/PyTorch stack, standard-library csv/json/hashlib/dataclasses, existing unittest suite, Ruff, and the already-pinned center-nearest-frame-v1 sampling contract. No new runtime dependency is required.

## Global Constraints

- Execute from the clean codex/rangitoto-active-learning-round-01 baseline at commit 7a3a05352076e9b0302379a9f2e0f415661f2f6e; create branch codex/socal-independent-validation before implementation. Do not implement on the root codex/rangitoto-review checkout because it lacks the active-learning trust-chain baseline.
- When the source video is outside the implementation worktree, pass an explicit video_root (for this machine, E:/Spike-Trace) to every freeze, truth, proxy, isolation, and evaluation command; never copy or silently discover the multi-gigabyte source.
- The frozen source is data/SoCal Cup Final_ MVVC 17 Red vs C2 Attack 17-1, 06_15_2025 [9ESOXojmAGI].mp4, match ID socal-cup-final-2025, and SHA-256 b29e55cde114f5fda745349f86cc878d8abb81ba44ee430f467885bd7ce11c17.
- C2 Attack 17-1 Elite is the only target team; far and near are camera crop positions and must be mapped per set and side-switch interval. player_number remains empty in every validation artifact.
- SoCal is val only. Its video path, match ID, content SHA, truth JSON, prediction files, and reports must never be appended to a training manifest, active-learning selection source, pseudo-label source, or training cache.
- No model prediction, confidence, candidate ranking, or predicted event may be shown or consumed while the truth draft is being authored. The inference command must reject a draft and accept only a locked truth bundle.
- The model label contract remains exactly background, serve, receive, set, attack, block, dig; raw truth may additionally use free_ball, which projects to background only in the compatibility CSV and metric labels.
- Users enter action times at whole-second precision. Preserve original candidate boundaries and absolute times, but reject manually entered action times with fractional seconds; never infer a hidden action from a referee gesture, scoreboard, or context.
- fully_occluded, off_camera, and unresolved are visibility evidence intervals, not visible action positives and not background negatives. non_rally and unusable coverage is excluded from visible-action denominators.
- The compatibility CSV has exactly this ordered header: video_path,start_seconds,end_seconds,label,team_side,player_number,crop_x1,crop_y1,crop_x2,crop_y2,split,match_id,rally_id. It contains no prediction, confidence, review-status, checkbox, or separate free_ball column.
- All durable JSON/CSV/bundle publication is deterministic, atomic, and no-overwrite. A changed source creates a new versioned artifact; an existing destination is never replaced.
- Use unittest rather than pytest, keep existing public infer/manifest contracts backward compatible, and update README.md plus docs/PROJECT_PLAN.md whenever a module, command, directory, or data contract changes.
- Do not add player detection, tracking, OCR, SQLite, frontend, user accounts, or formal volleyball statistics in this validation implementation.

## File and Responsibility Map

Create the following focused modules on top of the baseline branch:

- src/spiketrace/validation_contract.py: shared validation constants, ValidationVideoBinding, canonical JSON/hash helpers, explicit video-root resolution, and race-safe no-overwrite bytes publication.
- src/spiketrace/validation_rallies.py: deterministic motion-based candidate detection, complete coverage construction, set/side/crop interval mapping, rally queue validation, and silent proxy generation.
- src/spiketrace/validation_truth.py: prediction-blind draft creation, rally/action/visibility schema validation, locked authority JSON, and the seven-class compatibility CSV projection.
- src/spiketrace/validation_inference.py: multi-interval, multi-crop inference with absolute-time provenance and a locked-truth prerequisite; existing infer_video remains unchanged for ordinary runs.
- src/spiketrace/validation_evaluation.py: deterministic event matching, one-second window expansion, visibility/coverage accounting, per-set reports, and confusion diagnostics.
- src/spiketrace/validation_outputs.py: atomic five-file validation result publication and independent verification.
- tests/test_validation_contract.py, tests/test_validation_rallies.py, tests/test_validation_truth.py, tests/test_validation_inference.py, tests/test_validation_evaluation.py, tests/test_validation_outputs.py, tests/test_validation_cli.py, and tests/test_socal_validation_integration.py: isolated unit and integration coverage for each contract.

Modify only these existing files:

- src/spiketrace/errors.py for ValidationError.
- src/spiketrace/domain.py and src/spiketrace/manifest.py to retain optional match_id/rally_id without breaking older manifests.
- src/spiketrace/video.py and src/spiketrace/inference.py to expose range-window and reusable batch primitives while preserving existing output format 2.
- src/spiketrace/cli.py for explicit freeze, queue, truth, isolation, evaluation, and verification commands.
- README.md and docs/PROJECT_PLAN.md for structure, commands, status, and the next manual truth-entry step.

The only intended SoCal data artifacts are small metadata/truth/report files under data/validation/ and outputs/validation/; the multi-gigabyte source video and generated proxy MP4 files remain local and are not copied into Git.

## Execution Bootstrap

Before Task 1, create the implementation worktree from the pushed active-learning baseline and bring the confirmed design and this plan into it:

~~~powershell
git fetch origin
git worktree add .worktrees/socal-independent-validation -b codex/socal-independent-validation codex/rangitoto-active-learning-round-01
git -C .worktrees/socal-independent-validation cherry-pick 0448ee1
git -C .worktrees/socal-independent-validation status --short --branch
~~~

The plan file is already committed in the current checkout when execution begins; if it is not present in the new worktree, copy it by committing the current plan first and cherry-picking that commit identified with git log --all -- docs/superpowers/plans/2026-09-01-socal-cup-independent-validation.md. The resulting worktree must report branch codex/socal-independent-validation, baseline code 7a3a053..., and no uncommitted implementation changes before Task 1.

---

### Task 1: Freeze the source contract and enforce split isolation

**Files:**
- Create: src/spiketrace/validation_contract.py
- Modify: src/spiketrace/errors.py
- Modify: src/spiketrace/domain.py
- Modify: src/spiketrace/manifest.py
- Create: tests/test_validation_contract.py
- Modify: tests/test_manifest.py
- Modify: README.md and docs/PROJECT_PLAN.md (record the new contract module in the same commit)

**Interfaces:**
- Produces: ValidationError(SpikeTraceError).
- Produces: sha256_file(path: str | Path) -> str and canonical_json_bytes(value: object) -> bytes.
- Produces: write_new_bytes(path: Path, payload: bytes, *, error_type: type[Exception] = ValidationError) -> None, which publishes through a sibling temporary file, os.fsync, and os.link; it raises if the destination exists or loses a concurrent race.
- Produces: ValidationVideoBinding(match_id: str, video_path: Path, video_root: Path, repo_video_path: str, sha256: str, metadata: VideoMetadata); repo_video_path is relative to video_root, never an absolute machine path.
- Produces: freeze_video_binding(video_path: str | Path, *, match_id: str, expected_sha256: str, repo_root: str | Path, video_root: str | Path | None = None, expected_metadata: Mapping[str, int | float] | None = None) -> ValidationVideoBinding.
- Produces: write_video_binding(path: str | Path, binding: ValidationVideoBinding, *, repo_root: str | Path) -> Path and load_video_binding(path: str | Path, *, repo_root: str | Path, video_root: str | Path | None = None) -> ValidationVideoBinding.
- Produces: assert_no_content_overlap(binding: ValidationVideoBinding, *, manifest_paths: Sequence[str | Path], selection_paths: Sequence[str | Path], repo_root: str | Path, video_root: str | Path | None = None) -> None.
- Modifies AnnotationRecord by appending optional match_id: str | None = None and rally_id: str | None = None; older positional constructors remain valid.
- Modifies load_manifest() to read those optional columns while retaining the existing required five columns and path-based leakage check.

- [ ] **Step 1: Write failing source-binding and race tests**

Create a one-second synthetic AVI fixture and assert the exact contract:

~~~
binding = freeze_video_binding(
    video,
    match_id="socal-cup-final-2025",
    expected_sha256=sha256_file(video),
    repo_root=root,
    expected_metadata={"fps": 10.0, "frame_count": 10, "width": 32, "height": 24},
)
self.assertEqual(binding.match_id, "socal-cup-final-2025")
self.assertEqual(binding.repo_video_path, "data/fixture.avi")
self.assertEqual(binding.metadata.frame_count, 10)

with self.assertRaisesRegex(ValidationError, "SHA-256"):
    freeze_video_binding(video, match_id="socal-cup-final-2025", expected_sha256="0" * 64, repo_root=root)

destination.write_bytes(b"winner")
with self.assertRaises(ValidationError):
    write_new_bytes(destination, b"loser")
self.assertEqual(destination.read_bytes(), b"winner")
~~~

Add isolation cases for a copied video under a different filename, a different path with the same match_id, and a selection JSON containing the validation SHA. Each must raise ValidationError; an unrelated manifest and unrelated selection JSON must pass.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

~~~powershell
.venv\Scripts\python.exe -m unittest tests.test_validation_contract tests.test_manifest -v
~~~

Expected: import failure for ValidationError/validation_contract, and existing manifest tests remain green before the new assertions are enabled.

- [ ] **Step 3: Implement canonical source binding and fail-closed overlap scanning**

Implement freeze_video_binding with inspect_video, exact SHA-256 comparison, POSIX path normalization relative to the explicit video_root (defaulting to repo_root), and metadata comparisons (fps/duration_seconds tolerance 1e-6; integer fields exact). Reject empty/non-hex match IDs, non-64-character hashes, missing files, and paths outside video_root. Store only the relative path and never serialize an absolute video_root. write_video_binding serializes only format_version, match_id, relative video_path, sha256, and metadata; load_video_binding requires the caller to supply the effective video_root and re-hashes the resolved source before returning.

assert_no_content_overlap must inspect only the explicitly passed files. For CSV manifests, parse video_path, split, match_id, and optional video_sha256; resolve paths through video_root, hash existing referenced videos, and reject either matching match_id or matching content SHA. For JSON selection sources, recursively inspect match_id, video_sha256, sha256, and video.path; reject a validation match/SHA/path and raise on an unreadable explicit source instead of silently skipping it. Do not scan directories or glob outputs implicitly.

Use the exact optional record parsing below so old manifests still load:

~~~
record = AnnotationRecord(
    video_path=resolved_video,
    start_seconds=start,
    end_seconds=end,
    label=label,
    split=split,
    team_side=_optional_text(row.get("team_side")),
    player_number=_optional_text(row.get("player_number")),
    crop=_optional_crop(row, row_number),
    match_id=_optional_text(row.get("match_id")),
    rally_id=_optional_text(row.get("rally_id")),
)
~~~

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run:

~~~powershell
.venv\Scripts\python.exe -m unittest tests.test_validation_contract tests.test_manifest -v
~~~

Expected: all new hash, metadata, renamed-copy, match-ID, selection-source, optional-column, and atomic-publication tests pass, with the pre-existing manifest suite unchanged.

- [ ] **Step 5: Commit the source-contract boundary**

~~~powershell
git add src/spiketrace/validation_contract.py src/spiketrace/errors.py src/spiketrace/domain.py src/spiketrace/manifest.py tests/test_validation_contract.py tests/test_manifest.py README.md docs/PROJECT_PLAN.md
git commit -m "feat: add validation source isolation contract"
~~~

---

### Task 2: Build the complete rally queue and side-switch mapping

**Files:**
- Create: src/spiketrace/validation_rallies.py
- Create: tests/test_validation_rallies.py
- Modify: README.md and docs/PROJECT_PLAN.md (record the queue/proxy module in the same commit)

**Interfaces:**
- Produces RallyDetectionSettings(sample_seconds: float = 0.5, motion_threshold: float = 12.0, dead_ball_seconds: float = 2.0, merge_gap_seconds: float = 1.0, buffer_before_seconds: float = 3.0, buffer_after_seconds: float = 3.0).
- Produces RallySegment(segment_id: str, source_segment_id: str | None, set_index: int | None, rally_id: str, start_seconds: float, end_seconds: float, status: Literal["pending", "rally", "non_rally", "unusable"], team_side: Literal["near", "far"] | None, crop: tuple[int, int, int, int] | None, buffer_before_seconds: float, buffer_after_seconds: float, boundary_source: Literal["motion", "manual"], coverage_confirmed: bool, all_c2_actions_checked: bool, no_c2_action: bool | None).
- Produces detect_rally_candidates(video_path: str | Path, *, settings: RallyDetectionSettings) -> tuple[tuple[float, float], ...].
- Produces complete_coverage(candidates: Sequence[tuple[float, float]], *, duration_seconds: float, binding: ValidationVideoBinding) -> tuple[RallySegment, ...].
- Produces apply_side_map(segments: Sequence[RallySegment], *, set_intervals: Sequence[Mapping[str, object]], side_intervals: Sequence[Mapping[str, object]], metadata: VideoMetadata) -> tuple[RallySegment, ...].
- Produces validate_rally_queue(segments: Sequence[RallySegment], *, binding: ValidationVideoBinding, require_complete: bool = False) -> None.
- Produces write_rally_queue(path: str | Path, *, binding: ValidationVideoBinding, segments: Sequence[RallySegment], set_intervals: Sequence[Mapping[str, object]], side_intervals: Sequence[Mapping[str, object]], settings: RallyDetectionSettings, code_sha: str) -> Path and load_rally_queue(path: str | Path, *, binding: ValidationVideoBinding) -> tuple[RallySegment, ...].
- Produces write_rally_proxies(queue: Sequence[RallySegment], output_dir: str | Path, *, video_root: str | Path | None = None, repo_root: str | Path, output_fps: float = 15.0, max_width: int = 960) -> dict[str, object], reusing video.write_proxy_video and never writing an audio track.

- [ ] **Step 1: Write failing candidate, coverage, and side-switch tests**

Use a synthetic video whose downsampled frames alternate between a stable image and a high-motion image. Assert that active runs are separated at a two-second dead-ball gap, buffers are clamped to [0, duration], and complete_coverage inserts explicit non_rally segments rather than leaving holes.

Add these exact validation checks:

~~~
segments = complete_coverage(((2.0, 5.0), (8.0, 10.0)), duration_seconds=12.0, binding=binding)
validate_rally_queue(segments, binding=binding, require_complete=True)
self.assertEqual([(s.start_seconds, s.end_seconds) for s in segments], [(0.0, 2.0), (2.0, 5.0), (5.0, 8.0), (8.0, 10.0), (10.0, 12.0)])
self.assertEqual(segments[0].status, "non_rally")

with self.assertRaisesRegex(ValidationError, "overlap"):
    validate_rally_queue((*segments[:2], replace(segments[2], start_seconds=4.5)), binding=binding)

mapped = apply_side_map(
    (replace(segments[1], set_index=5, rally_id="set-05-rally-001"),),
    set_intervals=[{"set_index": 5, "start_seconds": 1.0, "end_seconds": 12.0}],
    side_intervals=[{"segment_id": "set-05-pre", "set_index": 5, "start_seconds": 1.0, "end_seconds": 6.0, "team_side": "near", "crop": [0, 500, 1920, 1080]}, {"segment_id": "set-05-post", "set_index": 5, "start_seconds": 6.0, "end_seconds": 12.0, "team_side": "far", "crop": [0, 0, 1920, 580]}],
    metadata=metadata,
)
self.assertEqual(mapped[0].team_side, "near")
~~~

Test an interval crossing 6.0 seconds is rejected instead of silently inheriting one crop, and test proxy output is absent after a simulated decode failure.

- [ ] **Step 2: Run the rally tests and verify RED**

~~~powershell
.venv\Scripts\python.exe -m unittest tests.test_validation_rallies -v
~~~

Expected: import failure for validation_rallies symbols.

- [ ] **Step 3: Implement deterministic motion candidates and full coverage**

Sample one BGR frame every sample_seconds, resize to 64x36, convert to grayscale, and compute the mean absolute difference from the previous sample. Mark samples active when the difference is at least motion_threshold; merge active runs separated by less than merge_gap_seconds; close a run after dead_ball_seconds of inactive samples. Expand each candidate by the configured buffers and clamp to source duration. This is a review queue generator, not a truth source; write boundary_source="motion" and leave coverage_confirmed=False.

complete_coverage must sort candidates, reject negative/zero/out-of-bounds intervals, create stable rally-000001 IDs for candidates, and fill every gap with stable non-rally-000001 segments. Adjacent endpoints may touch; overlaps and gaps after normalization are errors when require_complete=True.

apply_side_map must require exactly one set interval and one side interval for every final rally, require a single near/far crop fully inside video geometry, and split any candidate that crosses a side boundary at the boundary rather than assigning the wrong crop. Preserve the original segment in source_segment_id in the serialized queue.

- [ ] **Step 4: Implement proxy generation and run the rally tests**

Serialize a queue with format_version=1, the frozen binding, detection settings, set intervals, side intervals, and ordered coverage segments. write_rally_queue uses write_new_bytes and rejects a second write to the same queue path. write_rally_proxies resolves the binding’s relative path against the explicit video_root, writes clips/<segment_id>.mp4 only for pending/rally segments, calls write_proxy_video(..., output_fps=15.0, max_width=960, codec="mp4v"), records each proxy SHA and source bounds, and publishes proxy-manifest.json atomically. A pre-existing output directory or proxy path raises without deleting prior files.

Run:

~~~powershell
.venv\Scripts\python.exe -m unittest tests.test_validation_rallies -v
~~~

Expected: all candidate, endpoint, side-switch, crop, proxy metadata, and rollback tests pass.

- [ ] **Step 5: Commit the rally queue**

~~~powershell
git add src/spiketrace/validation_rallies.py tests/test_validation_rallies.py README.md docs/PROJECT_PLAN.md
git commit -m "feat: add validation rally coverage queue"
~~~

---

### Task 3: Author prediction-blind truth and lock the authority bundle

**Files:**
- Create: src/spiketrace/validation_truth.py
- Create: tests/test_validation_truth.py
- Modify: README.md and docs/PROJECT_PLAN.md (record the truth draft/lock module in the same commit)

**Interfaces:**
- Produces GroundTruthAction(action_ref: str, match_id: str, rally_id: str, label: str, projected_label: str, start_seconds: float, end_seconds: float, visibility: Literal["visible", "fully_occluded", "off_camera", "unresolved"], evidence: str, player_number: str | None, notes: str).
- Produces VisibilityInterval(event_ref: str, rally_id: str | None, kind: Literal["fully_occluded", "off_camera", "unresolved"], start_seconds: float, end_seconds: float, notes: str).
- Produces ValidationTruth(video: ValidationVideoBinding, set_intervals: tuple[Mapping[str, object], ...], side_intervals: tuple[Mapping[str, object], ...], coverage: tuple[RallySegment, ...], actions: tuple[GroundTruthAction, ...], visibility_events: tuple[VisibilityInterval, ...], annotation_version: str, locked: bool, locked_sha256: str | None, csv_sha256: str | None).
- Produces init_truth_draft(queue_json: str | Path, output_json: str | Path, *, code_sha: str) -> Path.
- Produces validate_truth_draft(draft_json: str | Path, *, binding: ValidationVideoBinding) -> ValidationTruth.
- Produces lock_truth_bundle(draft_json: str | Path, csv_path: str | Path, json_path: str | Path, *, binding: ValidationVideoBinding, repo_root: str | Path, code_sha: str, created_at: str) -> dict[str, Path].
- Produces load_locked_truth(json_path: str | Path, csv_path: str | Path, *, binding: ValidationVideoBinding) -> ValidationTruth and verify_truth_bundle(json_path: str | Path, csv_path: str | Path, *, binding: ValidationVideoBinding, repo_root: str | Path, video_root: str | Path | None = None) -> dict[str, object].

- [ ] **Step 1: Write failing draft/schema/CSV tests**

Build a queue fixture with one rally containing a serve and a free ball, one confirmed no-action rally, one non_rally segment, and one visibility interval. Assert that the draft contains no prediction-shaped fields and that these cases fail:

~~~
draft = init_truth_draft(queue_json, draft_json, code_sha="abc123")
payload = json.loads(draft.read_text(encoding="utf-8"))
self.assertNotIn("predictions", payload)
self.assertNotIn("confidence", json.dumps(payload))

payload["coverage"][0]["coverage_confirmed"] = True
payload["coverage"][0]["all_c2_actions_checked"] = True
payload["coverage"][0]["no_c2_action"] = False
payload["actions"] = [{
    "action_ref": "set-01-rally-001/action-001",
    "rally_id": "set-01-rally-001",
    "label": "free_ball",
    "start_seconds": 12,
    "end_seconds": 13,
    "visibility": "visible",
    "evidence": "direct_video",
    "player_number": None,
    "notes": "passive return",
}]
truth = validate_truth_draft(draft_json, binding=binding)
self.assertEqual(truth.actions[0].projected_label, "background")
~~~

Assert the CSV header is exactly the global ordered header, free-ball is written as background, split is val, match_id is fixed, and no-action/visibility-only rallies create no fabricated CSV rows. Add failures for fractional action time, duplicate action_ref, action outside rally, pending coverage, actions in non_rally, non-empty player number, invalid label, overlapping duplicate action, and changed source hash.

- [ ] **Step 2: Run truth tests and verify RED**

~~~powershell
.venv\Scripts\python.exe -m unittest tests.test_validation_truth -v
~~~

Expected: import failure for validation_truth symbols.

- [ ] **Step 3: Implement strict draft validation and raw/projection semantics**

Use exact root JSON fields format_version, state, video, set_intervals, side_intervals, coverage, actions, visibility_events, annotation, and integrity; reject duplicate keys and unknown root/record fields. Require state="draft" for init_truth_draft/validate_truth_draft and state="locked" plus a non-empty lock hash for load_locked_truth.

For each rally coverage segment require coverage_confirmed=True and all_c2_actions_checked=True; require no_c2_action=True exactly when its action list is empty. For non_rally/unusable, require no actions and do not synthesize background. Validate all absolute action times at whole-second precision and bounds [rally.start_seconds, rally.end_seconds]. Visibility intervals must be bounded, typed, and retained separately; they never become background rows.

Map only free_ball -> background; all other raw labels must be in ACTION_LABELS. Store match_id on every action, preserve evidence/notes, and normalize an absent player number to None.

- [ ] **Step 4: Implement immutable JSON authority and compatible CSV projection**

lock_truth_bundle must re-read and hash the frozen video before writing, canonicalize the validated authority payload with canonical_json_bytes, compute locked_sha256 over the payload without the integrity hashes, and write the final JSON plus CSV through write_new_bytes. CSV uses UTF-8 BOM, the exact ordered header, \n line endings, repository-relative POSIX video_path, integer-second times, all crop coordinates from the mapped side interval, split=val, and empty player_number. The JSON stores the raw free_ball, its projected_label, the source binding, all coverage and visibility records, created_at, code_sha, csv_sha256, and locked_sha256.

verify_truth_bundle must recompute both file hashes, revalidate the source binding, parse the CSV without changing row order, compare every row to the JSON projection, and reject any post-lock byte mutation or destination collision. It must return counts for coverage segments, visible actions, no-action rallies, and visibility intervals.

Run:

~~~powershell
.venv\Scripts\python.exe -m unittest tests.test_validation_truth -v
~~~

Expected: all prediction-blind, precision, free-ball, empty-rally, visibility, CSV-order, hash, and no-overwrite tests pass.

- [ ] **Step 5: Commit the truth authority contract**

~~~powershell
git add src/spiketrace/validation_truth.py tests/test_validation_truth.py README.md docs/PROJECT_PLAN.md
git commit -m "feat: add locked validation truth bundle"
~~~

---

### Task 4: Run locked-truth segmented inference with absolute provenance

**Files:**
- Modify: src/spiketrace/video.py
- Modify: src/spiketrace/inference.py
- Create: src/spiketrace/validation_inference.py
- Create: tests/test_validation_inference.py
- Modify: README.md and docs/PROJECT_PLAN.md (record the segmented inference module in the same commit)

**Interfaces:**
- Produces InferenceSegment(segment_id: str, set_index: int, start_seconds: float, end_seconds: float, team_side: Literal["near", "far"], crop: tuple[int, int, int, int]).
- Produces ValidationWindow(window_index: int, segment_id: str, set_index: int, team_side: str, start_seconds: float, end_seconds: float, action: str, confidence: float).
- Produces ValidationPrediction(prediction_id: str, segment_id: str, set_index: int, team_side: str, start_seconds: float, end_seconds: float, action: str, confidence: float, source_window_indices: tuple[int, ...]).
- Produces ValidationInferenceResult(windows: tuple[ValidationWindow, ...], predictions: tuple[ValidationPrediction, ...], settings: dict[str, object], checkpoint_sha256: str, video_sha256: str).
- Adds iter_window_times_range(start_seconds: float, end_seconds: float, *, window_seconds: float, stride_seconds: float) -> Iterator[tuple[float, float]] to video.py; existing iter_window_times(duration_seconds, ...) remains unchanged.
- Produces infer_locked_validation(video_path: str | Path, checkpoint_path: str | Path, truth: ValidationTruth, *, stride_seconds: float = 0.4, confidence_threshold: float = 0.5, merge_gap_seconds: float = 0.25, min_event_seconds: float = 0.2, batch_size: int = 8, device: str = "auto") -> ValidationInferenceResult.

- [ ] **Step 1: Write failing range, crop, lock, and provenance tests**

Patch the model loader with a deterministic constant model and use a tiny video. Assert range windows never cross segment boundaries, near/far crops are applied to their own intervals, and event IDs/provenance retain absolute times:

~~~
result = infer_locked_validation(video, checkpoint, locked_truth, device="cpu", stride_seconds=1.0)
self.assertTrue(all(w.start_seconds >= 10.0 and w.end_seconds <= 20.0 for w in result.windows if w.segment_id == "set-01-near"))
self.assertEqual(result.predictions[0].source_window_indices, (0,))
self.assertEqual(result.predictions[0].team_side, "near")

with self.assertRaisesRegex(ValidationError, "locked"):
    infer_locked_validation(video, checkpoint, draft_truth, device="cpu")
~~~

Add tests for out-of-order/overlapping segments, a crop outside frame geometry, exactly adjacent side intervals, changed video bytes during inference, changed checkpoint bytes during inference, and a source window at each segment endpoint. Keep the existing tests/test_inference.py ordinary full-video assertions unchanged.

- [ ] **Step 2: Run inference tests and verify RED**

~~~powershell
.venv\Scripts\python.exe -m unittest tests.test_validation_inference tests.test_inference -v
~~~

Expected: missing range helper/validation inference imports fail while the existing inference tests still pass.

- [ ] **Step 3: Add reusable range-window generation and preserve ordinary inference**

Implement iter_window_times_range with half-open range semantics: generate windows beginning at start_seconds, advance by stride_seconds, include a final clamped window ending at end_seconds, and reject non-finite/negative/out-of-order arguments. Refactor only the internal batch loop needed by validation; do not change infer_video settings, format-2 JSON, event IDs, or single-crop behavior.

- [ ] **Step 4: Implement locked multi-segment inference**

Derive inference segments from locked truth side intervals and covered rally/non_rally ranges; omit unusable ranges. Validate each crop against inspect_video, reject overlaps, and process each segment independently with iter_sequential_video_clip_batches so one crop cannot leak into another side. Load the checkpoint once, preserve absolute source times, and call merge_action_windows_with_provenance per segment. Prefix deterministic IDs with match_id, set index, and segment ID; flatten windows while remapping provenance indices.

Hash the video and checkpoint immediately before decoding and again after the last batch; raise ValidationError if either changes. Store sampling_contract, model version, device, all thresholds, segment/crop map, source hashes, and locked truth SHA in settings. The function accepts a ValidationTruth object and checks truth.locked is True before loading the model, making prediction-before-lock impossible through this public entry point.

- [ ] **Step 5: Run tests and commit segmented inference**

~~~powershell
.venv\Scripts\python.exe -m unittest tests.test_validation_inference tests.test_inference -v
~~~

Expected: all new range, side-switch, endpoint, mutation, lock-gate, and provenance tests pass and all existing inference tests remain green.

~~~powershell
git add src/spiketrace/video.py src/spiketrace/inference.py src/spiketrace/validation_inference.py tests/test_validation_inference.py README.md docs/PROJECT_PLAN.md
git commit -m "feat: add locked segmented validation inference"
~~~

---

### Task 5: Match events and compute visible/window validation metrics

**Files:**
- Create: src/spiketrace/validation_evaluation.py
- Create: tests/test_validation_evaluation.py
- Modify: README.md and docs/PROJECT_PLAN.md (record the metrics module in the same commit)

**Interfaces:**
- Produces EventMatch(prediction_id: str, truth_ref: str, predicted_label: str, truth_label: str, center_error_seconds: float, confidence: float).
- Produces EventMatchResult(matches: tuple[EventMatch, ...], false_positive_ids: tuple[str, ...], false_negative_refs: tuple[str, ...], diagnostic_confusion: tuple[EventMatch, ...]).
- Produces WindowSeries(starts: tuple[float, ...], targets: tuple[str, ...], predictions: tuple[str, ...], segment_ids: tuple[str, ...]).
- Produces ValidationReport(event_metrics: dict[str, object], window_metrics: dict[str, object], coverage_metrics: dict[str, object], visibility_metrics: dict[str, object], confusion_rows: tuple[dict[str, object], ...]) with to_dict() -> dict[str, object].
- Produces match_events(predictions: Sequence[ValidationPrediction], truth_actions: Sequence[GroundTruthAction], *, tolerance_seconds: float = 1.0) -> EventMatchResult.
- Produces expand_one_second_windows(truth: ValidationTruth, inference: ValidationInferenceResult) -> WindowSeries.
- Produces evaluate_validation(truth: ValidationTruth, inference: ValidationInferenceResult) -> ValidationReport.

- [ ] **Step 1: Write failing matching and metric tests**

Cover the exact edge cases:

~~~
result = match_events(
    predictions=(prediction("p1", "attack", 10.0, 0.60), prediction("p2", "attack", 10.4, 0.95)),
    truth_actions=(truth_action("t1", "attack", 10.0), truth_action("t2", "attack", 10.4)),
    tolerance_seconds=1.0,
)
self.assertEqual(len(result.matches), 2)

self.assertEqual(
    match_events((prediction("p", "attack", 10.0, 0.9),), (truth_action("t", "block", 10.0),)).matches,
    (),
)
self.assertEqual(len(match_events((prediction("p", "attack", 11.0, 0.9),), (truth_action("t", "attack", 10.0),)).matches), 1)
self.assertEqual(len(match_events((prediction("p", "attack", 11.01, 0.9),), (truth_action("t", "attack", 10.0),)).matches), 0)
~~~

Add a greedy-trap fixture where one prediction can match two truths but a second prediction can match only the first; assert maximum cardinality wins. Add equal-error ties and assert confidence then stable IDs decide. Test free-ball projection, a confirmed no-action rally producing background windows, non_rally/unusable exclusion, visibility exclusion, non-match false positives, per-set counts, and zero-support classes returning 0.0 rather than NaN.

- [ ] **Step 2: Run evaluation tests and verify RED**

~~~powershell
.venv\Scripts\python.exe -m unittest tests.test_validation_evaluation -v
~~~

Expected: import failure for validation_evaluation symbols.

- [ ] **Step 3: Implement deterministic strict and diagnostic matching**

Group visible truth actions and predictions by projected label, sort each group by (center_seconds, stable_id), and use a dynamic-programming matcher over the two sorted sequences. A state retains the lexicographically greatest score (matched_count, -total_error_microseconds, total_confidence_micro, -stable_id_signature). Matching edges exist only when absolute center error is at most 1.0 seconds; this gives maximum cardinality first, minimum total error second, then confidence and stable IDs without a greedy cardinality loss.

The primary result matches only equal projected labels. Unmatched predictions are false positives and unmatched visible truth actions are false negatives. For confusion diagnostics, run the same matcher over remaining events without the label grouping and retain truth_label -> predicted_label cells, including attack/block and receive/set swaps; this diagnostic pass never changes strict Precision/Recall/F1.

- [ ] **Step 4: Implement one-second windows and report aggregation**

Use absolute bins [k, k + 1) with k = floor(start_seconds). Include a bin only when its center lies in a confirmed rally interval and outside any visibility interval. Select the nearest visible truth action center in a bin, projecting free_ball to background; if none exists and the rally has no_c2_action=True, use background. Select the highest-confidence overlapping prediction, breaking ties by prediction ID; absent predictions are background. Never create bins for non_rally or unusable coverage.

Call existing classification_metrics with ACTION_LABELS for the window series. Event metrics report six active classes (serve, receive, set, attack, block, dig), support, strict Precision/Recall/F1, Macro F1, matched/false-positive/false-negative counts, false positives per minute, and per-set/per-side summaries. Coverage metrics report total/confirmed rallies, complete-action-check count, no-action count, and coverage seconds. Visibility metrics report interval count, seconds, and affected-rally count for each of fully_occluded, off_camera, and unresolved. Add non-rally prediction count separately from visible-action false positives.

- [ ] **Step 5: Run evaluation tests and commit metrics**

~~~powershell
.venv\Scripts\python.exe -m unittest tests.test_validation_evaluation -v
~~~

Expected: all matching, tie, tolerance, projection, exclusion, confusion, per-set, and zero-denominator tests pass.

~~~powershell
git add src/spiketrace/validation_evaluation.py tests/test_validation_evaluation.py README.md docs/PROJECT_PLAN.md
git commit -m "feat: add validation event and window metrics"
~~~

---

### Task 6: Publish reproducible outputs and explicit CLI gates

**Files:**
- Create: src/spiketrace/validation_outputs.py
- Modify: src/spiketrace/cli.py
- Create: tests/test_validation_outputs.py
- Create: tests/test_validation_cli.py
- Modify: README.md and docs/PROJECT_PLAN.md (record all validation commands and output files in the same commit)

**Interfaces:**
- Produces write_validation_outputs(output_dir: str | Path, *, truth: ValidationTruth, inference: ValidationInferenceResult, report: ValidationReport, checkpoint_path: str | Path, code_sha: str, parameters: Mapping[str, object], created_at: str) -> dict[str, Path].
- Produces verify_validation_outputs(output_dir: str | Path, *, repo_root: str | Path, video_root: str | Path | None = None, require_source_files: bool = True) -> dict[str, object].
- Adds these parser commands with all source paths explicit:
  - freeze-validation-video VIDEO BINDING_JSON --repo-root ROOT --video-root VIDEO_ROOT --match-id ID --expected-sha256 SHA256.
  - prepare-validation-rallies BINDING_JSON QUEUE_JSON PROXY_DIR --repo-root ROOT --video-root VIDEO_ROOT --side-map SIDE_MAP_JSON.
  - init-validation-truth QUEUE_JSON DRAFT_JSON --code-sha SHA.
  - validate-validation-truth BINDING_JSON DRAFT_JSON --repo-root ROOT --video-root VIDEO_ROOT.
  - lock-validation-truth BINDING_JSON DRAFT_JSON TRUTH_JSON TRUTH_CSV --repo-root ROOT --video-root VIDEO_ROOT --code-sha SHA --created-at ISO8601.
  - verify-validation-truth BINDING_JSON TRUTH_JSON TRUTH_CSV --repo-root ROOT --video-root VIDEO_ROOT.
  - verify-validation-isolation BINDING_JSON --repo-root ROOT --video-root VIDEO_ROOT --manifest MANIFEST (repeatable) [--selection-source JSON (repeatable)].
  - evaluate-validation VIDEO TRUTH_JSON CHECKPOINT OUTPUT_DIR --repo-root ROOT --video-root VIDEO_ROOT --manifest MANIFEST (repeatable) [--selection-source JSON (repeatable)] plus the fixed inference options --stride-seconds, --confidence-threshold, --merge-gap-seconds, --min-event-seconds, --batch-size, and --device.
  - verify-validation OUTPUT_DIR --repo-root ROOT --video-root VIDEO_ROOT.

- [ ] **Step 1: Write failing output, CLI, and collision tests**

Construct a synthetic locked truth/inference/report and assert the exact five files and no-overwrite behavior:

~~~
paths = write_validation_outputs(output_dir, truth=truth, inference=inference, report=report, checkpoint_path=checkpoint, code_sha="abc", parameters={"stride_seconds": 0.4}, created_at="2026-09-01T00:00:00Z")
self.assertEqual(set(path.name for path in paths.values()), {"metrics.json", "confusion_matrix.csv", "predicted-events.json", "predicted-events.csv", "run-manifest.json"})
self.assertEqual(verify_validation_outputs(output_dir, repo_root=root)["match_id"], "socal-cup-final-2025")

with self.assertRaisesRegex(ValidationError, "already exists"):
    write_validation_outputs(output_dir, truth=truth, inference=inference, report=report, checkpoint_path=checkpoint, code_sha="abc", parameters={}, created_at="2026-09-01T00:00:00Z")
~~~

Add CLI parser/dispatch tests in tests/test_validation_cli.py for every command, a draft passed to evaluate-validation (must fail before model loading), stale truth/video/checkpoint hashes, a modified prediction CSV, a partial output directory, and a simulated atomic publication failure that leaves no staging directory.

- [ ] **Step 2: Run output/CLI tests and verify RED**

~~~powershell
.venv\Scripts\python.exe -m unittest tests.test_validation_outputs tests.test_validation_cli -v
~~~

Expected: import failure for validation_outputs and absent validation command parsers.

- [ ] **Step 3: Implement the five-file atomic result bundle**

Write exactly:

~~~text
outputs/validation/socal-cup-c2-baseline/
├─ metrics.json
├─ confusion_matrix.csv
├─ predicted-events.json
├─ predicted-events.csv
└─ run-manifest.json
~~~

metrics.json is ValidationReport.to_dict() plus format version and truth/checkpoint/source bindings. confusion_matrix.csv uses ordered columns scope,true_label,predicted_label,count. predicted-events.json retains segment, set, side, absolute times, confidence, and source window indices; its CSV projection has deterministic field order and no truth labels. run-manifest.json records video SHA, match ID, locked truth JSON/CSV SHA, checkpoint path/SHA, code SHA, sampling/model parameters, output file SHA/byte counts, and the caller-supplied generation timestamp. Passing a fixed created_at reproduces identical bytes; the CLI supplies UTC once per run.

Build all bytes in a sibling staging directory, fsync each file, verify hashes and cross-file counts, then publish with a no-overwrite directory commit. Reject an existing destination before any source read and clean staging on every exception. verify_validation_outputs must independently parse all five files, recompute hashes, verify source binding and locked truth, and report the run identity without running a model.

- [ ] **Step 4: Implement CLI orchestration and hard gates**

freeze-validation-video calls freeze_video_binding and publishes a small binding JSON. prepare-validation-rallies calls the motion queue and proxy writer and never imports/loading PyTorch. init/validate/lock/verify-validation-truth call only truth functions and never read checkpoints or prediction outputs. verify-validation-isolation calls assert_no_content_overlap for exactly the paths supplied by repeated --manifest/--selection-source options.

evaluate-validation must execute this order and abort on the first failed check:

~~~text
load locked truth JSON+CSV
→ re-hash source video and validate match/SHA/metadata
→ assert no explicit train/selection source overlap
→ load checkpoint and run infer_locked_validation
→ evaluate_validation
→ write_validation_outputs (no-overwrite)
~~~

It must not discover manifests, selections, checkpoints, or videos by walking directories. run_command returns JSON-serializable paths/counts, and main converts ValidationError to the existing actionable CLI error format.

- [ ] **Step 5: Run focused/full tests and commit the CLI/output layer**

~~~powershell
.venv\Scripts\python.exe -m unittest tests.test_validation_outputs tests.test_validation_cli tests.test_validation_truth tests.test_validation_inference -v
.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~

Expected: all validation tests and the complete pre-existing suite pass; the only permitted platform-specific skip is the existing Windows symlink privilege test.

~~~powershell
git add src/spiketrace/validation_outputs.py src/spiketrace/cli.py tests/test_validation_outputs.py tests/test_validation_cli.py README.md docs/PROJECT_PLAN.md
git commit -m "feat: publish isolated validation runs"
~~~

---

### Task 7: Materialize the SoCal binding and synchronize project documentation

**Files:**
- Create: data/validation/socal_cup_c2_video.json
- Create: data/validation/README.md
- Modify: README.md
- Modify: docs/PROJECT_PLAN.md
- Create: tests/test_socal_validation_integration.py

**Interfaces:**
- Produces the committed source-binding metadata only; it does not commit the 2.5 GB video, generated proxies, draft truth, or baseline predictions.
- Produces an integration test that exercises the complete command contracts on a tiny synthetic video and proves the SoCal-specific binding constants are exact.

- [ ] **Step 1: Write the integration and documentation assertions**

Add tests that load data/validation/socal_cup_c2_video.json, assert the exact match ID, path, dimensions, FPS, frame count, duration, and SHA from the design, assert README/PROJECT_PLAN contain C2 Attack 17-1 Elite, the new module names, five output filenames, the explicit no-prediction-before-lock rule, and the val-only isolation rule, and add a synthetic end-to-end test that runs freeze → queue → draft → lock → evaluate with a fake checkpoint and verifies the output directory.

- [ ] **Step 2: Run integration tests and verify RED**

~~~powershell
.venv\Scripts\python.exe -m unittest tests.test_socal_validation_integration -v
~~~

Expected: missing binding/documentation markers fail before the artifacts and edits are added.

- [ ] **Step 3: Add the frozen SoCal binding metadata**

Write this exact source binding (with repository-relative POSIX path and no generated prediction fields):

~~~json
{
  "format_version": 1,
  "match_id": "socal-cup-final-2025",
  "video_path": "data/SoCal Cup Final_ MVVC 17 Red vs C2 Attack 17-1, 06_15_2025 [9ESOXojmAGI].mp4",
  "sha256": "b29e55cde114f5fda745349f86cc878d8abb81ba44ee430f467885bd7ce11c17",
  "metadata": {"width": 1920, "height": 1080, "fps": 60.0, "frame_count": 294604, "duration_seconds": 4910.066666666667}
}
~~~

The runtime loader must compare this metadata against the real local file before accepting it; the committed JSON is a declaration, not a substitute for hashing the source.

- [ ] **Step 4: Synchronize README and project plan**

Update the README structure tree with the six validation modules, eight validation test modules, data/validation/, and outputs/validation/. Add the exact PowerShell workflow for the nine CLI commands, show the fixed SoCal source/checkpoint paths, explain that the user’s next manual task is to inspect/edit the prediction-blind draft one rally at a time, and state that no baseline recognition has run until the locked truth bundle exists. Update docs/PROJECT_PLAN.md to mark independent SoCal validation as the current Stage A deliverable, preserve the later test/identity/database/frontend stages, and repeat the match-ID/content-SHA isolation rule.

- [ ] **Step 5: Run final verification and commit documentation**

~~~powershell
.venv\Scripts\python.exe -m unittest tests.test_socal_validation_integration -v
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe -m compileall -q src tests
.venv\Scripts\ruff.exe check src tests
git diff --check
~~~

Expected: all tests, compilation, Ruff, and whitespace checks pass; the integration report states that truth is still unlocked and no SoCal prediction output was generated during this implementation pass.

~~~powershell
git add data/validation/socal_cup_c2_video.json data/validation/README.md README.md docs/PROJECT_PLAN.md tests/test_socal_validation_integration.py
git commit -m "docs: document SoCal independent validation workflow"
~~~

---

## Execution Order and Manual Handoff

Implement Tasks 1–7 in order. Each task has its own tests and commit; do not start the next task with a failing previous-task suite. After Task 2, the user may inspect and correct the generated rally queue and side map. After Task 3, the user must fill every rally segment’s whole-second action list or explicitly set no_c2_action=true; only then may Task 6’s evaluate-validation command run.

The first real run uses:

~~~powershell
.venv\Scripts\spiketrace.exe freeze-validation-video "data\SoCal Cup Final_ MVVC 17 Red vs C2 Attack 17-1, 06_15_2025 [9ESOXojmAGI].mp4" data\validation\socal_cup_c2_binding.json --repo-root . --video-root E:\Spike-Trace --match-id socal-cup-final-2025 --expected-sha256 b29e55cde114f5fda745349f86cc878d8abb81ba44ee430f467885bd7ce11c17
~~~

Then prepare the queue and prediction-blind draft. Do not run evaluate-validation or infer on SoCal until the locked files data/validation/socal_cup_c2_validation.json and data/validation/socal_cup_c2_validation.csv have passed verify-validation-truth and the isolation gate. The first baseline output belongs only in outputs/validation/socal-cup-c2-baseline/; later checkpoints use a new directory and are labeled val results, never final test accuracy.

## Plan Self-Review

- Spec coverage: Tasks 1–2 cover source freeze, candidate rallies, complete coverage, set/side/crop mapping, and proxy clips; Task 3 covers prediction-blind truth, no-action rallies, free-ball projection, visibility evidence, and immutable CSV/JSON; Tasks 4–5 cover segmented inference, absolute-time provenance, deterministic matching, one-second windows, confusion, per-set, and visibility metrics; Task 6 covers output hashes, atomic publication, CLI gates, and explicit source lists; Task 7 covers real SoCal metadata and README/PROJECT_PLAN synchronization.
- Placeholder scan: Every task names files, symbols, test commands, expected outcomes, and commit boundaries; no unspecified implementation step remains.
- Type consistency: ValidationVideoBinding is produced by Task 1 and consumed by Tasks 2–3/6; RallySegment is produced by Task 2 and stored by Task 3; ValidationTruth is produced by Task 3 and is the required input to Task 4; ValidationInferenceResult is produced by Task 4 and consumed by Tasks 5–6; ValidationReport is produced by Task 5 and consumed by Task 6.
