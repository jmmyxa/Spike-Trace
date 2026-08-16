from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ._active_learning_selection_artifact import (
    _load_previous_selections,
    _selection_events,
    validate_merged_review_source,
    write_review_selection,
)
from ._active_learning_selection_contract import (
    MINORITY_ACTIONS,
    ROUND_ONE_QUOTAS,
    _finite_number,
    _fit_clip_bounds,
    _interval_gap,
    _intervals_overlap,
    _mapping,
    _ordered_sides,
    _present_list,
    _represented_sides,
    _represented_sides_from_items,
    _selection_settings_ms,
    format_timecode,
)
from .dual_crop_review import verify_dual_crop_review
from .errors import ActiveLearningError
from .events import seconds_to_milliseconds


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
    _verifier: Callable[..., object] = verify_dual_crop_review,
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
    verified = validate_merged_review_source(
        merged_json_path, repo_root=root, _verifier=_verifier
    )
    merged = _mapping(verified["merged"], "merged review source")
    video = _mapping(verified["video"], "selection video")
    video_duration_ms = seconds_to_milliseconds(
        _finite_number(video["duration_seconds"], "video duration_seconds")
    )
    if video_duration_ms <= 0:
        raise ActiveLearningError("Video duration must be positive.")

    previous_records, previous_intervals = _load_previous_selections(
        previous_selection_paths,
        repo_root=root,
        current_video=video,
        verifier=_verifier,
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
    return write_review_selection(
        payload, output_path, repo_root=root, _verifier=_verifier
    )


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
    uncovered: list[dict[str, Any]] = []
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
        except ActiveLearningError:
            uncovered.extend(members)
            continue
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
    if uncovered:
        uncovered.sort(
            key=lambda item: (
                MINORITY_ACTIONS.index(item["action"]),
                item["start_ms"],
                item["stable_id"],
            )
        )
        raise ActiveLearningError(
            "Required minority clusters cannot fit in legal clips: "
            + ", ".join(item["stable_id"] for item in uncovered)
        )
    return items


def _minority_events_link(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if (
        left["clip_start_ms"] is not None
        and right["clip_start_ms"] is not None
        and _intervals_overlap(
            left["clip_start_ms"],
            left["clip_end_ms"],
            right["clip_start_ms"],
            right["clip_end_ms"],
        )
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
    anchors = [
        (anchor, interval[1] - interval[0] > max_ms)
        for interval in _merge_intervals(intersections)
        for anchor in _split_background_interval(
            *interval,
            preferred_ms=preferred_ms,
            min_ms=min_ms,
            max_ms=max_ms,
        )
    ]
    items: list[dict[str, Any]] = []
    for index, (anchor, is_long_interval) in enumerate(anchors, start=1):
        anchor_start_ms, anchor_end_ms = anchor
        if is_long_interval:
            start_ms, end_ms = anchor_start_ms, anchor_end_ms
        else:
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


def _split_background_interval(
    start_ms: int,
    end_ms: int,
    *,
    preferred_ms: int,
    min_ms: int,
    max_ms: int,
) -> list[tuple[int, int]]:
    duration_ms = end_ms - start_ms
    if duration_ms <= max_ms:
        return [(start_ms, end_ms)]
    target_ms = min(max_ms, max(min_ms, preferred_ms))
    minimum_count = (duration_ms + max_ms - 1) // max_ms
    maximum_count = duration_ms // min_ms
    preferred_count = max(2, duration_ms // target_ms)
    count = min(max(preferred_count, minimum_count), maximum_count)
    if count < minimum_count:
        return [(start_ms, start_ms + max_ms)]
    base_duration, longer_segments = divmod(duration_ms, count)
    anchors: list[tuple[int, int]] = []
    cursor = start_ms
    for index in range(count):
        segment_duration = base_duration + (index < longer_segments)
        anchors.append((cursor, cursor + segment_duration))
        cursor += segment_duration
    return anchors


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
        merge_reason = f"merged_overlap_from:{bucket}"
        if merge_reason not in existing["selection_reasons"]:
            existing["selection_reasons"].append(merge_reason)
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
