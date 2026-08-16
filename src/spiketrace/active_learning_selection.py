from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .dual_crop_review import verify_dual_crop_review
from .errors import ActiveLearningError
from .events import seconds_to_milliseconds

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
_SOURCE_FIELDS = (
    "merged_json",
    "merged_json_sha256",
    "checkpoint",
    "checkpoint_sha256",
    "inference_runs",
    "format_version",
    "merge_format_version",
    "model_version",
)
_VIDEO_FIELDS = (
    "video_id",
    "path",
    "sha256",
    "fps",
    "frame_count",
    "width",
    "height",
    "duration_seconds",
    "crops",
)
_INFERENCE_RUN_FIELDS = (
    "source_file",
    "source_file_sha256",
    "normalized_payload_sha256",
)
_SHA256_LENGTH = 64

ROUND_ONE_QUOTAS = (
    ("conflict_or_minority", 20),
    ("high_confidence_tail", 8),
    ("dual_view_agreement", 4),
    ("random_candidate_control", 4),
    ("dual_background_control", 4),
)
MINORITY_ACTIONS = ("receive", "block", "dig")
_SIDE_ORDER = {"far": 0, "near": 1}
_TASK_TWO_SETTINGS_FIELDS = (
    "seed",
    "preferred_clip_seconds",
    "min_clip_seconds",
    "max_clip_seconds",
    "min_anchor_gap_seconds",
    "time_strata",
    "planned_quotas",
)
_PREVIOUS_SELECTION_FIELDS = ("path", "sha256", "batch_id", "round_id")
_QUOTA_FIELDS = (
    "bucket",
    "planned",
    "selected",
    "transferred_out",
    "transferred_to",
    "reason",
)
_COVERAGE_FIELDS = (
    "represented_time_strata",
    "represented_time_strata_count",
    "available_crop_scenes",
    "represented_crop_scenes",
    "available_minority_candidate_ids",
    "covered_minority_candidate_ids",
)
_TASK_TWO_CLIP_FIELDS = (
    "clip_id",
    "ordinal",
    "start_seconds",
    "end_seconds",
    "start_time",
    "end_time",
    "duration_seconds",
    "time_stratum",
    "selection_bucket",
    "selection_reasons",
    "proxy_filename",
    "anchor",
    "candidate_hints",
    "reserved_source_event_ids",
    "reserved_duplicate_group_ids",
    "reserved_conflict_group_ids",
)
_ANCHOR_FIELDS = (
    "canonical_event_id",
    "start_seconds",
    "end_seconds",
    "action",
    "confidence",
    "observed_sides",
)
_HINT_FIELDS = (
    "canonical_event_id",
    "absolute_start_seconds",
    "absolute_end_seconds",
    "relative_start_seconds",
    "relative_end_seconds",
    "action",
    "confidence",
    "observed_sides",
    "duplicate_group_id",
    "conflict_group_id",
    "source_candidate_ids",
)


def select_review_batch(
    merged_json_path: str | Path,
    output_path: str | Path,
    *,
    repo_root: str | Path,
    round_number: int = 1,
    seed: int = 42,
    preferred_clip_seconds: float = 15.0,
    min_clip_seconds: float = 5.0,
    max_clip_seconds: float = 30.0,
    min_anchor_gap_seconds: float = 5.0,
    time_strata: int = 10,
    previous_selection_paths: Iterable[str | Path] = (),
) -> dict[str, object]:
    settings_ms = _selection_settings_ms(
        round_number=round_number,
        seed=seed,
        preferred_clip_seconds=preferred_clip_seconds,
        min_clip_seconds=min_clip_seconds,
        max_clip_seconds=max_clip_seconds,
        min_anchor_gap_seconds=min_anchor_gap_seconds,
        time_strata=time_strata,
    )
    root = Path(repo_root).expanduser().resolve()
    verified = validate_merged_review_source(merged_json_path, repo_root=root)
    merged = _mapping(verified["merged"], "merged review source")
    video = _mapping(verified["video"], "selection video")
    video_duration_ms = seconds_to_milliseconds(
        _finite_number(video["duration_seconds"], "video duration_seconds")
    )
    if video_duration_ms <= 0:
        raise ActiveLearningError("Video duration must be positive.")

    previous_records, previous_intervals = _load_previous_selections(
        previous_selection_paths, repo_root=root
    )
    events = _selection_events(
        merged.get("events"),
        video_duration_ms=video_duration_ms,
        preferred_ms=settings_ms["preferred_ms"],
        min_ms=settings_ms["min_ms"],
        max_ms=settings_ms["max_ms"],
        time_strata=time_strata,
        previous_intervals=previous_intervals,
    )
    background_items = _dual_background_items(
        merged.get("input_runs"),
        video_duration_ms=video_duration_ms,
        preferred_ms=settings_ms["preferred_ms"],
        min_ms=settings_ms["min_ms"],
        max_ms=settings_ms["max_ms"],
        time_strata=time_strata,
        previous_intervals=previous_intervals,
    )

    required_events = [
        event for event in events if event["action"] in MINORITY_ACTIONS
    ]
    required_ids = {event["stable_id"] for event in required_events}
    required_items = _minority_clusters(
        required_events,
        video_duration_ms=video_duration_ms,
        preferred_ms=settings_ms["preferred_ms"],
        min_ms=settings_ms["min_ms"],
        max_ms=settings_ms["max_ms"],
        time_strata=time_strata,
    )
    required_items.sort(key=_minority_item_key)

    nonminority = [event for event in events if event["stable_id"] not in required_ids]
    conflict_items = [
        _event_item(event, reasons=["distinct_conflict_group"])
        for event in nonminority
        if event["conflict_group_id"] is not None
    ]
    conflict_items.sort(
        key=lambda item: (
            item["anchor_start_ms"],
            item["conflict_group_ids"],
            item["stable_id"],
        )
    )
    tail_items = [
        _event_item(event, reasons=["high_confidence_tail_priority"])
        for event in nonminority
        if event["conflict_group_id"] is None
    ]
    tail_items.sort(
        key=lambda item: (
            not (
                item["members"][0]["action"] in ("set", "attack", "serve")
                and item["members"][0]["confidence"] >= 0.4
            ),
            -item["members"][0]["confidence"],
            item["anchor_start_ms"],
            item["stable_id"],
        )
    )
    dual_items = [
        _event_item(event, reasons=["cross_crop_duplicate_agreement"])
        for event in nonminority
        if event["conflict_group_id"] is None
        and event["duplicate_group_id"] is not None
        and not (
            event["action"] in ("set", "attack", "serve")
            and event["confidence"] >= 0.4
        )
    ]
    dual_items.sort(
        key=lambda item: (
            item["anchor_start_ms"],
            item["duplicate_group_ids"],
            item["stable_id"],
        )
    )

    random_items = [
        _event_item(event, reasons=["time_stratified_random_control"])
        for event in nonminority
    ]

    bucket_items = {
        "conflict_or_minority": required_items + conflict_items,
        "high_confidence_tail": tail_items,
        "dual_view_agreement": dual_items,
        "random_candidate_control": random_items,
        "dual_background_control": background_items,
    }
    selected: list[dict[str, Any]] = []
    reserved_sources: set[str] = set()
    reserved_duplicates: set[str] = set()
    reserved_conflicts: set[str] = set()
    quota_summary: list[dict[str, object]] = []
    transfer = 0

    for bucket_index, (bucket, planned) in enumerate(ROUND_ONE_QUOTAS):
        target = planned + transfer
        before = sum(item["selection_bucket"] == bucket for item in selected)
        candidates = bucket_items[bucket]
        if bucket in ("random_candidate_control", "dual_background_control"):
            covered = {item["time_stratum"] for item in selected}
            candidates = _interleave_time_strata(
                candidates,
                time_strata=time_strata,
                covered_strata=covered,
                rank_key=lambda item, name=bucket: _stable_rank(
                    seed, name, item["stable_id"]
                ),
                stratum_rank_key=lambda index, name=bucket: _stable_rank(
                    seed, f"{name}:stratum", str(index)
                ),
            )
        for item in candidates:
            current = sum(
                candidate["selection_bucket"] == bucket for candidate in selected
            )
            if current - before >= target:
                break
            _accept_item(
                item,
                bucket=bucket,
                selected=selected,
                reserved_sources=reserved_sources,
                reserved_duplicates=reserved_duplicates,
                reserved_conflicts=reserved_conflicts,
                min_anchor_gap_ms=settings_ms["gap_ms"],
                max_ms=settings_ms["max_ms"],
                video_duration_ms=video_duration_ms,
                time_strata=time_strata,
            )

        selected_count = (
            sum(item["selection_bucket"] == bucket for item in selected) - before
        )
        transfer = max(0, target - selected_count)
        next_bucket = (
            ROUND_ONE_QUOTAS[bucket_index + 1][0]
            if transfer and bucket_index + 1 < len(ROUND_ONE_QUOTAS)
            else None
        )
        quota_summary.append(
            {
                "bucket": bucket,
                "planned": planned,
                "selected": selected_count,
                "transferred_out": transfer,
                "transferred_to": next_bucket,
                "reason": "eligible_pool_exhausted" if transfer else None,
            }
        )
        if bucket == "conflict_or_minority":
            covered_required = {
                member["stable_id"]
                for item in selected
                for member in item["members"]
                if member["action"] in MINORITY_ACTIONS
            }
            if covered_required != required_ids:
                missing = sorted(required_ids - covered_required)
                raise ActiveLearningError(
                    "The first bucket did not cover required minority candidates: "
                    + ", ".join(missing)
                )

    if transfer or len(selected) != 40:
        raise ActiveLearningError(
            f"Could not select exactly 40 legal clips; selected {len(selected)}."
        )

    represented_strata = sorted({item["time_stratum"] for item in selected})
    if len(represented_strata) < 10:
        raise ActiveLearningError(
            "The selected batch must represent at least 10 time strata."
        )
    available_sides = _represented_sides(events)
    selected_sides = _represented_sides_from_items(selected)
    if {"far", "near"}.issubset(available_sides) and not {
        "far",
        "near",
    }.issubset(selected_sides):
        raise ActiveLearningError(
            "The selected batch must represent both available crop scenes."
        )

    selected.sort(key=lambda item: (item["start_ms"], item["end_ms"], item["stable_id"]))
    clips = [
        _serialize_clip(item, ordinal=index, round_number=round_number)
        for index, item in enumerate(selected, start=1)
    ]
    round_id = f"round-{round_number:02d}"
    ordered_required_ids = [
        event["stable_id"]
        for action in MINORITY_ACTIONS
        for event in sorted(
            (candidate for candidate in required_events if candidate["action"] == action),
            key=lambda candidate: (candidate["start_ms"], candidate["stable_id"]),
        )
    ]
    settings = {
        "seed": seed,
        "preferred_clip_seconds": preferred_clip_seconds,
        "min_clip_seconds": min_clip_seconds,
        "max_clip_seconds": max_clip_seconds,
        "min_anchor_gap_seconds": min_anchor_gap_seconds,
        "time_strata": time_strata,
        "planned_quotas": {name: count for name, count in ROUND_ONE_QUOTAS},
    }
    coverage = {
        "represented_time_strata": represented_strata,
        "represented_time_strata_count": len(represented_strata),
        "available_crop_scenes": _ordered_sides(available_sides),
        "represented_crop_scenes": _ordered_sides(selected_sides),
        "available_minority_candidate_ids": ordered_required_ids,
        "covered_minority_candidate_ids": ordered_required_ids,
    }
    payload = {
        "format_version": 1,
        "selection_algorithm_version": "active-learning-selection-v1",
        "batch_id": f"{video['video_id']}-{round_id}",
        "round_id": round_id,
        "round_number": round_number,
        "source": verified["source"],
        "video": verified["video"],
        "settings": settings,
        "previous_selections": previous_records,
        "quota_summary": quota_summary,
        "coverage": coverage,
        "clips": clips,
    }
    return write_review_selection(payload, output_path, repo_root=root)


def _selection_settings_ms(
    *,
    round_number: object,
    seed: object,
    preferred_clip_seconds: object,
    min_clip_seconds: object,
    max_clip_seconds: object,
    min_anchor_gap_seconds: object,
    time_strata: object,
) -> dict[str, int]:
    if type(round_number) is not int or round_number < 1:
        raise ActiveLearningError("round_number must be a positive integer.")
    if type(seed) is not int:
        raise ActiveLearningError("seed must be an integer.")
    values = {
        "preferred_ms": _positive_seconds_ms(
            preferred_clip_seconds, "preferred_clip_seconds"
        ),
        "min_ms": _positive_seconds_ms(min_clip_seconds, "min_clip_seconds"),
        "max_ms": _positive_seconds_ms(max_clip_seconds, "max_clip_seconds"),
        "gap_ms": _positive_seconds_ms(
            min_anchor_gap_seconds, "min_anchor_gap_seconds"
        ),
    }
    if values["min_ms"] < 5000 or values["max_ms"] > 30000:
        raise ActiveLearningError("Review clips must stay within 5 to 30 seconds.")
    if values["min_ms"] > values["max_ms"]:
        raise ActiveLearningError(
            "min_clip_seconds must not exceed max_clip_seconds."
        )
    if type(time_strata) is not int or time_strata < 10:
        raise ActiveLearningError("time_strata must be an integer of at least 10.")
    return values


def _positive_seconds_ms(value: object, description: str) -> int:
    seconds = _finite_number(value, description)
    if seconds <= 0:
        raise ActiveLearningError(f"{description} must be positive.")
    milliseconds = seconds_to_milliseconds(seconds)
    if milliseconds <= 0:
        raise ActiveLearningError(f"{description} must be at least one millisecond.")
    return milliseconds


def _load_previous_selections(
    paths: Iterable[str | Path], *, repo_root: Path
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
        previous = load_review_selection(path, repo_root=repo_root)
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
        except ActiveLearningError as exc:
            if action in MINORITY_ACTIONS:
                raise ActiveLearningError(
                    "Required minority cluster cannot fit in a legal clip: "
                    + event_id
                ) from exc
            raise
        if any(
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


def _event_item(event: dict[str, Any], *, reasons: list[str]) -> dict[str, Any]:
    return {
        "stable_id": event["stable_id"],
        "start_ms": event["clip_start_ms"],
        "end_ms": event["clip_end_ms"],
        "anchor_start_ms": event["start_ms"],
        "anchor_end_ms": event["end_ms"],
        "time_stratum": event["time_stratum"],
        "members": [event],
        "source_ids": list(event["source_event_ids"]),
        "duplicate_group_ids": _present_list(event["duplicate_group_id"]),
        "conflict_group_ids": _present_list(event["conflict_group_id"]),
        "observed_sides": list(event["observed_sides"]),
        "selection_reasons": reasons,
        "required": False,
    }


def _minority_clusters(
    events: list[dict[str, Any]],
    *,
    video_duration_ms: int,
    preferred_ms: int,
    min_ms: int,
    max_ms: int,
    time_strata: int,
) -> list[dict[str, Any]]:
    remaining = set(range(len(events)))
    clusters: list[list[dict[str, Any]]] = []
    while remaining:
        queue = [remaining.pop()]
        indexes = set(queue)
        while queue:
            current = queue.pop()
            linked = {
                other
                for other in remaining
                if _minority_events_link(events[current], events[other])
                or any(
                    _minority_events_link(events[member], events[other])
                    for member in indexes
                )
            }
            remaining.difference_update(linked)
            indexes.update(linked)
            queue.extend(linked)
        clusters.append([events[index] for index in indexes])

    items: list[dict[str, Any]] = []
    for members in clusters:
        members.sort(key=lambda item: (item["start_ms"], item["stable_id"]))
        anchor_start_ms = min(item["start_ms"] for item in members)
        anchor_end_ms = max(item["end_ms"] for item in members)
        ids = [item["stable_id"] for item in members]
        try:
            start_ms, end_ms = _fit_clip_bounds(
                anchor_start_ms,
                anchor_end_ms,
                video_duration_ms=video_duration_ms,
                preferred_ms=preferred_ms,
                min_ms=min_ms,
                max_ms=max_ms,
            )
        except ActiveLearningError as exc:
            raise ActiveLearningError(
                "Required minority cluster cannot fit in a legal clip: "
                + ", ".join(ids)
            ) from exc
        midpoint_ms = (anchor_start_ms + anchor_end_ms) // 2
        items.append(
            {
                "stable_id": "|".join(ids),
                "start_ms": start_ms,
                "end_ms": end_ms,
                "anchor_start_ms": anchor_start_ms,
                "anchor_end_ms": anchor_end_ms,
                "time_stratum": min(
                    time_strata - 1,
                    midpoint_ms * time_strata // video_duration_ms,
                ),
                "members": members,
                "source_ids": sorted(
                    {source for item in members for source in item["source_event_ids"]}
                ),
                "duplicate_group_ids": sorted(
                    {
                        item["duplicate_group_id"]
                        for item in members
                        if item["duplicate_group_id"] is not None
                    }
                ),
                "conflict_group_ids": sorted(
                    {
                        item["conflict_group_id"]
                        for item in members
                        if item["conflict_group_id"] is not None
                    }
                ),
                "observed_sides": _ordered_sides(
                    {side for item in members for side in item["observed_sides"]}
                ),
                "selection_reasons": ["required_minority_coverage"],
                "required": True,
            }
        )
    return items


def _minority_events_link(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if _intervals_overlap(
        left["clip_start_ms"],
        left["clip_end_ms"],
        right["clip_start_ms"],
        right["clip_end_ms"],
    ):
        return True
    return bool(
        (
            left["duplicate_group_id"] is not None
            and left["duplicate_group_id"] == right["duplicate_group_id"]
        )
        or (
            left["conflict_group_id"] is not None
            and left["conflict_group_id"] == right["conflict_group_id"]
        )
    )


def _minority_item_key(item: dict[str, Any]) -> tuple[int, int, str]:
    action_rank = min(
        MINORITY_ACTIONS.index(member["action"]) for member in item["members"]
    )
    return action_rank, item["anchor_start_ms"], item["stable_id"]


def _dual_background_items(
    value: object,
    *,
    video_duration_ms: int,
    preferred_ms: int,
    min_ms: int,
    max_ms: int,
    time_strata: int,
    previous_intervals: list[tuple[int, int]],
) -> list[dict[str, Any]]:
    input_runs = _mapping(value, "input_runs")
    by_side: dict[str, list[tuple[int, int]]] = {}
    for side in ("far", "near"):
        run = _mapping(input_runs.get(side), f"{side} input run")
        raw_windows = run.get("windows", [])
        if not isinstance(raw_windows, list):
            raise ActiveLearningError(f"{side} windows must be an array.")
        intervals: list[tuple[int, int]] = []
        for index, raw_window in enumerate(raw_windows, start=1):
            window = _mapping(raw_window, f"{side} window {index}")
            if window.get("action") != "background":
                continue
            start_ms = seconds_to_milliseconds(
                _finite_number(window.get("start_seconds"), "window start_seconds")
            )
            end_ms = seconds_to_milliseconds(
                _finite_number(window.get("end_seconds"), "window end_seconds")
            )
            if start_ms < 0 or end_ms <= start_ms or end_ms > video_duration_ms:
                raise ActiveLearningError("Background window bounds are invalid.")
            intervals.append((start_ms, end_ms))
        by_side[side] = _merge_intervals(intervals)

    intersections: list[tuple[int, int]] = []
    for far_start, far_end in by_side["far"]:
        for near_start, near_end in by_side["near"]:
            start_ms = max(far_start, near_start)
            end_ms = min(far_end, near_end)
            if end_ms - start_ms >= 5000:
                intersections.append((start_ms, end_ms))
    items: list[dict[str, Any]] = []
    for index, (anchor_start_ms, anchor_end_ms) in enumerate(
        _merge_intervals(intersections), start=1
    ):
        start_ms, end_ms = _fit_clip_bounds(
            anchor_start_ms,
            anchor_end_ms,
            video_duration_ms=video_duration_ms,
            preferred_ms=preferred_ms,
            min_ms=min_ms,
            max_ms=max_ms,
        )
        if any(
            _intervals_overlap(start_ms, end_ms, old_start, old_end)
            for old_start, old_end in previous_intervals
        ):
            continue
        midpoint_ms = (anchor_start_ms + anchor_end_ms) // 2
        items.append(
            {
                "stable_id": f"dual-background-{index:06d}-{anchor_start_ms}",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "anchor_start_ms": anchor_start_ms,
                "anchor_end_ms": anchor_end_ms,
                "time_stratum": min(
                    time_strata - 1,
                    midpoint_ms * time_strata // video_duration_ms,
                ),
                "members": [],
                "source_ids": [],
                "duplicate_group_ids": [],
                "conflict_group_ids": [],
                "observed_sides": ["far", "near"],
                "selection_reasons": ["paired_dual_background_interval"],
                "required": False,
            }
        )
    return items


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start_ms, end_ms in sorted(intervals):
        if merged and start_ms <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end_ms)
        else:
            merged.append([start_ms, end_ms])
    return [(start_ms, end_ms) for start_ms, end_ms in merged]


def _accept_item(
    item: dict[str, Any],
    *,
    bucket: str,
    selected: list[dict[str, Any]],
    reserved_sources: set[str],
    reserved_duplicates: set[str],
    reserved_conflicts: set[str],
    min_anchor_gap_ms: int,
    max_ms: int,
    video_duration_ms: int,
    time_strata: int,
) -> bool:
    if (
        reserved_sources.intersection(item["source_ids"])
        or reserved_duplicates.intersection(item["duplicate_group_ids"])
        or reserved_conflicts.intersection(item["conflict_group_ids"])
    ):
        return False
    overlaps = [
        existing
        for existing in selected
        if _intervals_overlap(
            item["start_ms"],
            item["end_ms"],
            existing["start_ms"],
            existing["end_ms"],
        )
    ]
    if overlaps:
        if item["required"] or len(overlaps) != 1:
            return False
        existing = overlaps[0]
        merged_start = min(item["start_ms"], existing["start_ms"])
        merged_end = max(item["end_ms"], existing["end_ms"])
        if merged_end - merged_start > max_ms or any(
            other is not existing
            and _intervals_overlap(
                merged_start,
                merged_end,
                other["start_ms"],
                other["end_ms"],
            )
            for other in selected
        ):
            return False
        existing["start_ms"] = merged_start
        existing["end_ms"] = merged_end
        existing["anchor_start_ms"] = min(
            existing["anchor_start_ms"], item["anchor_start_ms"]
        )
        existing["anchor_end_ms"] = max(
            existing["anchor_end_ms"], item["anchor_end_ms"]
        )
        midpoint_ms = (
            existing["anchor_start_ms"] + existing["anchor_end_ms"]
        ) // 2
        existing["time_stratum"] = min(
            time_strata - 1,
            midpoint_ms * time_strata // video_duration_ms,
        )
        existing["members"].extend(item["members"])
        existing["source_ids"] = sorted(
            set(existing["source_ids"]).union(item["source_ids"])
        )
        existing["duplicate_group_ids"] = sorted(
            set(existing["duplicate_group_ids"]).union(item["duplicate_group_ids"])
        )
        existing["conflict_group_ids"] = sorted(
            set(existing["conflict_group_ids"]).union(item["conflict_group_ids"])
        )
        existing["observed_sides"] = _ordered_sides(
            set(existing["observed_sides"]).union(item["observed_sides"])
        )
        existing["selection_reasons"].append(f"merged_overlap_from:{bucket}")
        reserved_sources.update(item["source_ids"])
        reserved_duplicates.update(item["duplicate_group_ids"])
        reserved_conflicts.update(item["conflict_group_ids"])
        return False

    if any(
        _interval_gap(
            item["anchor_start_ms"],
            item["anchor_end_ms"],
            existing["anchor_start_ms"],
            existing["anchor_end_ms"],
        )
        < min_anchor_gap_ms
        for existing in selected
    ):
        return False
    item["selection_bucket"] = bucket
    selected.append(item)
    reserved_sources.update(item["source_ids"])
    reserved_duplicates.update(item["duplicate_group_ids"])
    reserved_conflicts.update(item["conflict_group_ids"])
    return True


def _stable_rank(seed: int, namespace: str, stable_id: str) -> str:
    value = f"{seed}\0{namespace}\0{stable_id}".encode()
    return hashlib.sha256(value).hexdigest()


def _interleave_time_strata(
    items: Iterable[dict[str, Any]],
    *,
    time_strata: int,
    covered_strata: set[int],
    rank_key: Callable[[dict[str, Any]], object],
    stratum_rank_key: Callable[[int], object],
) -> list[dict[str, Any]]:
    queues = {index: [] for index in range(time_strata)}
    for item in items:
        queues[item["time_stratum"]].append(item)
    for queue in queues.values():
        queue.sort(key=rank_key)
    stratum_order = sorted(
        range(time_strata),
        key=lambda index: (index in covered_strata, stratum_rank_key(index)),
    )
    ordered: list[dict[str, Any]] = []
    while any(queues.values()):
        for index in stratum_order:
            if queues[index]:
                ordered.append(queues[index].pop(0))
    return ordered


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


def format_timecode(seconds: float) -> str:
    milliseconds = seconds_to_milliseconds(seconds)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{millis:03d}"


def _serialize_clip(
    item: dict[str, Any], *, ordinal: int, round_number: int
) -> dict[str, object]:
    start_ms = item["start_ms"]
    end_ms = item["end_ms"]
    members = sorted(item["members"], key=_anchor_member_key)
    primary = min(members, key=_anchor_member_key) if members else None
    anchor = {
        "canonical_event_id": primary["stable_id"] if primary else None,
        "start_seconds": item["anchor_start_ms"] / 1000,
        "end_seconds": item["anchor_end_ms"] / 1000,
        "action": primary["action"] if primary else "background",
        "confidence": primary["confidence"] if primary else None,
        "observed_sides": list(item["observed_sides"]),
    }
    hints = [
        {
            "canonical_event_id": member["stable_id"],
            "absolute_start_seconds": member["start_ms"] / 1000,
            "absolute_end_seconds": member["end_ms"] / 1000,
            "relative_start_seconds": (member["start_ms"] - start_ms) / 1000,
            "relative_end_seconds": (member["end_ms"] - start_ms) / 1000,
            "action": member["action"],
            "confidence": member["confidence"],
            "observed_sides": list(member["observed_sides"]),
            "duplicate_group_id": member["duplicate_group_id"],
            "conflict_group_id": member["conflict_group_id"],
            "source_candidate_ids": list(member["source_event_ids"]),
        }
        for member in members
    ]
    clip_id = f"round-{round_number:02d}-clip-{ordinal:03d}"
    clip = {
        "clip_id": clip_id,
        "ordinal": ordinal,
        "start_seconds": start_ms / 1000,
        "end_seconds": end_ms / 1000,
        "start_time": format_timecode(start_ms / 1000),
        "end_time": format_timecode(end_ms / 1000),
        "duration_seconds": (end_ms - start_ms) / 1000,
        "time_stratum": item["time_stratum"],
        "selection_bucket": item["selection_bucket"],
        "selection_reasons": list(item["selection_reasons"]),
        "proxy_filename": f"clips/{clip_id}.mp4",
        "anchor": anchor,
        "candidate_hints": hints,
        "reserved_source_event_ids": list(item["source_ids"]),
        "reserved_duplicate_group_ids": list(item["duplicate_group_ids"]),
        "reserved_conflict_group_ids": list(item["conflict_group_ids"]),
    }
    return clip


def _anchor_member_key(member: dict[str, Any]) -> tuple[int, int, str]:
    action_rank = (
        MINORITY_ACTIONS.index(member["action"])
        if member["action"] in MINORITY_ACTIONS
        else len(MINORITY_ACTIONS)
    )
    return action_rank, member["start_ms"], member["stable_id"]


def _represented_sides(events: list[dict[str, Any]]) -> set[str]:
    return {
        side
        for item in events
        for side in item["observed_sides"]
    }


def _represented_sides_from_items(items: list[dict[str, Any]]) -> set[str]:
    return {
        side
        for item in items
        for member in item["members"]
        for side in member["observed_sides"]
    }


def _ordered_sides(sides: Iterable[str]) -> list[str]:
    return sorted(set(sides), key=_SIDE_ORDER.__getitem__)


def _sides(value: object, description: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(side not in _SIDE_ORDER for side in value)
        or len(value) != len(set(value))
    ):
        raise ActiveLearningError(f"{description} must list unique far/near scenes.")
    if value != _ordered_sides(value):
        raise ActiveLearningError(f"{description} must use far/near order.")
    return list(value)


def _string_list(value: object, description: str, *, required: bool) -> list[str]:
    if not isinstance(value, list) or (required and not value):
        raise ActiveLearningError(f"{description} must be an array.")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ActiveLearningError(f"{description} must contain non-empty strings.")
    if len(value) != len(set(value)):
        raise ActiveLearningError(f"{description} must be unique.")
    return list(value)


def _optional_string(value: object, description: str) -> str | None:
    if value is None:
        return None
    return _nonempty_string(value, description)


def _integer_ms(value: object, description: str) -> int:
    if type(value) is not int:
        raise ActiveLearningError(f"{description} must be an integer.")
    return value


def _present_list(value: str | None) -> list[str]:
    return [] if value is None else [value]


def _intervals_overlap(
    left_start: int, left_end: int, right_start: int, right_end: int
) -> bool:
    return left_start < right_end and right_start < left_end


def _interval_gap(
    left_start: int, left_end: int, right_start: int, right_end: int
) -> int:
    if _intervals_overlap(left_start, left_end, right_start, right_end):
        return 0
    return max(left_start, right_start) - min(left_end, right_end)


def validate_merged_review_source(
    merged_json_path: str | Path,
    *,
    repo_root: str | Path,
    require_video: bool = True,
) -> dict[str, object]:
    root = Path(repo_root).expanduser().resolve()
    merged_path = _resolved_path(merged_json_path, root, "merged JSON")
    try:
        merged = _load_json_bytes(
            merged_path.read_bytes(), description="merged review source"
        )
        verify_dual_crop_review(merged_path)
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
) -> dict[str, object]:
    root = Path(repo_root).expanduser().resolve()
    output = _resolved_path(output_path, root, "selection output")
    validated = _validate_selection_payload(payload, root, require_video=True)
    _write_new_json(output, validated)
    return validated


def load_review_selection(
    selection_path: str | Path,
    *,
    repo_root: str | Path,
    require_video: bool = True,
) -> dict[str, object]:
    root = Path(repo_root).expanduser().resolve()
    path = _resolved_path(selection_path, root, "selection JSON")
    try:
        payload = _load_json_bytes(path.read_bytes(), description="selection JSON")
    except ActiveLearningError:
        raise
    except OSError as exc:
        raise ActiveLearningError(f"Cannot read selection JSON: {path}") from exc
    return _validate_selection_payload(payload, root, require_video=require_video)


def _validate_selection_payload(
    payload: object,
    repo_root: Path,
    *,
    require_video: bool,
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
    merged_path = _resolved_path(
        selected_source["merged_json"], repo_root, "source merged JSON"
    )
    if _sha256_file(merged_path) != _sha256(
        selected_source["merged_json_sha256"], "merged JSON SHA-256"
    ):
        raise ActiveLearningError("Merged JSON SHA-256 does not match.")
    verified = validate_merged_review_source(
        merged_path, repo_root=repo_root, require_video=require_video
    )
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
    if _has_task_two_surface(selection):
        _validate_task_two_selection(selection, repo_root)
    _validate_json_value(selection, "selection")
    return selection


def _has_task_two_surface(selection: dict[str, Any]) -> bool:
    settings = selection["settings"]
    clips = selection["clips"]
    return bool(
        any(field in settings for field in _TASK_TWO_SETTINGS_FIELDS)
        or any("selection_bucket" in clip for clip in clips)
    )


def _validate_task_two_selection(
    selection: dict[str, Any], repo_root: Path
) -> None:
    settings = _mapping(selection["settings"], "selection settings")
    _require_exact_fields(settings, _TASK_TWO_SETTINGS_FIELDS, "selection settings")
    _selection_settings_ms(
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

    _validate_previous_selection_records(
        selection["previous_selections"],
        current_round=selection["round_number"],
        repo_root=repo_root,
    )
    _validate_task_two_clips(
        selection["clips"],
        round_number=selection["round_number"],
        time_strata=settings["time_strata"],
        video_duration_ms=seconds_to_milliseconds(
            _finite_number(
                selection["video"]["duration_seconds"], "video duration_seconds"
            )
        ),
    )
    _validate_quota_summary(selection["quota_summary"], selection["clips"])
    _validate_coverage(selection["coverage"], selection["clips"])


def _validate_previous_selection_records(
    value: list[object], *, current_round: int, repo_root: Path
) -> None:
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
        previous = load_review_selection(path, repo_root=repo_root)
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


def _validate_task_two_clips(
    value: list[object],
    *,
    round_number: int,
    time_strata: int,
    video_duration_ms: int,
) -> None:
    all_source_ids: set[str] = set()
    all_duplicate_ids: set[str] = set()
    all_conflict_ids: set[str] = set()
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
        if not 5000 <= duration_ms <= 30000:
            raise ActiveLearningError("Task 2 clips must be 5 to 30 seconds long.")
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
            _sides(hint["observed_sides"], "hint observed_sides")
            duplicate_id = _optional_string(
                hint["duplicate_group_id"], "hint duplicate_group_id"
            )
            conflict_id = _optional_string(
                hint["conflict_group_id"], "hint conflict_group_id"
            )
            hint_source_ids.update(
                _string_list(
                    hint["source_candidate_ids"],
                    "hint source_candidate_ids",
                    required=True,
                )
            )
            if duplicate_id is not None:
                hint_duplicate_ids.add(duplicate_id)
            if conflict_id is not None:
                hint_conflict_ids.add(conflict_id)

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


def _validate_coverage(value: dict[str, object], clips: list[object]) -> None:
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
    available_sides = _sides(
        coverage["available_crop_scenes"], "available_crop_scenes"
    )
    selected_sides = _sides(
        coverage["represented_crop_scenes"], "represented_crop_scenes"
    )
    if not set(selected_sides).issubset(available_sides):
        raise ActiveLearningError("Represented crop scenes must be available.")
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
    if covered_minority != available_minority:
        raise ActiveLearningError("All available minority candidates must be covered.")


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


def _mapping(value: object, description: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ActiveLearningError(f"{description} must be a JSON object.")
    return value


def _require_exact_fields(
    value: dict[str, Any], fields: tuple[str, ...], description: str
) -> None:
    if tuple(value) != fields:
        raise ActiveLearningError(
            f"{description} fields must be exactly {', '.join(fields)} in order."
        )


def _nonempty_string(value: object, description: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActiveLearningError(f"{description} must be a non-empty string.")
    return value


def _finite_number(value: object, description: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ActiveLearningError(f"{description} must be a finite number.")
    try:
        finite = math.isfinite(value)
    except OverflowError as exc:
        raise ActiveLearningError(f"{description} must be a finite number.") from exc
    if not finite:
        raise ActiveLearningError(f"{description} must be a finite number.")
    return value


def _validate_json_value(value: object, description: str) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, (int, float)):
        _finite_number(value, description)
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, description)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _validate_json_value(item, description)
        return
    raise ActiveLearningError(f"{description} contains a non-JSON value.")
