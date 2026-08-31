from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path

from . import _active_learning_selection_artifact as _selection_artifact
from .dual_crop_review import verify_dual_crop_review_bytes as _verify_merged_bytes

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RESULT_TYPE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_REVIEW_ROOT_FIELDS = (
    "format", "format_version", "result_set_id", "review_set_key", "batch_id",
    "round_id", "selection", "workbook", "evidence_overrides", "video",
    "time_precision_seconds", "source_review_rows", "source_repairs",
    "action_observations", "outcome_observations", "visibility_observations",
    "action_participants", "normalization_audit",
)
_ACTION_FIELDS = (
    "action_ref", "clip_id", "source_action_slot", "source_row", "raw_values",
    "normalized_values", "review_label", "relative_start_seconds",
    "relative_end_seconds", "start_seconds", "end_seconds", "team_side",
    "visibility", "evidence_basis", "interval_scope", "background_scope",
    "side_inherited", "note", "source_reason", "source_repairs",
)
_SOURCE_ROW_FIELDS = (
    "action_ref", "clip_id", "source_action_slot", "source_row", "raw_values",
    "normalized_values", "background_scope", "side_inherited", "source_repairs",
)
_OUTCOME_FIELDS = (
    "outcome_ref", "related_action_refs", "outcome", "result_type",
    "evidence_basis", "status", "note",
)
_VISIBILITY_FIELDS = (
    "visibility_ref", "event_kind", "clip_id", "team_side", "start_seconds",
    "end_seconds", "interval_scope", "related_action_refs", "note", "source_reason",
)
_PARTICIPANT_FIELDS = (
    "action_ref", "track_id", "identity_ref", "player_number", "participation",
    "touch_status", "assignment_status", "assignment_confidence", "evidence",
)
_AUDIT_FIELDS = (
    "kind", "clip_id", "action_ref", "source_row", "raw_value",
    "normalized_value", "reason",
)
_LABELS = {"background", "serve", "receive", "set", "attack", "block", "dig", "free_ball"}
_VISIBILITY = {"direct_clear", "direct_partial", "fully_occluded", "off_camera", "unresolved"}
_EVIDENCE = {"direct_video", "referee_signal", "scoreboard", "sequence_context", "mixed"}
_OUTCOMES = {"continued", "point_won", "point_lost", "unknown"}
_OUTCOME_STATUS = {"observed_or_inferred", "unresolved"}
_EVENT_KINDS = {"occlusion", "off_camera"}
_INTERVAL_SCOPES = {"timed", "clip_bounds"}
_PARTICIPATION = {"primary_actor", "block_attempt", "support"}
_TOUCH_STATUS = {"touched", "no_touch", "unknown"}
_ASSIGNMENT_STATUS = {"confirmed", "candidate", "unresolved"}


@dataclass(frozen=True, slots=True)
class ArtifactBinding:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class VideoBinding:
    video_id: str
    path: str
    sha256: str
    fps: float
    frame_count: int
    width: int
    height: int
    duration_seconds: float
    crops: dict[str, tuple[int, int, int, int]]


@dataclass(frozen=True, slots=True)
class FrozenArtifact:
    absolute_path: Path
    repo_path: str
    raw: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class ReviewInputSnapshots:
    selection: FrozenArtifact
    review_input: FrozenArtifact
    workbook: FrozenArtifact
    evidence_overrides: FrozenArtifact
    merged_candidates: FrozenArtifact


@dataclass(frozen=True, slots=True)
class ReviewSourceHashes:
    selection_sha256: str
    workbook_sha256: str
    evidence_overrides_sha256: str
    review_input_sha256: str
    merged_candidates_sha256: str


@dataclass(frozen=True, slots=True)
class ValidatedReviewInput:
    result_set_id: str
    review_set_key: str
    batch_id: str
    round_id: str
    time_precision_seconds: int
    source_hashes: ReviewSourceHashes
    selection_binding: ArtifactBinding
    review_input_binding: ArtifactBinding
    workbook_binding: ArtifactBinding
    evidence_overrides_binding: ArtifactBinding
    merged_candidates_binding: ArtifactBinding
    video_binding: VideoBinding
    merged_candidates: dict[str, object]
    source_review_rows: tuple[dict[str, object], ...]
    source_repairs: tuple[dict[str, object], ...]
    action_observations: tuple[dict[str, object], ...]
    outcome_observations: tuple[dict[str, object], ...]
    visibility_observations: tuple[dict[str, object], ...]
    action_participants: tuple[dict[str, object], ...]
    normalization_audit: tuple[dict[str, object], ...]


def derive_result_set_id(
    batch_id: object,
    round_id: object,
    selection_sha256: object,
    workbook_sha256: object,
    evidence_overrides_sha256: object,
) -> str:
    batch = _text(batch_id, "batch_id")
    round_name = _text(round_id, "round_id")
    digest = hashlib.sha256(
        b"\0".join(
            value.encode("ascii")
            for value in (
                "spiketrace.active-review-observations", "2", batch, round_name, _hash(selection_sha256, "selection SHA-256"),
                _hash(workbook_sha256, "workbook SHA-256"),
                _hash(evidence_overrides_sha256, "evidence overrides SHA-256"),
            )
        )
    ).hexdigest()[:16]
    return f"{batch}/result-{digest}"


def verify_dual_crop_review_bytes(
    merged_bytes: bytes, csv_bytes: bytes | None = None
) -> dict[str, object]:
    return _verify_merged_bytes(merged_bytes, csv_bytes=csv_bytes)


def validate_merged_review_source_bytes(
    merged_bytes: bytes,
    *,
    merged_repo_path: str,
    repo_root: str | Path,
    require_video: bool,
) -> dict[str, object]:
    root = Path(repo_root).expanduser().resolve()
    repo_path = _repo_path(merged_repo_path, "merged JSON")
    _resolve(root, repo_path, "merged JSON")
    merged = _selection_artifact._load_json_bytes(
        merged_bytes, description="merged review source"
    )
    try:
        verify_dual_crop_review_bytes(merged_bytes)
        input_runs = _mapping(merged["input_runs"], "input_runs")
        far_settings = _mapping(_mapping(input_runs["far"], "far run")["settings"], "far settings")
        near_settings = _mapping(_mapping(input_runs["near"], "near run")["settings"], "near settings")
        checkpoint = _repo_path(far_settings["checkpoint"], "far checkpoint")
        checkpoint_sha256 = _hash(far_settings["checkpoint_sha256"], "far checkpoint SHA-256")
        if _repo_path(near_settings["checkpoint"], "near checkpoint") != checkpoint or _hash(near_settings["checkpoint_sha256"], "near checkpoint SHA-256") != checkpoint_sha256:
            raise ValueError("Far and near runs must pin the same checkpoint path and SHA-256.")
        video_sha256 = _hash(far_settings["video_sha256"], "far video SHA-256")
        if _hash(near_settings["video_sha256"], "near video SHA-256") != video_sha256:
            raise ValueError("Far and near runs must pin the same video SHA-256.")
        merged_video = _mapping(merged["video"], "merged video")
        video_path = _repo_path(merged_video["path"], "merged video path")
        source_video = _resolve(root, video_path, "source video")
        if source_video.exists():
            if _sha256_file(source_video) != video_sha256:
                raise ValueError("Source video SHA-256 does not match.")
        elif require_video:
            raise ValueError(f"Source video does not exist: {source_video}")
        checkpoint_path = _resolve(root, checkpoint, "checkpoint")
        checkpoint_file_checked = checkpoint_path.exists()
        if checkpoint_file_checked and _sha256_file(checkpoint_path) != checkpoint_sha256:
            raise ValueError("Checkpoint SHA-256 does not match.")
        audits = _mapping(_mapping(merged["settings"], "merged settings")["input_runs"], "settings.input_runs")
        source = {
            "merged_json": repo_path,
            "merged_json_sha256": hashlib.sha256(merged_bytes).hexdigest(),
            "checkpoint": checkpoint, "checkpoint_sha256": checkpoint_sha256,
            "inference_runs": {
                "far": _selection_artifact._source_audit(audits["far"], "far inference run", root),
                "near": _selection_artifact._source_audit(audits["near"], "near inference run", root),
            },
            "format_version": merged["format_version"],
            "merge_format_version": merged["merge_format_version"],
            "model_version": merged["model_version"],
        }
        video = {
            "video_id": Path(video_path).stem, "path": video_path, "sha256": video_sha256,
            "fps": merged_video["fps"], "frame_count": merged_video["frame_count"],
            "width": merged_video["width"], "height": merged_video["height"],
            "duration_seconds": merged_video["duration_seconds"],
            "crops": {"far": far_settings["crop"], "near": near_settings["crop"]},
        }
        return {"merged": merged, "source": source, "video": video, "verification": {"checkpoint_file_checked": checkpoint_file_checked}}
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"Invalid merged review source: {exc}") from exc


def validate_review_selection_payload(
    payload: object,
    verified_merged: dict[str, object],
    repo_root: str | Path,
    require_video: bool,
) -> dict[str, object]:
    selection = _mapping(payload, "selection")
    previous = selection.get("previous_selections")
    if previous:
        raise ValueError("V2 review inputs do not support previous_selections.")
    try:
        return _selection_artifact._validate_selection_payload(
            selection, Path(repo_root).expanduser().resolve(), require_video=require_video,
            verifier=verify_dual_crop_review_bytes, verified_merged=verified_merged,
        )
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(str(exc)) from exc


def load_review_selection_bytes(
    selection_bytes: bytes,
    *,
    merged_bytes: bytes,
    merged_repo_path: str,
    repo_root: str | Path,
    require_video: bool = True,
) -> dict[str, object]:
    payload = _selection_artifact._load_json_bytes(selection_bytes, description="selection JSON")
    source = _mapping(payload.get("source"), "selection source")
    if _repo_path(source.get("merged_json"), "source merged JSON") != _repo_path(merged_repo_path, "merged JSON"):
        raise ValueError("Selection merged JSON path does not match frozen merged artifact.")
    if _hash(source.get("merged_json_sha256"), "merged JSON SHA-256") != hashlib.sha256(merged_bytes).hexdigest():
        raise ValueError("Merged JSON SHA-256 does not match.")
    verified = validate_merged_review_source_bytes(
        merged_bytes, merged_repo_path=merged_repo_path, repo_root=repo_root,
        require_video=require_video,
    )
    return validate_review_selection_payload(payload, verified, repo_root, require_video)


def snapshot_review_sources_v2(
    review_input_path: str | Path, selection_path: str | Path, repo_root: str | Path
) -> ReviewInputSnapshots:
    root = Path(repo_root).expanduser().resolve()
    selection = _freeze(_resolve(root, selection_path, "selection JSON"), root, "selection JSON")
    review = _freeze(_resolve(root, review_input_path, "review input"), root, "review input")
    selection_payload = _selection_artifact._load_json_bytes(selection.raw, description="selection JSON")
    review_payload = _selection_artifact._load_json_bytes(review.raw, description="review input")
    source = _mapping(selection_payload.get("source"), "selection source")
    workbook = _binding_path(review_payload.get("workbook"), root, "workbook")
    overrides = _binding_path(review_payload.get("evidence_overrides"), root, "evidence overrides")
    merged = _freeze(_resolve(root, _repo_path(source.get("merged_json"), "source merged JSON"), "merged JSON"), root, "merged JSON")
    return ReviewInputSnapshots(selection, review, workbook, overrides, merged)


def load_review_sources_v2(
    snapshots: ReviewInputSnapshots, selection: dict[str, object]
) -> ValidatedReviewInput:
    review = _selection_artifact._load_json_bytes(snapshots.review_input.raw, description="review input")
    _exact(review, _REVIEW_ROOT_FIELDS, "review input")
    if review["format"] != "spiketrace.active-review-evidence-input" or type(review["format_version"]) is not int or review["format_version"] != 2:
        raise ValueError("Review input must use evidence input format version 2.")
    if type(review["time_precision_seconds"]) is not int or review["time_precision_seconds"] != 1:
        raise ValueError("time_precision_seconds must be 1.")
    batch_id = _text(review["batch_id"], "batch_id")
    round_id = _text(review["round_id"], "round_id")
    if batch_id != selection["batch_id"] or round_id != selection["round_id"]:
        raise ValueError("Review input batch or round does not match selection.")
    selection_binding = _binding(review["selection"], snapshots.selection, "selection")
    workbook_binding = _binding(review["workbook"], snapshots.workbook, "workbook")
    override_binding = _binding(review["evidence_overrides"], snapshots.evidence_overrides, "evidence overrides")
    merged_binding = ArtifactBinding(snapshots.merged_candidates.repo_path, snapshots.merged_candidates.sha256)
    expected_result = derive_result_set_id(batch_id, round_id, selection_binding.sha256, workbook_binding.sha256, override_binding.sha256)
    if review["result_set_id"] != expected_result:
        raise ValueError("result_set_id does not match frozen source hashes.")
    if (
        not isinstance(review["review_set_key"], str)
        or not re.fullmatch(r"[^/]+/round-[0-9]{2}", review["review_set_key"])
    ):
        raise ValueError("review_set_key must be nonempty text.")
    video_binding = _video_binding(review["video"])
    if review["video"] != selection["video"]:
        raise ValueError("Review input video does not match selection.")
    source_rows = _validate_source_rows(review["source_review_rows"])
    source_repairs = _list_of_objects(review["source_repairs"], "source_repairs")
    actions = _validate_actions(review["action_observations"], selection, source_rows)
    outcomes = _validate_outcomes(review["outcome_observations"], actions, expected_result)
    visibility = _validate_visibility(review["visibility_observations"], actions, expected_result)
    participants = _validate_participants(review["action_participants"], actions)
    audit = _validate_audit(review["normalization_audit"], actions)
    return ValidatedReviewInput(
        expected_result, review["review_set_key"], batch_id, round_id, 1,
        ReviewSourceHashes(selection_binding.sha256, workbook_binding.sha256, override_binding.sha256, snapshots.review_input.sha256, merged_binding.sha256),
        selection_binding, ArtifactBinding(snapshots.review_input.repo_path, snapshots.review_input.sha256), workbook_binding, override_binding, merged_binding, video_binding,
        _selection_artifact._load_json_bytes(snapshots.merged_candidates.raw, description="merged review source"),
        tuple(source_rows), tuple(source_repairs), tuple(actions), tuple(outcomes), tuple(visibility), tuple(participants), tuple(audit),
    )


def assert_review_snapshots_stable(snapshots: ReviewInputSnapshots) -> None:
    for label, snapshot in (("selection", snapshots.selection), ("review input", snapshots.review_input), ("workbook", snapshots.workbook), ("evidence overrides", snapshots.evidence_overrides), ("merged candidates", snapshots.merged_candidates)):
        try:
            current = snapshot.absolute_path.read_bytes()
        except OSError as exc:
            raise ValueError(f"{label} changed or became unavailable during review application.") from exc
        if current != snapshot.raw:
            raise ValueError(f"{label} changed during review application.")


def _validate_source_rows(value: object) -> list[dict[str, object]]:
    rows = _list_of_objects(value, "source_review_rows")
    seen: set[str] = set()
    slots: set[tuple[str, int]] = set()
    for row in rows:
        _exact(row, _SOURCE_ROW_FIELDS, "source review row")
        ref = _text(row["action_ref"], "source action_ref")
        if ref in seen:
            raise ValueError("source_review_rows contains duplicate action_ref.")
        seen.add(ref)
        _source_identity(row)
        slot = (row["clip_id"], row["source_action_slot"])
        if slot in slots:
            raise ValueError("source_review_rows contains duplicate source slot.")
        slots.add(slot)
    return rows


def _validate_actions(value: object, selection: dict[str, object], source_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    actions = _list_of_objects(value, "action_observations")
    clips = {clip["clip_id"]: clip for clip in selection["clips"]}
    source_by_ref = {row["action_ref"]: row for row in source_rows}
    seen: set[str] = set()
    supplemental: dict[str, list[int]] = {}
    for action in actions:
        _exact(action, _ACTION_FIELDS, "action observation")
        ref = _text(action["action_ref"], "action_ref")
        if ref in seen:
            raise ValueError("action_observations contains duplicate action_ref.")
        seen.add(ref)
        clip_id = _text(action["clip_id"], "clip_id")
        if clip_id not in clips:
            raise ValueError("action observation references an unknown clip.")
        label = action["review_label"]
        if label not in _LABELS:
            raise ValueError("review_label is invalid.")
        _enum(action["visibility"], _VISIBILITY, "visibility")
        _enum(action["evidence_basis"], _EVIDENCE, "evidence_basis")
        if type(action["side_inherited"]) is not bool or action["team_side"] not in {"far", "near"}:
            raise ValueError("action team_side or side_inherited is invalid.")
        _text(action["note"], "action note", allow_empty=True)
        _optional_text(action["source_reason"], "action source_reason")
        if not isinstance(action["raw_values"], dict) or not isinstance(action["normalized_values"], dict) or not isinstance(action["source_repairs"], list):
            raise TypeError("action values and repairs must be objects/arrays.")
        slot = action["source_action_slot"]
        row = action["source_row"]
        if slot is None or row is None:
            if slot is not None or row is not None or not ref.startswith(f"{clip_id}/supplemental-"):
                raise ValueError("supplemental actions need null source slot and row.")
            number = _suffix_number(ref, f"{clip_id}/supplemental-")
            supplemental.setdefault(clip_id, []).append(number)
        else:
            _source_identity(action)
            source = source_by_ref.get(ref)
            if source is None:
                raise ValueError("action observation source ref is dangling.")
            if any(action[field] != source[field] for field in _SOURCE_ROW_FIELDS):
                raise ValueError("action observation does not preserve its source row.")
        relative_start = action["relative_start_seconds"]
        relative_end = action["relative_end_seconds"]
        if label == "background" and action["background_scope"] == "clip_sentinel":
            if any(item is not None for item in (relative_start, relative_end, action["start_seconds"], action["end_seconds"], action["interval_scope"])):
                raise ValueError("clip-sentinel background must be untimed.")
        else:
            _whole(relative_start, "relative_start_seconds")
            _whole(relative_end, "relative_end_seconds")
            if relative_end <= relative_start:
                raise ValueError("action interval must be positive.")
            _finite(action["start_seconds"], "start_seconds")
            _finite(action["end_seconds"], "end_seconds")
            if action["end_seconds"] <= action["start_seconds"]:
                raise ValueError("action absolute interval must be positive.")
            if action["interval_scope"] not in _INTERVAL_SCOPES:
                raise ValueError("action interval_scope is invalid.")
            if action["start_seconds"] != clips[clip_id]["start_seconds"] + relative_start or action["end_seconds"] != clips[clip_id]["start_seconds"] + relative_end:
                raise ValueError("action absolute bounds do not equal clip-relative bounds.")
    for numbers in supplemental.values():
        if sorted(numbers) != list(range(1, len(numbers) + 1)):
            raise ValueError("supplemental action indexes must be contiguous per clip.")
    return actions


def _validate_outcomes(value: object, actions: list[dict[str, object]], result_id: str) -> list[dict[str, object]]:
    outcomes = _list_of_objects(value, "outcome_observations")
    action_map = {action["action_ref"]: action for action in actions}
    for index, outcome in enumerate(outcomes, 1):
        _exact(outcome, _OUTCOME_FIELDS, "outcome observation")
        if outcome["outcome_ref"] != f"{result_id}/outcome-{index:03d}":
            raise ValueError("outcome refs must be stable and contiguous.")
        refs = _refs(outcome["related_action_refs"], action_map, "outcome")
        _enum(outcome["outcome"], _OUTCOMES, "outcome")
        _enum(outcome["evidence_basis"], _EVIDENCE, "outcome evidence_basis")
        _enum(outcome["status"], _OUTCOME_STATUS, "outcome status")
        if outcome["result_type"] is not None and (not isinstance(outcome["result_type"], str) or not _RESULT_TYPE.fullmatch(outcome["result_type"])):
            raise ValueError("result_type is invalid.")
        if outcome["result_type"] == "free_ball_error" and not (outcome["outcome"] == "point_lost" and any(action_map[ref]["review_label"] == "free_ball" for ref in refs)):
            raise ValueError("free_ball_error requires point_lost related to a free_ball.")
        _text(outcome["note"], "outcome note", allow_empty=True)
    return outcomes


def _validate_visibility(value: object, actions: list[dict[str, object]], result_id: str) -> list[dict[str, object]]:
    observations = _list_of_objects(value, "visibility_observations")
    action_map = {action["action_ref"]: action for action in actions}
    counts = {"occlusion": 0, "off_camera": 0}
    for observation in observations:
        _exact(observation, _VISIBILITY_FIELDS, "visibility observation")
        kind = _enum(observation["event_kind"], _EVENT_KINDS, "event_kind")
        counts[kind] += 1
        if observation["visibility_ref"] != f"{result_id}/{kind}-source-{counts[kind]:03d}":
            raise ValueError("visibility refs must be stable and contiguous.")
        _finite(observation["start_seconds"], "visibility start_seconds")
        _finite(observation["end_seconds"], "visibility end_seconds")
        if observation["end_seconds"] <= observation["start_seconds"] or observation["interval_scope"] not in _INTERVAL_SCOPES:
            raise ValueError("visibility interval is invalid.")
        refs = _refs(observation["related_action_refs"], action_map, "visibility")
        for ref in refs:
            action = action_map[ref]
            if action["clip_id"] != observation["clip_id"] or action["team_side"] != observation["team_side"]:
                raise ValueError("visibility refs must use the same clip and side.")
        _text(observation["note"], "visibility note", allow_empty=True)
        _optional_text(observation["source_reason"], "visibility source_reason")
    for action in actions:
        if action["visibility"] not in {"fully_occluded", "off_camera"}:
            continue
        kind = "occlusion" if action["visibility"] == "fully_occluded" else "off_camera"
        if not any(
            observation["event_kind"] == kind
            and action["action_ref"] in observation["related_action_refs"]
            and observation["start_seconds"] <= action["start_seconds"]
            and observation["end_seconds"] >= action["end_seconds"]
            for observation in observations
        ):
            raise ValueError("action lacks matching visibility coverage.")
    return observations


def _validate_participants(value: object, actions: list[dict[str, object]]) -> list[dict[str, object]]:
    participants = _list_of_objects(value, "action_participants")
    refs = {action["action_ref"] for action in actions}
    for participant in participants:
        _exact(participant, _PARTICIPANT_FIELDS, "action participant")
        if participant["action_ref"] not in refs:
            raise ValueError("action participant has dangling action_ref.")
        _enum(participant["participation"], _PARTICIPATION, "participation")
        _enum(participant["touch_status"], _TOUCH_STATUS, "touch_status")
        status = _enum(participant["assignment_status"], _ASSIGNMENT_STATUS, "assignment_status")
        if not isinstance(participant["evidence"], list):
            raise TypeError("participant evidence must be an array.")
        if status == "confirmed":
            _finite(participant["assignment_confidence"], "confirmed assignment confidence")
            if not 0 <= participant["assignment_confidence"] <= 1:
                raise ValueError("confirmed assignment confidence must be in [0,1].")
        elif status == "unresolved" and (participant["identity_ref"] is not None or participant["player_number"] is not None):
            raise ValueError("unresolved assignment must not claim identity or player number.")
    return participants


def _validate_audit(value: object, actions: list[dict[str, object]]) -> list[dict[str, object]]:
    audit = _list_of_objects(value, "normalization_audit")
    refs = {action["action_ref"] for action in actions}
    for item in audit:
        _exact(item, _AUDIT_FIELDS, "normalization audit entry")
        _enum(item["kind"], {"read_only_repair", "side_inheritance"}, "normalization audit kind")
        if item["action_ref"] not in refs:
            raise ValueError("normalization audit has dangling action_ref.")
        _whole(item["source_row"], "normalization audit source_row")
        _text(item["reason"], "normalization audit reason")
    return audit


def _freeze(path: Path, root: Path, description: str) -> FrozenArtifact:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"Cannot read {description}: {path}") from exc
    return FrozenArtifact(path, _normalized(path, root), raw, hashlib.sha256(raw).hexdigest())


def _binding_path(value: object, root: Path, description: str) -> FrozenArtifact:
    binding = _mapping(value, description)
    _exact(binding, ("path", "sha256"), description)
    path = _resolve(root, _repo_path(binding["path"], f"{description} path"), description)
    frozen = _freeze(path, root, description)
    if frozen.sha256 != _hash(binding["sha256"], f"{description} SHA-256"):
        raise ValueError(f"{description} SHA-256 does not match.")
    return frozen


def _binding(value: object, snapshot: FrozenArtifact, description: str) -> ArtifactBinding:
    binding = _mapping(value, description)
    _exact(binding, ("path", "sha256"), description)
    path = _repo_path(binding["path"], f"{description} path")
    digest = _hash(binding["sha256"], f"{description} SHA-256")
    if path != snapshot.repo_path or digest != snapshot.sha256:
        raise ValueError(f"{description} binding does not match frozen artifact.")
    return ArtifactBinding(path, digest)


def _video_binding(value: object) -> VideoBinding:
    video = _mapping(value, "video")
    _exact(video, ("video_id", "path", "sha256", "fps", "frame_count", "width", "height", "duration_seconds", "crops"), "video")
    width = _whole(video["width"], "video width")
    height = _whole(video["height"], "video height")
    if width < 1 or height < 1:
        raise ValueError("video dimensions must be positive.")
    fps = _finite(video["fps"], "video fps")
    duration = _finite(video["duration_seconds"], "video duration_seconds")
    if fps <= 0 or duration <= 0 or _whole(video["frame_count"], "video frame_count") < 1:
        raise ValueError("video metadata must be positive.")
    crops = _mapping(video["crops"], "video crops")
    _exact(crops, ("far", "near"), "video crops")
    normalized: dict[str, tuple[int, int, int, int]] = {}
    for side in ("far", "near"):
        crop = crops[side]
        if not isinstance(crop, list) or len(crop) != 4:
            raise ValueError("video crop must have four coordinates.")
        x1, y1, x2, y2 = (_whole(item, f"{side} crop coordinate") for item in crop)
        if x1 < 0 or y1 < 0 or x1 >= x2 or y1 >= y2 or x2 > width or y2 > height:
            raise ValueError("video crop is outside frame bounds.")
        normalized[side] = (x1, y1, x2, y2)
    return VideoBinding(_text(video["video_id"], "video_id"), _repo_path(video["path"], "video path"), _hash(video["sha256"], "video SHA-256"), fps, _whole(video["frame_count"], "video frame_count"), width, height, duration, normalized)


def _source_identity(value: dict[str, object]) -> None:
    if type(value["source_action_slot"]) is not int or value["source_action_slot"] < 1 or type(value["source_row"]) is not int or value["source_row"] < 1:
        raise ValueError("source action slot and row must be positive integers.")


def _refs(value: object, actions: dict[str, dict[str, object]], description: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{description} related_action_refs must be nonempty.")
    if any(not isinstance(ref, str) or ref not in actions for ref in value) or len(value) != len(set(value)):
        raise ValueError(f"{description} contains dangling or duplicate action refs.")
    return value


def _suffix_number(ref: str, prefix: str) -> int:
    suffix = ref.removeprefix(prefix)
    if not re.fullmatch(r"\d{3}", suffix):
        raise ValueError("supplemental action ref is invalid.")
    return int(suffix)


def _list_of_objects(value: object, description: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise TypeError(f"{description} must be an array.")
    return [_mapping(item, description) for item in value]


def _exact(value: dict[str, object], fields: tuple[str, ...], description: str) -> None:
    if set(value) != set(fields) or len(value) != len(fields):
        raise ValueError(f"{description} fields must be exactly {fields}.")


def _mapping(value: object, description: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{description} must be an object.")
    return value


def _repo_path(value: object, description: str) -> str:
    try:
        return _selection_artifact._relative_posix_path(value, description)
    except Exception as exc:
        raise ValueError(str(exc)) from exc


def _resolve(root: Path, value: str | Path, description: str) -> Path:
    try:
        return _selection_artifact._resolved_path(value, root, description)
    except Exception as exc:
        raise ValueError(str(exc)) from exc


def _normalized(path: Path, root: Path) -> str:
    return _resolve(root, path, "artifact").relative_to(root).as_posix()


def _hash(value: object, description: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{description} must be a lowercase SHA-256.")
    return value


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text(value: object, description: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{description} must be nonempty text.")
    return value


def _optional_text(value: object, description: str) -> str | None:
    if value is None:
        return None
    return _text(value, description)


def _enum(value: object, values: set[str], description: str) -> str:
    if value not in values:
        raise ValueError(f"{description} is invalid.")
    return value  # type: ignore[return-value]


def _finite(value: object, description: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(value):
        raise ValueError(f"{description} must be finite number.")
    return float(value)


def _whole(value: object, description: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{description} must be an integer.")
    return value
