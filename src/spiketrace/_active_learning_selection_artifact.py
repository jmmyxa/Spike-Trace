from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from ._active_learning_selection_contract import (
    _ANCHOR_FIELDS,
    _COVERAGE_FIELDS,
    _HINT_FIELDS,
    _INFERENCE_RUN_FIELDS,
    _PREVIOUS_SELECTION_FIELDS,
    _QUOTA_FIELDS,
    _SHA256_LENGTH,
    _SOURCE_FIELDS,
    _TASK_TWO_CLIP_FIELDS,
    _TASK_TWO_SETTINGS_FIELDS,
    _VIDEO_FIELDS,
    MINORITY_ACTIONS,
    ROUND_ONE_QUOTAS,
    SELECTION_ROOT_FIELDS,
    _finite_number,
    _fit_clip_bounds,
    _integer_ms,
    _intervals_overlap,
    _mapping,
    _nonempty_string,
    _optional_string,
    _ordered_sides,
    _represented_sides,
    _require_exact_fields,
    _selection_settings_ms,
    _sides,
    _string_list,
    _validate_json_value,
    format_timecode,
)
from .dual_crop_review import verify_dual_crop_review
from .errors import ActiveLearningError
from .events import seconds_to_milliseconds


def _load_previous_selections(
    paths: Iterable[str | Path],
    *,
    repo_root: Path,
    current_video: dict[str, Any],
    verifier: Callable[..., object] = verify_dual_crop_review,
) -> tuple[list[dict[str, object]], list[tuple[int, int]]]:
    records: list[dict[str, object]] = []
    intervals: list[tuple[int, int]] = []
    seen_paths: set[str] = set()
    try:
        path_items = list(paths)
    except TypeError as exc:
        raise ActiveLearningError("previous_selection_paths must be iterable.") from exc
    for raw_path in path_items:
        path = _resolved_path(raw_path, repo_root, "previous selection")
        normalized = _normalized_path(path, repo_root, "previous selection")
        if normalized in seen_paths:
            raise ActiveLearningError("Previous selection paths must be unique.")
        seen_paths.add(normalized)
        previous = load_review_selection(path, repo_root=repo_root, _verifier=verifier)
        _require_same_video(previous["video"], current_video)
        for raw_clip in previous["clips"]:
            clip = _mapping(raw_clip, "previous selection clip")
            start_ms = seconds_to_milliseconds(
                _finite_number(clip["start_seconds"], "previous clip start_seconds")
            )
            end_ms = seconds_to_milliseconds(
                _finite_number(clip["end_seconds"], "previous clip end_seconds")
            )
            if end_ms <= start_ms:
                raise ActiveLearningError("Previous clip bounds are invalid.")
            intervals.append((start_ms, end_ms))
        records.append(
            {
                "path": normalized,
                "sha256": _sha256_file(path),
                "batch_id": previous["batch_id"],
                "round_id": previous["round_id"],
            }
        )
    intervals.sort()
    return records, intervals


def _selection_events(
    value: object,
    *,
    video_duration_ms: int,
    preferred_ms: int,
    min_ms: int,
    max_ms: int,
    time_strata: int,
    previous_intervals: list[tuple[int, int]],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ActiveLearningError("Merged events must be an array.")
    events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_event in enumerate(value, start=1):
        event = _mapping(raw_event, f"merged event {index}")
        event_id = _nonempty_string(event.get("event_id"), "canonical event ID")
        if event_id in seen_ids:
            raise ActiveLearningError("Canonical event IDs must be unique.")
        seen_ids.add(event_id)
        start_ms = _integer_ms(event.get("start_ms"), "event start_ms")
        end_ms = _integer_ms(event.get("end_ms"), "event end_ms")
        if start_ms < 0 or end_ms <= start_ms or end_ms > video_duration_ms:
            raise ActiveLearningError("Canonical event bounds are invalid.")
        action = _nonempty_string(event.get("action"), "event action")
        confidence = _finite_number(event.get("confidence"), "event confidence")
        if not 0 <= confidence <= 1:
            raise ActiveLearningError("Event confidence must be between 0 and 1.")
        observed_sides = _sides(event.get("observed_sides"), "observed_sides")
        source_ids = _string_list(
            event.get("source_event_ids"), "source candidate IDs", required=True
        )
        duplicate_id = _optional_string(
            event.get("duplicate_group_id"), "duplicate_group_id"
        )
        conflict_id = _optional_string(
            event.get("conflict_group_id"), "conflict_group_id"
        )
        try:
            clip_start_ms, clip_end_ms = _fit_clip_bounds(
                start_ms,
                end_ms,
                video_duration_ms=video_duration_ms,
                preferred_ms=preferred_ms,
                min_ms=min_ms,
                max_ms=max_ms,
            )
        except ActiveLearningError:
            if action not in MINORITY_ACTIONS:
                continue
            clip_start_ms = None
            clip_end_ms = None
        if clip_start_ms is not None and any(
            _intervals_overlap(clip_start_ms, clip_end_ms, old_start, old_end)
            for old_start, old_end in previous_intervals
        ):
            continue
        midpoint_ms = (start_ms + end_ms) // 2
        events.append(
            {
                "stable_id": event_id,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "action": action,
                "confidence": confidence,
                "observed_sides": observed_sides,
                "duplicate_group_id": duplicate_id,
                "conflict_group_id": conflict_id,
                "source_event_ids": source_ids,
                "clip_start_ms": clip_start_ms,
                "clip_end_ms": clip_end_ms,
                "time_stratum": min(
                    time_strata - 1,
                    midpoint_ms * time_strata // video_duration_ms,
                ),
            }
        )
    events.sort(key=lambda item: (item["start_ms"], item["end_ms"], item["stable_id"]))
    return events


def validate_merged_review_source(
    merged_json_path: str | Path,
    *,
    repo_root: str | Path,
    require_video: bool = True,
    _verifier: Callable[..., object] = verify_dual_crop_review,
) -> dict[str, object]:
    root = Path(repo_root).expanduser().resolve()
    merged_path = _resolved_path(merged_json_path, root, "merged JSON")
    try:
        merged_bytes = merged_path.read_bytes()
        if _verifier is verify_dual_crop_review:
            from ._active_learning_review_contract import (
                validate_merged_review_source_bytes,
            )

            return validate_merged_review_source_bytes(
                merged_bytes,
                merged_repo_path=_normalized_path(merged_path, root, "merged JSON"),
                repo_root=root,
                require_video=require_video,
            )
        merged = _load_json_bytes(merged_bytes, description="merged review source")
        _verifier(merged_path)
        input_runs = _mapping(merged["input_runs"], "input_runs")
        far_settings = _mapping(
            _mapping(input_runs["far"], "far run")["settings"], "far settings"
        )
        near_settings = _mapping(
            _mapping(input_runs["near"], "near run")["settings"], "near settings"
        )
        checkpoint = _relative_posix_path(far_settings["checkpoint"], "far checkpoint")
        checkpoint_sha256 = _sha256(
            far_settings["checkpoint_sha256"], "far checkpoint SHA-256"
        )
        if (
            _relative_posix_path(near_settings["checkpoint"], "near checkpoint")
            != checkpoint
            or _sha256(near_settings["checkpoint_sha256"], "near checkpoint SHA-256")
            != checkpoint_sha256
        ):
            raise ActiveLearningError(
                "Far and near runs must pin the same checkpoint path and SHA-256."
            )
        video_sha256 = _sha256(far_settings["video_sha256"], "far video SHA-256")
        if _sha256(near_settings["video_sha256"], "near video SHA-256") != video_sha256:
            raise ActiveLearningError(
                "Far and near runs must pin the same video SHA-256."
            )

        merged_video = _mapping(merged["video"], "merged video")
        normalized_video_path = _relative_posix_path(
            merged_video["path"], "merged video path"
        )
        video_path = _resolved_path(normalized_video_path, root, "source video")
        if video_path.exists():
            if _sha256_file(video_path) != video_sha256:
                raise ActiveLearningError("Source video SHA-256 does not match.")
        elif require_video:
            raise ActiveLearningError(f"Source video does not exist: {video_path}")

        checkpoint_path = _resolved_path(checkpoint, root, "checkpoint")
        checkpoint_file_checked = checkpoint_path.exists()
        if (
            checkpoint_file_checked
            and _sha256_file(checkpoint_path) != checkpoint_sha256
        ):
            raise ActiveLearningError("Checkpoint SHA-256 does not match.")

        audits = _mapping(
            _mapping(merged["settings"], "merged settings")["input_runs"],
            "settings.input_runs",
        )
        inference_runs = {
            "far": _source_audit(audits["far"], "far inference run", root),
            "near": _source_audit(audits["near"], "near inference run", root),
        }
        source = {
            "merged_json": _normalized_path(merged_path, root, "merged JSON"),
            "merged_json_sha256": _sha256_file(merged_path),
            "checkpoint": checkpoint,
            "checkpoint_sha256": checkpoint_sha256,
            "inference_runs": inference_runs,
            "format_version": merged["format_version"],
            "merge_format_version": merged["merge_format_version"],
            "model_version": merged["model_version"],
        }
        video = {
            "video_id": Path(normalized_video_path).stem,
            "path": normalized_video_path,
            "sha256": video_sha256,
            "fps": merged_video["fps"],
            "frame_count": merged_video["frame_count"],
            "width": merged_video["width"],
            "height": merged_video["height"],
            "duration_seconds": merged_video["duration_seconds"],
            "crops": {
                "far": far_settings["crop"],
                "near": near_settings["crop"],
            },
        }
        _validate_json_value(source, "source")
        _validate_json_value(video, "video")
    except ActiveLearningError:
        raise
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise ActiveLearningError(f"Invalid merged review source: {exc}") from exc
    return {
        "merged": merged,
        "source": source,
        "video": video,
        "verification": {"checkpoint_file_checked": checkpoint_file_checked},
    }


def write_review_selection(
    payload: object,
    output_path: str | Path,
    *,
    repo_root: str | Path,
    _verifier: Callable[..., object] = verify_dual_crop_review,
) -> dict[str, object]:
    root = Path(repo_root).expanduser().resolve()
    output = _resolved_path(output_path, root, "selection output")
    validated = _validate_selection_payload(
        payload, root, require_video=True, verifier=_verifier
    )
    _write_new_json(output, validated)
    return validated


def load_review_selection(
    selection_path: str | Path,
    *,
    repo_root: str | Path,
    require_video: bool = True,
    _verifier: Callable[..., object] = verify_dual_crop_review,
) -> dict[str, object]:
    root = Path(repo_root).expanduser().resolve()
    path = _resolved_path(selection_path, root, "selection JSON")
    try:
        payload = _load_json_bytes(path.read_bytes(), description="selection JSON")
    except ActiveLearningError:
        raise
    except OSError as exc:
        raise ActiveLearningError(f"Cannot read selection JSON: {path}") from exc
    return _validate_selection_payload(
        payload, root, require_video=require_video, verifier=_verifier
    )


def _validate_selection_payload(
    payload: object,
    repo_root: Path,
    *,
    require_video: bool,
    verifier: Callable[..., object],
    verified_merged: dict[str, object] | None = None,
) -> dict[str, object]:
    selection = _mapping(payload, "selection")
    _require_exact_fields(selection, SELECTION_ROOT_FIELDS, "selection")
    if type(selection["format_version"]) is not int or selection["format_version"] != 1:
        raise ActiveLearningError("Selection must use format version 1.")
    if selection["selection_algorithm_version"] != "active-learning-selection-v1":
        raise ActiveLearningError("Selection must use active-learning-selection-v1.")
    _nonempty_string(selection["batch_id"], "batch_id")
    _nonempty_string(selection["round_id"], "round_id")
    if type(selection["round_number"]) is not int or selection["round_number"] < 1:
        raise ActiveLearningError("round_number must be a positive integer.")

    selected_source = _mapping(selection["source"], "selection source")
    _require_exact_fields(selected_source, _SOURCE_FIELDS, "selection source")
    selected_video = _mapping(selection["video"], "selection video")
    _require_exact_fields(selected_video, _VIDEO_FIELDS, "selection video")
    if verified_merged is None:
        merged_path = _resolved_path(
            selected_source["merged_json"], repo_root, "source merged JSON"
        )
        if _sha256_file(merged_path) != _sha256(
            selected_source["merged_json_sha256"], "merged JSON SHA-256"
        ):
            raise ActiveLearningError("Merged JSON SHA-256 does not match.")
        verified = validate_merged_review_source(
            merged_path,
            repo_root=repo_root,
            require_video=require_video,
            _verifier=verifier,
        )
    else:
        verified = verified_merged
    if selected_source != verified["source"]:
        raise ActiveLearningError(
            "Selection source fields do not match the verified merged JSON."
        )
    if selected_video != verified["video"]:
        raise ActiveLearningError(
            "Selection video fields do not match the verified merged JSON."
        )

    if not isinstance(selection["settings"], dict):
        raise ActiveLearningError("settings must be an object.")
    if not isinstance(selection["previous_selections"], list):
        raise ActiveLearningError("previous_selections must be an array.")
    if not isinstance(selection["quota_summary"], list):
        raise ActiveLearningError("quota_summary must be an array.")
    if not isinstance(selection["coverage"], dict):
        raise ActiveLearningError("coverage must be an object.")
    _validate_clips(
        selection["clips"], video_duration=selected_video["duration_seconds"]
    )
    _validate_task_two_selection(
        selection, repo_root, merged=verified["merged"], verifier=verifier
    )
    _validate_json_value(selection, "selection")
    return selection


def _validate_task_two_selection(
    selection: dict[str, Any],
    repo_root: Path,
    *,
    merged: object,
    verifier: Callable[..., object],
) -> None:
    settings = _mapping(selection["settings"], "selection settings")
    _require_exact_fields(settings, _TASK_TWO_SETTINGS_FIELDS, "selection settings")
    settings_ms = _selection_settings_ms(
        round_number=selection["round_number"],
        seed=settings["seed"],
        preferred_clip_seconds=settings["preferred_clip_seconds"],
        min_clip_seconds=settings["min_clip_seconds"],
        max_clip_seconds=settings["max_clip_seconds"],
        min_anchor_gap_seconds=settings["min_anchor_gap_seconds"],
        time_strata=settings["time_strata"],
    )
    expected_round_id = f"round-{selection['round_number']:02d}"
    if selection["round_id"] != expected_round_id:
        raise ActiveLearningError("round_id does not match round_number.")
    expected_batch_id = f"{selection['video']['video_id']}-{expected_round_id}"
    if selection["batch_id"] != expected_batch_id:
        raise ActiveLearningError("batch_id does not match video_id and round_id.")
    planned_quotas = _mapping(settings["planned_quotas"], "planned_quotas")
    if tuple(planned_quotas.items()) != ROUND_ONE_QUOTAS:
        raise ActiveLearningError("planned_quotas must match the five round-one quotas.")

    previous_intervals = _validate_previous_selection_records(
        selection["previous_selections"],
        current_round=selection["round_number"],
        current_video=selection["video"],
        current_clips=selection["clips"],
        repo_root=repo_root,
        verifier=verifier,
    )
    video_duration_ms = seconds_to_milliseconds(
        _finite_number(
            selection["video"]["duration_seconds"], "video duration_seconds"
        )
    )
    merged_payload = _mapping(merged, "merged review source")
    source_events = _selection_events(
        merged_payload.get("events"),
        video_duration_ms=video_duration_ms,
        preferred_ms=settings_ms["preferred_ms"],
        min_ms=settings_ms["min_ms"],
        max_ms=settings_ms["max_ms"],
        time_strata=settings["time_strata"],
        previous_intervals=previous_intervals,
    )
    hint_ids, represented_sides = _validate_task_two_clips(
        selection["clips"],
        round_number=selection["round_number"],
        time_strata=settings["time_strata"],
        video_duration_ms=video_duration_ms,
        min_clip_ms=settings_ms["min_ms"],
        max_clip_ms=settings_ms["max_ms"],
        min_anchor_gap_ms=settings_ms["gap_ms"],
        source_events={event["stable_id"]: event for event in source_events},
    )
    _validate_quota_summary(selection["quota_summary"], selection["clips"])
    available_minority_ids = [
        event["stable_id"]
        for action in MINORITY_ACTIONS
        for event in sorted(
            (event for event in source_events if event["action"] == action),
            key=lambda event: (event["start_ms"], event["stable_id"]),
        )
    ]
    _validate_coverage(
        selection["coverage"],
        selection["clips"],
        available_sides=_ordered_sides(_represented_sides(source_events)),
        represented_sides=_ordered_sides(represented_sides),
        available_minority_ids=available_minority_ids,
        hint_ids=hint_ids,
    )


def _validate_previous_selection_records(
    value: list[object],
    *,
    current_round: int,
    current_video: dict[str, object],
    current_clips: list[object],
    repo_root: Path,
    verifier: Callable[..., object],
) -> list[tuple[int, int]]:
    current_intervals = [
        (
            seconds_to_milliseconds(
                _finite_number(
                    _mapping(raw_clip, "clip")["start_seconds"],
                    "clip start_seconds",
                )
            ),
            seconds_to_milliseconds(
                _finite_number(
                    _mapping(raw_clip, "clip")["end_seconds"],
                    "clip end_seconds",
                )
            ),
        )
        for raw_clip in current_clips
    ]
    previous_intervals: list[tuple[int, int]] = []
    seen_paths: set[str] = set()
    for index, raw_record in enumerate(value, start=1):
        record = _mapping(raw_record, f"previous selection record {index}")
        _require_exact_fields(
            record, _PREVIOUS_SELECTION_FIELDS, f"previous selection record {index}"
        )
        normalized = _relative_posix_path(
            record["path"], f"previous selection record {index} path"
        )
        if normalized in seen_paths:
            raise ActiveLearningError("Previous selection record paths must be unique.")
        seen_paths.add(normalized)
        path = _resolved_path(normalized, repo_root, "previous selection record")
        expected_sha256 = _sha256(
            record["sha256"], f"previous selection record {index} SHA-256"
        )
        if _sha256_file(path) != expected_sha256:
            raise ActiveLearningError("Previous selection SHA-256 does not match.")
        previous = load_review_selection(
            path, repo_root=repo_root, _verifier=verifier
        )
        if (
            previous["batch_id"] != _nonempty_string(
                record["batch_id"], "previous selection batch_id"
            )
            or previous["round_id"]
            != _nonempty_string(record["round_id"], "previous selection round_id")
        ):
            raise ActiveLearningError(
                "Previous selection metadata does not match its pinned artifact."
            )
        if previous["round_number"] >= current_round:
            raise ActiveLearningError(
                "Previous selections must come from an earlier round."
            )
        _require_same_video(previous["video"], current_video)
        for raw_clip in previous["clips"]:
            clip = _mapping(raw_clip, "previous selection clip")
            previous_interval = (
                seconds_to_milliseconds(
                    _finite_number(
                        clip["start_seconds"], "previous clip start_seconds"
                    )
                ),
                seconds_to_milliseconds(
                    _finite_number(clip["end_seconds"], "previous clip end_seconds")
                ),
            )
            if any(
                _intervals_overlap(*previous_interval, *current_interval)
                for current_interval in current_intervals
            ):
                raise ActiveLearningError(
                    "Current clips must not overlap a previous selection clip."
                )
            previous_intervals.append(previous_interval)
    previous_intervals.sort()
    return previous_intervals


def _require_same_video(left: object, right: object) -> None:
    left_video = _mapping(left, "previous selection video")
    right_video = _mapping(right, "current selection video")
    identity_fields = (
        "sha256",
        "fps",
        "frame_count",
        "width",
        "height",
        "duration_seconds",
    )
    if any(left_video.get(field) != right_video.get(field) for field in identity_fields):
        raise ActiveLearningError(
            "Previous selections must reference the same source video."
        )


def _validate_task_two_clips(
    value: list[object],
    *,
    round_number: int,
    time_strata: int,
    video_duration_ms: int,
    min_clip_ms: int,
    max_clip_ms: int,
    min_anchor_gap_ms: int,
    source_events: dict[str, dict[str, Any]],
) -> tuple[set[str], set[str]]:
    all_source_ids: set[str] = set()
    all_duplicate_ids: set[str] = set()
    all_conflict_ids: set[str] = set()
    all_hint_ids: set[str] = set()
    represented_sides: set[str] = set()
    previous_anchor_end_ms: int | None = None
    for ordinal, raw_clip in enumerate(value, start=1):
        clip = _mapping(raw_clip, f"clip {ordinal}")
        _require_exact_fields(clip, _TASK_TWO_CLIP_FIELDS, f"clip {ordinal}")
        expected_clip_id = f"round-{round_number:02d}-clip-{ordinal:03d}"
        if clip["clip_id"] != expected_clip_id:
            raise ActiveLearningError("Task 2 clip IDs must match round and ordinal.")
        start_ms = seconds_to_milliseconds(
            _finite_number(clip["start_seconds"], "clip start_seconds")
        )
        end_ms = seconds_to_milliseconds(
            _finite_number(clip["end_seconds"], "clip end_seconds")
        )
        duration_ms = seconds_to_milliseconds(
            _finite_number(clip["duration_seconds"], "clip duration_seconds")
        )
        if not min_clip_ms <= duration_ms <= max_clip_ms:
            raise ActiveLearningError(
                "Task 2 clip duration is outside the persisted duration bounds."
            )
        if clip["start_time"] != format_timecode(start_ms / 1000):
            raise ActiveLearningError("clip start_time does not match start_seconds.")
        if clip["end_time"] != format_timecode(end_ms / 1000):
            raise ActiveLearningError("clip end_time does not match end_seconds.")
        stratum = clip["time_stratum"]
        if type(stratum) is not int or not 0 <= stratum < time_strata:
            raise ActiveLearningError("clip time_stratum is invalid.")
        if clip["selection_bucket"] not in dict(ROUND_ONE_QUOTAS):
            raise ActiveLearningError("clip selection_bucket is invalid.")
        _string_list(clip["selection_reasons"], "selection_reasons", required=True)
        if clip["proxy_filename"] != f"clips/{expected_clip_id}.mp4":
            raise ActiveLearningError("clip proxy_filename does not match clip_id.")

        anchor = _mapping(clip["anchor"], "clip anchor")
        _require_exact_fields(anchor, _ANCHOR_FIELDS, "clip anchor")
        anchor_start_ms = seconds_to_milliseconds(
            _finite_number(anchor["start_seconds"], "anchor start_seconds")
        )
        anchor_end_ms = seconds_to_milliseconds(
            _finite_number(anchor["end_seconds"], "anchor end_seconds")
        )
        if not start_ms <= anchor_start_ms < anchor_end_ms <= end_ms:
            raise ActiveLearningError("Anchor bounds must stay within the clip.")
        if (
            previous_anchor_end_ms is not None
            and anchor_start_ms - previous_anchor_end_ms < min_anchor_gap_ms
        ):
            raise ActiveLearningError(
                "Clip anchors violate the persisted minimum anchor gap."
            )
        previous_anchor_end_ms = anchor_end_ms
        expected_stratum = min(
            time_strata - 1,
            ((anchor_start_ms + anchor_end_ms) // 2)
            * time_strata
            // video_duration_ms,
        )
        if stratum != expected_stratum:
            raise ActiveLearningError("clip time_stratum does not match its anchor.")
        _nonempty_string(anchor["action"], "anchor action")
        anchor_sides = _sides(anchor["observed_sides"], "anchor observed_sides")

        hints = clip["candidate_hints"]
        if not isinstance(hints, list):
            raise ActiveLearningError("candidate_hints must be an array.")
        hint_ids: set[str] = set()
        hint_source_ids: set[str] = set()
        hint_duplicate_ids: set[str] = set()
        hint_conflict_ids: set[str] = set()
        for hint_index, raw_hint in enumerate(hints, start=1):
            hint = _mapping(raw_hint, f"candidate hint {hint_index}")
            _require_exact_fields(hint, _HINT_FIELDS, f"candidate hint {hint_index}")
            hint_id = _nonempty_string(
                hint["canonical_event_id"], "hint canonical_event_id"
            )
            if hint_id in hint_ids:
                raise ActiveLearningError("Candidate hint IDs must be unique per clip.")
            hint_ids.add(hint_id)
            if hint_id in all_hint_ids:
                raise ActiveLearningError(
                    "Canonical candidate hint IDs must be globally unique."
                )
            all_hint_ids.add(hint_id)
            absolute_start_ms = seconds_to_milliseconds(
                _finite_number(
                    hint["absolute_start_seconds"], "hint absolute_start_seconds"
                )
            )
            absolute_end_ms = seconds_to_milliseconds(
                _finite_number(
                    hint["absolute_end_seconds"], "hint absolute_end_seconds"
                )
            )
            relative_start_ms = seconds_to_milliseconds(
                _finite_number(
                    hint["relative_start_seconds"], "hint relative_start_seconds"
                )
            )
            relative_end_ms = seconds_to_milliseconds(
                _finite_number(
                    hint["relative_end_seconds"], "hint relative_end_seconds"
                )
            )
            if not start_ms <= absolute_start_ms < absolute_end_ms <= end_ms:
                raise ActiveLearningError("Candidate hint bounds must stay within clip.")
            if (
                relative_start_ms != absolute_start_ms - start_ms
                or relative_end_ms != absolute_end_ms - start_ms
            ):
                raise ActiveLearningError("Candidate hint relative bounds are invalid.")
            _nonempty_string(hint["action"], "hint action")
            confidence = _finite_number(hint["confidence"], "hint confidence")
            if not 0 <= confidence <= 1:
                raise ActiveLearningError("Hint confidence must be between 0 and 1.")
            hint_sides = _sides(hint["observed_sides"], "hint observed_sides")
            duplicate_id = _optional_string(
                hint["duplicate_group_id"], "hint duplicate_group_id"
            )
            conflict_id = _optional_string(
                hint["conflict_group_id"], "hint conflict_group_id"
            )
            source_candidate_ids = _string_list(
                hint["source_candidate_ids"],
                "hint source_candidate_ids",
                required=True,
            )
            hint_source_ids.update(source_candidate_ids)
            if duplicate_id is not None:
                hint_duplicate_ids.add(duplicate_id)
            if conflict_id is not None:
                hint_conflict_ids.add(conflict_id)
            source_event = source_events.get(hint_id)
            if source_event is None or (
                absolute_start_ms != source_event["start_ms"]
                or absolute_end_ms != source_event["end_ms"]
                or hint["action"] != source_event["action"]
                or confidence != source_event["confidence"]
                or hint_sides != source_event["observed_sides"]
                or duplicate_id != source_event["duplicate_group_id"]
                or conflict_id != source_event["conflict_group_id"]
                or source_candidate_ids != source_event["source_event_ids"]
            ):
                raise ActiveLearningError(
                    "Candidate hint does not match its merged canonical event."
                )
            represented_sides.update(hint_sides)

        anchor_id = anchor["canonical_event_id"]
        if hints:
            if anchor_id not in hint_ids or anchor["confidence"] is None:
                raise ActiveLearningError("Event clip anchor must reference a hint.")
            _finite_number(anchor["confidence"], "anchor confidence")
        elif (
            anchor_id is not None
            or anchor["action"] != "background"
            or anchor["confidence"] is not None
            or anchor_sides != ["far", "near"]
        ):
            raise ActiveLearningError("Background clips must use a background anchor.")

        source_ids = _string_list(
            clip["reserved_source_event_ids"],
            "reserved_source_event_ids",
            required=bool(hints),
        )
        duplicate_ids = _string_list(
            clip["reserved_duplicate_group_ids"],
            "reserved_duplicate_group_ids",
            required=False,
        )
        conflict_ids = _string_list(
            clip["reserved_conflict_group_ids"],
            "reserved_conflict_group_ids",
            required=False,
        )
        if (
            set(source_ids) != hint_source_ids
            or set(duplicate_ids) != hint_duplicate_ids
            or set(conflict_ids) != hint_conflict_ids
        ):
            raise ActiveLearningError(
                "Clip reservations must equal the complete candidate hint resources."
            )
        if (
            all_source_ids.intersection(source_ids)
            or all_duplicate_ids.intersection(duplicate_ids)
            or all_conflict_ids.intersection(conflict_ids)
        ):
            raise ActiveLearningError("Reserved candidate resources must be unique.")
        all_source_ids.update(source_ids)
        all_duplicate_ids.update(duplicate_ids)
        all_conflict_ids.update(conflict_ids)
    return all_hint_ids, represented_sides


def _validate_quota_summary(value: list[object], clips: list[object]) -> None:
    if len(value) != len(ROUND_ONE_QUOTAS):
        raise ActiveLearningError("quota_summary must contain all five buckets.")
    counts = {name: 0 for name, _ in ROUND_ONE_QUOTAS}
    for raw_clip in clips:
        counts[_mapping(raw_clip, "clip")["selection_bucket"]] += 1
    incoming = 0
    for index, ((expected_bucket, planned), raw_summary) in enumerate(
        zip(ROUND_ONE_QUOTAS, value), start=1
    ):
        summary = _mapping(raw_summary, f"quota summary {index}")
        _require_exact_fields(summary, _QUOTA_FIELDS, f"quota summary {index}")
        selected = summary["selected"]
        transferred_out = summary["transferred_out"]
        if (
            summary["bucket"] != expected_bucket
            or summary["planned"] != planned
            or type(selected) is not int
            or selected != counts[expected_bucket]
            or type(transferred_out) is not int
            or transferred_out != planned + incoming - selected
            or transferred_out < 0
        ):
            raise ActiveLearningError("quota_summary does not match selected clips.")
        expected_to = (
            ROUND_ONE_QUOTAS[index][0]
            if transferred_out and index < len(ROUND_ONE_QUOTAS)
            else None
        )
        if summary["transferred_to"] != expected_to or summary["reason"] != (
            "eligible_pool_exhausted" if transferred_out else None
        ):
            raise ActiveLearningError("quota_summary transfer metadata is invalid.")
        incoming = transferred_out
    if incoming:
        raise ActiveLearningError("The final bucket cannot transfer a deficit.")


def _validate_coverage(
    value: dict[str, object],
    clips: list[object],
    *,
    available_sides: list[str],
    represented_sides: list[str],
    available_minority_ids: list[str],
    hint_ids: set[str],
) -> None:
    coverage = _mapping(value, "coverage")
    _require_exact_fields(coverage, _COVERAGE_FIELDS, "coverage")
    represented = coverage["represented_time_strata"]
    if (
        not isinstance(represented, list)
        or any(type(item) is not int for item in represented)
        or represented != sorted(set(represented))
    ):
        raise ActiveLearningError("represented_time_strata must be sorted and unique.")
    actual_strata = sorted(
        {_mapping(clip, "clip")["time_stratum"] for clip in clips}
    )
    if (
        represented != actual_strata
        or coverage["represented_time_strata_count"] != len(represented)
        or len(represented) < 10
    ):
        raise ActiveLearningError("coverage time strata do not match selected clips.")
    persisted_available_sides = _sides(
        coverage["available_crop_scenes"], "available_crop_scenes"
    )
    persisted_represented_sides = _sides(
        coverage["represented_crop_scenes"], "represented_crop_scenes"
    )
    if (
        persisted_available_sides != available_sides
        or persisted_represented_sides != represented_sides
    ):
        raise ActiveLearningError(
            "Persisted scene coverage does not match the merged source and hints."
        )
    available_minority = _string_list(
        coverage["available_minority_candidate_ids"],
        "available_minority_candidate_ids",
        required=False,
    )
    covered_minority = _string_list(
        coverage["covered_minority_candidate_ids"],
        "covered_minority_candidate_ids",
        required=False,
    )
    actual_covered_minority = [
        event_id for event_id in available_minority_ids if event_id in hint_ids
    ]
    if available_minority != available_minority_ids:
        raise ActiveLearningError(
            "Persisted coverage does not match merged minority candidates."
        )
    if (
        covered_minority != actual_covered_minority
        or actual_covered_minority != available_minority_ids
    ):
        raise ActiveLearningError(
            "All merged minority candidates must be covered by actual hints."
        )


def _validate_clips(value: object, *, video_duration: object) -> None:
    if not isinstance(value, list) or len(value) != 40:
        raise ActiveLearningError("Selection must contain exactly 40 clips.")
    duration = _finite_number(video_duration, "video duration_seconds")
    seen_ids: set[str] = set()
    previous_end: int | float | None = None
    for expected_ordinal, raw_clip in enumerate(value, start=1):
        clip = _mapping(raw_clip, f"clip {expected_ordinal}")
        for field in ("clip_id", "ordinal", "start_seconds", "end_seconds"):
            if field not in clip:
                raise ActiveLearningError(
                    f"clip {expected_ordinal} is missing field {field!r}."
                )
        clip_id = _nonempty_string(clip["clip_id"], "clip_id")
        if clip_id in seen_ids:
            raise ActiveLearningError("Clip IDs must be unique.")
        seen_ids.add(clip_id)
        if type(clip["ordinal"]) is not int or clip["ordinal"] != expected_ordinal:
            raise ActiveLearningError("Clip ordinals must be exactly 1 through 40.")
        start = _finite_number(clip["start_seconds"], "clip start_seconds")
        end = _finite_number(clip["end_seconds"], "clip end_seconds")
        if start < 0 or end <= start or end > duration:
            raise ActiveLearningError(
                "Clip bounds must satisfy 0 <= start < end <= video duration."
            )
        if previous_end is not None and start < previous_end:
            raise ActiveLearningError(
                "Clips must be time-ordered and must not overlap."
            )
        previous_end = end
        if "duration_seconds" in clip:
            clip_duration = _finite_number(
                clip["duration_seconds"], "clip duration_seconds"
            )
            if seconds_to_milliseconds(clip_duration) != (
                seconds_to_milliseconds(end) - seconds_to_milliseconds(start)
            ):
                raise ActiveLearningError(
                    "Clip duration_seconds must equal end_seconds - start_seconds."
                )


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


def _load_json_bytes(raw: bytes, *, description: str) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ActiveLearningError(
                    f"{description} contains duplicate key {key!r}."
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ActiveLearningError(f"{description} contains non-finite number {value}.")

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActiveLearningError(f"{description} is not valid UTF-8 JSON.") from exc
    return _mapping(parsed, description)


def _resolved_path(value: str | Path, root: Path, description: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ActiveLearningError(
            f"{description} escapes repository root: {value}"
        ) from exc
    return resolved


def _normalized_path(path: Path, root: Path, description: str) -> str:
    resolved = _resolved_path(path, root, description)
    return resolved.relative_to(root).as_posix()


def _relative_posix_path(value: object, description: str) -> str:
    text = _nonempty_string(value, description)
    candidate = PurePosixPath(text)
    windows_candidate = PureWindowsPath(text)
    if (
        windows_candidate.drive
        or candidate.is_absolute()
        or "\\" in text
        or text.startswith("/")
        or ".." in candidate.parts
    ):
        raise ActiveLearningError(
            f"{description} must be a repository-relative POSIX path."
        )
    return candidate.as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ActiveLearningError(f"Cannot hash file: {path}") from exc
    return digest.hexdigest()


def _sha256(value: object, description: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ActiveLearningError(f"{description} must be a lowercase SHA-256.")
    return value


def _source_audit(
    value: object, description: str, repo_root: Path
) -> dict[str, str]:
    audit = _mapping(value, description)
    _require_exact_fields(audit, _INFERENCE_RUN_FIELDS, description)
    source_file = _relative_posix_path(
        audit["source_file"], f"{description} source_file"
    )
    _resolved_path(source_file, repo_root, f"{description} source_file")
    return {
        "source_file": source_file,
        "source_file_sha256": _sha256(
            audit["source_file_sha256"], f"{description} source_file_sha256"
        ),
        "normalized_payload_sha256": _sha256(
            audit["normalized_payload_sha256"],
            f"{description} normalized_payload_sha256",
        ),
    }
