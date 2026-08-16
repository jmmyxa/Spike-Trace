from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

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
