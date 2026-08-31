from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

from ._active_learning_review_observations import ObservationSet
from .constants import ACTION_LABELS


@dataclass(frozen=True, slots=True)
class TrainingDecision:
    decision: str
    training_label: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class ProtectedInterval:
    source_ref: str
    clip_id: str
    team_side: str
    start_seconds: float
    end_seconds: float
    reason: str


@dataclass(frozen=True, slots=True)
class TrainingWindow:
    source_ref: str
    clip_id: str
    start_seconds: float
    end_seconds: float
    training_label: str
    review_label: str
    team_side: str
    crop: tuple[int, int, int, int]
    player_number: str | None
    generated: bool
    window_index: int | None
    source_top1_action: str | None
    source_top1_confidence: float | None
    note: str


@dataclass(frozen=True, slots=True)
class TrainingProjection:
    decisions: tuple[tuple[str, TrainingDecision], ...]
    human_windows: tuple[TrainingWindow, ...]
    protected_intervals: tuple[ProtectedInterval, ...]
    generated_background_windows: tuple[TrainingWindow, ...]
    positive_training_count: int
    requested_background_cap: int
    effective_background_cap: int


def derive_training_decision(action: object) -> TrainingDecision:
    """Fail closed unless a timed action is directly visible in video."""
    visibility = _field(action, "visibility")
    evidence_basis = _field(action, "evidence_basis")
    review_label = _field(action, "review_label")
    if (
        visibility not in {"direct_clear", "direct_partial"}
        or evidence_basis != "direct_video"
    ):
        return TrainingDecision("excluded", None, "insufficient_visual_evidence")
    if review_label == "free_ball":
        return TrainingDecision(
            "eligible_as_background",
            "background",
            "free_ball_projects_to_background",
        )
    if review_label == "background" and _field(action, "background_scope") == "clip_sentinel":
        return TrainingDecision("excluded", None, "background_sentinel_only")
    return TrainingDecision("eligible", review_label, "direct_visual")


def build_protected_intervals(
    observations: ObservationSet, selection: object
) -> tuple[ProtectedInterval, ...]:
    """Preserve every timed human observation and unavailable visual range."""
    clip_bounds = _clip_bounds(selection)
    protected: list[ProtectedInterval] = []
    for action in observations.actions:
        if action.start_seconds is None or action.end_seconds is None:
            continue
        protected.append(
            ProtectedInterval(
                action.action_ref,
                action.clip_id,
                action.team_side,
                action.start_seconds,
                action.end_seconds,
                "human_observation",
            )
        )
    for event in observations.occlusion_events + observations.off_camera_events:
        start, end = event.start_seconds, event.end_seconds
        reason = event.event_kind
        if event.interval_scope == "clip_bounds":
            start, end = clip_bounds[event_clip_id(event, clip_bounds)]
            reason = f"{reason}_clip_bounds"
        protected.append(
            ProtectedInterval(
                event.event_ref,
                event_clip_id(event, clip_bounds),
                event.team_side,
                start,
                end,
                reason,
            )
        )
    return tuple(protected)


def project_training_windows(
    observations: ObservationSet, selection: object
) -> tuple[TrainingWindow, ...]:
    """Create one human training window for every eligible timed action."""
    crops = _crops(selection)
    participants = _confirmed_player_numbers(observations)
    windows: list[TrainingWindow] = []
    for action in observations.actions:
        decision = derive_training_decision(action)
        if (
            decision.training_label is None
            or action.start_seconds is None
            or action.end_seconds is None
        ):
            continue
        windows.append(
            TrainingWindow(
                action.action_ref,
                action.clip_id,
                action.start_seconds,
                action.end_seconds,
                decision.training_label,
                action.review_label,
                action.team_side,
                crops[action.team_side],
                participants.get(action.action_ref),
                False,
                None,
                None,
                None,
                action.note,
            )
        )
    return tuple(windows)


def select_hard_negatives(
    selection: object,
    merged: object,
    observations: ObservationSet,
    protected_intervals: tuple[ProtectedInterval, ...],
    guard_seconds: float,
    cap: int,
    seed: int,
) -> tuple[TrainingWindow, ...]:
    """Select only model windows donated by an otherwise empty sentinel clip."""
    _selection_source(selection)
    if cap == 0:
        return ()
    if not _is_nonnegative_finite(guard_seconds):
        raise ValueError("guard_seconds must be finite and nonnegative.")
    if type(cap) is not int or cap < 0:
        raise ValueError("cap must be a nonnegative integer.")
    if type(seed) is not int:
        raise ValueError("seed must be an integer.")

    clips = _clip_bounds(selection)
    crops = _crops(selection)
    donors = _sentinel_donors(observations, clips)
    input_runs = _mapping(_mapping(merged, "merged")["input_runs"], "merged input_runs")
    candidates: list[tuple[tuple[int, float, str], TrainingWindow]] = []
    for clip_id, side in donors:
        run = _mapping(input_runs[side], f"merged {side} input run")
        windows = run["windows"]
        if not isinstance(windows, list):
            raise TypeError(f"Merged {side} windows must be an array.")
        clip_start, clip_end = clips[clip_id]
        for raw_window in windows:
            window_index, start, end, action, confidence = _candidate_fields(raw_window, side)
            if start < clip_start or end > clip_end:
                continue
            if _conflicts_with_protection(start, end, side, protected_intervals, float(guard_seconds)):
                continue
            candidates.append(
                (
                    (
                        0 if action != "background" else 1,
                        -confidence if action != "background" else 0.0,
                        _rank_tie(seed, clip_id, side, window_index),
                    ),
                    TrainingWindow(
                        f"{clip_id}/hard-negative-{side}-{window_index}",
                        clip_id,
                        start,
                        end,
                        "background",
                        "background",
                        side,
                        crops[side],
                        None,
                        True,
                        window_index,
                        action,
                        confidence,
                        "",
                    ),
                )
            )
    candidates.sort(key=lambda candidate: candidate[0])
    chosen: list[TrainingWindow] = []
    for _, candidate in candidates:
        if any(
            candidate.team_side == existing.team_side
            and _overlaps(
                candidate.start_seconds,
                candidate.end_seconds,
                existing.start_seconds,
                existing.end_seconds,
            )
            for existing in chosen
        ):
            continue
        chosen.append(candidate)
        if len(chosen) == cap:
            break
    return tuple(chosen)


def build_training_projection(
    observations: ObservationSet,
    selection: object,
    merged: object,
    background_guard_seconds: float,
    max_background_windows: int | None,
    background_seed: int | None,
) -> TrainingProjection:
    """Derive the immutable training-only view of evidence-aware observations."""
    decisions = tuple(
        (action.action_ref, derive_training_decision(action))
        for action in observations.actions
    )
    human_windows = project_training_windows(observations, selection)
    protected = build_protected_intervals(observations, selection)
    positive_count = sum(window.training_label != "background" for window in human_windows)
    if max_background_windows is not None and (
        type(max_background_windows) is not int or max_background_windows < 0
    ):
        raise ValueError("max_background_windows must be a nonnegative integer or None.")
    requested_cap = positive_count if max_background_windows is None else max_background_windows
    effective_cap = min(requested_cap, positive_count)
    selected_seed = _seed(selection) if background_seed is None else background_seed
    generated = select_hard_negatives(
        selection,
        merged,
        observations,
        protected,
        background_guard_seconds,
        effective_cap,
        selected_seed,
    )
    return TrainingProjection(
        decisions,
        human_windows,
        protected,
        generated,
        positive_count,
        requested_cap,
        effective_cap,
    )


def _sentinel_donors(
    observations: ObservationSet, clips: dict[str, tuple[float, float]]
) -> tuple[tuple[str, str], ...]:
    by_clip: dict[str, list[object]] = {}
    for action in observations.actions:
        by_clip.setdefault(action.clip_id, []).append(action)
    donors = []
    for clip_id, actions in by_clip.items():
        if clip_id not in clips or len(actions) != 1:
            continue
        action = actions[0]
        if (
            action.review_label == "background"
            and action.background_scope == "clip_sentinel"
            and action.start_seconds is None
            and action.end_seconds is None
        ):
            donors.append((clip_id, action.team_side))
    return tuple(sorted(donors))


def _confirmed_player_numbers(observations: ObservationSet) -> dict[str, str | None]:
    grouped: dict[str, list[str | None]] = {}
    for participant in observations.participants:
        if participant.assignment_status == "confirmed":
            grouped.setdefault(participant.action_ref, []).append(participant.player_number)
    return {
        action_ref: numbers[0] if len(numbers) == 1 else None
        for action_ref, numbers in grouped.items()
    }


def _candidate_fields(raw_window: object, side: str) -> tuple[int, float, float, str, float]:
    window = _mapping(raw_window, f"merged {side} window")
    window_index = window.get("window_index")
    start = window.get("start_seconds")
    end = window.get("end_seconds")
    action = window.get("action")
    confidence = window.get("confidence")
    if (
        type(window_index) is not int
        or window_index < 0
        or not _is_finite(start)
        or not _is_finite(end)
        or float(end) <= float(start)
        or action not in ACTION_LABELS
        or not _is_finite(confidence)
        or not 0 <= float(confidence) <= 1
    ):
        raise ValueError(f"Merged {side} window is invalid.")
    return window_index, float(start), float(end), action, float(confidence)


def _conflicts_with_protection(
    start: float,
    end: float,
    side: str,
    protected: tuple[ProtectedInterval, ...],
    guard: float,
) -> bool:
    return any(
        interval.team_side == side
        and _overlaps(start, end, interval.start_seconds - guard, interval.end_seconds + guard)
        for interval in protected
    )


def _clip_bounds(selection: object) -> dict[str, tuple[float, float]]:
    clips = _mapping(selection, "selection")["clips"]
    return {
        _mapping(clip, "selection clip")["clip_id"]: (
            float(_mapping(clip, "selection clip")["start_seconds"]),
            float(_mapping(clip, "selection clip")["end_seconds"]),
        )
        for clip in clips
    }


def _crops(selection: object) -> dict[str, tuple[int, int, int, int]]:
    crops = _mapping(_mapping(selection, "selection")["video"], "selection video")["crops"]
    return {
        side: tuple(crop)
        for side, crop in _mapping(crops, "selection crops").items()
    }


def _selection_source(selection: object) -> None:
    source = _mapping(_mapping(selection, "selection")["source"], "selection source")
    if not isinstance(source.get("merged_json"), str) or not source["merged_json"]:
        raise ValueError("Selection merged candidate path is invalid.")
    digest = source.get("merged_json_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("Selection merged candidate SHA-256 is invalid.")


def _seed(selection: object) -> int:
    seed = _mapping(_mapping(selection, "selection")["settings"], "selection settings").get("seed", 0)
    return seed if type(seed) is int else 0


def event_clip_id(event: object, clip_bounds: dict[str, tuple[float, float]]) -> str:
    matching = [
        clip_id
        for clip_id, (start, end) in clip_bounds.items()
        if start <= _field(event, "start_seconds") and _field(event, "end_seconds") <= end
    ]
    if len(matching) != 1:
        raise ValueError("Visibility event must belong to exactly one selected clip.")
    return matching[0]


def _rank_tie(seed: int, clip_id: str, side: str, window_index: int) -> str:
    return hashlib.sha256(f"{seed}/{clip_id}/{side}/{window_index}".encode()).hexdigest()


def _overlaps(left_start: float, left_end: float, right_start: float, right_end: float) -> bool:
    return left_start < right_end and right_start < left_end


def _mapping(value: object, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{description} must be an object.")
    return value


def _field(value: object, name: str) -> Any:
    return value[name] if isinstance(value, dict) else getattr(value, name)


def _is_finite(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _is_nonnegative_finite(value: object) -> bool:
    return _is_finite(value) and float(value) >= 0
