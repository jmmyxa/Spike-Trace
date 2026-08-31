from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Self

_VISIBILITY_MERGE_GAP_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class ActionObservation:
    action_ref: str
    clip_id: str
    source_action_slot: int | None
    source_row: int | None
    raw_values: dict[str, object]
    normalized_values: dict[str, object]
    review_label: str
    relative_start_seconds: int | None
    relative_end_seconds: int | None
    start_seconds: float | None
    end_seconds: float | None
    team_side: str
    visibility: str
    evidence_basis: str
    interval_scope: str | None
    background_scope: str | None
    side_inherited: bool
    note: str
    source_reason: str | None
    source_repairs: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class OutcomeObservation:
    outcome_ref: str
    related_action_refs: tuple[str, ...]
    outcome: str
    result_type: str | None
    evidence_basis: str
    status: str
    note: str


@dataclass(frozen=True, slots=True)
class VisibilityObservation:
    visibility_ref: str
    event_kind: str
    clip_id: str
    team_side: str
    start_seconds: float
    end_seconds: float
    interval_scope: str
    related_action_refs: tuple[str, ...]
    note: str
    source_reason: str


@dataclass(frozen=True, slots=True)
class VisibilityEvent:
    event_ref: str
    event_kind: str
    team_side: str
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    interval_scope: str
    related_action_refs: tuple[str, ...]
    source_refs: tuple[str, ...]
    source_intervals: tuple[tuple[float, float], ...]
    note: str


@dataclass(frozen=True, slots=True)
class ActionParticipant:
    action_ref: str
    track_id: str | None
    identity_ref: str | None
    player_number: str | None
    participation: str
    touch_status: str
    assignment_status: str
    assignment_confidence: float | None
    evidence: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class ObservationSet:
    result_set_id: str
    actions: tuple[ActionObservation, ...]
    outcomes: tuple[OutcomeObservation, ...]
    visibility_observations: tuple[VisibilityObservation, ...]
    occlusion_events: tuple[VisibilityEvent, ...]
    off_camera_events: tuple[VisibilityEvent, ...]
    participants: tuple[ActionParticipant, ...]

    @property
    def affected_action_count(self) -> int:
        return len({
            action_ref
            for event in self.occlusion_events + self.off_camera_events
            for action_ref in event.related_action_refs
        })


def compose_observation_set(review: object, selection: object) -> ObservationSet:
    """Compose validated review records into immutable observation relations."""
    del selection
    visibility_observations = tuple(
        _visibility_observation(value)
        for value in _field(review, "visibility_observations")
    )
    result_set_id = _field(review, "result_set_id")
    return ObservationSet(
        result_set_id=result_set_id,
        actions=tuple(
            _action_observation(value)
            for value in _field(review, "action_observations")
        ),
        outcomes=tuple(
            _outcome_observation(value)
            for value in _field(review, "outcome_observations")
        ),
        visibility_observations=visibility_observations,
        occlusion_events=merge_visibility_events(
            visibility_observations, result_set_id, "occlusion"
        ),
        off_camera_events=merge_visibility_events(
            visibility_observations, result_set_id, "off_camera"
        ),
        participants=tuple(
            _action_participant(value)
            for value in _field(review, "action_participants")
        ),
    )


def merge_visibility_events(
    observations: Iterable[VisibilityObservation | dict[str, object]],
    result_set_id: str,
    event_kind: str,
) -> tuple[VisibilityEvent, ...]:
    """Merge same-clip, same-side intervals for one visibility event kind."""
    matching = [
        _visibility_observation(value)
        for value in observations
        if _field(value, "event_kind") == event_kind
    ]
    by_clip_side: dict[tuple[str, str], list[VisibilityObservation]] = {}
    for observation in matching:
        by_clip_side.setdefault(
            (observation.clip_id, observation.team_side), []
        ).append(observation)

    grouped = [
        sorted(values, key=lambda value: (value.start_seconds, value.end_seconds, value.visibility_ref))
        for values in by_clip_side.values()
    ]
    grouped.sort(
        key=lambda values: (
            values[0].start_seconds,
            values[0].end_seconds,
            values[0].visibility_ref,
        )
    )
    merged_sources: list[list[VisibilityObservation]] = []
    for side_observations in grouped:
        current: list[VisibilityObservation] = []
        current_end = 0.0
        for observation in side_observations:
            if not current or observation.start_seconds <= current_end + _VISIBILITY_MERGE_GAP_SECONDS:
                current.append(observation)
                current_end = max(current_end, observation.end_seconds)
                continue
            merged_sources.append(current)
            current = [observation]
            current_end = observation.end_seconds
        if current:
            merged_sources.append(current)

    event_label = "off-camera" if event_kind == "off_camera" else event_kind
    return tuple(
        _visibility_event(
            sources,
            f"{result_set_id}/{event_label}-{index:03d}",
            event_kind,
        )
        for index, sources in enumerate(merged_sources, 1)
    )


def _action_observation(value: object) -> ActionObservation:
    return ActionObservation(
        action_ref=_field(value, "action_ref"),
        clip_id=_field(value, "clip_id"),
        source_action_slot=_field(value, "source_action_slot"),
        source_row=_field(value, "source_row"),
        raw_values=_freeze(_field(value, "raw_values")),
        normalized_values=_freeze(_field(value, "normalized_values")),
        review_label=_field(value, "review_label"),
        relative_start_seconds=_field(value, "relative_start_seconds"),
        relative_end_seconds=_field(value, "relative_end_seconds"),
        start_seconds=_field(value, "start_seconds"),
        end_seconds=_field(value, "end_seconds"),
        team_side=_field(value, "team_side"),
        visibility=_field(value, "visibility"),
        evidence_basis=_field(value, "evidence_basis"),
        interval_scope=_field(value, "interval_scope"),
        background_scope=_field(value, "background_scope"),
        side_inherited=_field(value, "side_inherited"),
        note=_field(value, "note"),
        source_reason=_field(value, "source_reason"),
        source_repairs=tuple(
            _freeze(repair) for repair in _field(value, "source_repairs")
        ),
    )


def _outcome_observation(value: object) -> OutcomeObservation:
    return OutcomeObservation(
        outcome_ref=_field(value, "outcome_ref"),
        related_action_refs=tuple(_field(value, "related_action_refs")),
        outcome=_field(value, "outcome"),
        result_type=_field(value, "result_type"),
        evidence_basis=_field(value, "evidence_basis"),
        status=_field(value, "status"),
        note=_field(value, "note"),
    )


def _visibility_observation(value: VisibilityObservation | dict[str, object]) -> VisibilityObservation:
    if isinstance(value, VisibilityObservation):
        return value
    return VisibilityObservation(
        visibility_ref=_field(value, "visibility_ref"),
        event_kind=_field(value, "event_kind"),
        clip_id=_field(value, "clip_id"),
        team_side=_field(value, "team_side"),
        start_seconds=_field(value, "start_seconds"),
        end_seconds=_field(value, "end_seconds"),
        interval_scope=_field(value, "interval_scope"),
        related_action_refs=tuple(_field(value, "related_action_refs")),
        note=_field(value, "note"),
        source_reason=_field(value, "source_reason"),
    )


def _action_participant(value: object) -> ActionParticipant:
    return ActionParticipant(
        action_ref=_field(value, "action_ref"),
        track_id=_field(value, "track_id"),
        identity_ref=_field(value, "identity_ref"),
        player_number=_field(value, "player_number"),
        participation=_field(value, "participation"),
        touch_status=_field(value, "touch_status"),
        assignment_status=_field(value, "assignment_status"),
        assignment_confidence=_field(value, "assignment_confidence"),
        evidence=tuple(_freeze(item) for item in _field(value, "evidence")),
    )


def _visibility_event(
    sources: list[VisibilityObservation], event_ref: str, event_kind: str
) -> VisibilityEvent:
    ordered_sources = sorted(sources, key=lambda value: value.visibility_ref)
    notes = tuple(dict.fromkeys(
        value.note for value in ordered_sources if value.note
    ))
    start_seconds = min(value.start_seconds for value in ordered_sources)
    end_seconds = max(value.end_seconds for value in ordered_sources)
    return VisibilityEvent(
        event_ref=event_ref,
        event_kind=event_kind,
        team_side=ordered_sources[0].team_side,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        duration_seconds=end_seconds - start_seconds,
        interval_scope=(
            "clip_bounds"
            if any(value.interval_scope == "clip_bounds" for value in ordered_sources)
            else "timed"
        ),
        related_action_refs=tuple(sorted({
            action_ref
            for value in ordered_sources
            for action_ref in value.related_action_refs
        })),
        source_refs=tuple(value.visibility_ref for value in ordered_sources),
        source_intervals=tuple(
            (value.start_seconds, value.end_seconds) for value in ordered_sources
        ),
        note=" | ".join(notes),
    )


def _field(value: object, name: str) -> Any:
    if isinstance(value, dict):
        return value[name]
    return getattr(value, name)


class _FrozenDict(dict[str, object]):
    def __init__(self, values: dict[str, object]) -> None:
        dict.__init__(self, values)

    def __delitem__(self, key: str) -> None:
        raise TypeError("Frozen mappings cannot be modified.")

    def __ior__(self, value: object) -> Self:
        raise TypeError("Frozen mappings cannot be modified.")

    def __setitem__(self, key: str, value: object) -> None:
        raise TypeError("Frozen mappings cannot be modified.")

    def clear(self) -> None:
        raise TypeError("Frozen mappings cannot be modified.")

    def pop(self, key: str, default: object = None) -> object:
        raise TypeError("Frozen mappings cannot be modified.")

    def popitem(self) -> tuple[str, object]:
        raise TypeError("Frozen mappings cannot be modified.")

    def setdefault(self, key: str, default: object = None) -> object:
        raise TypeError("Frozen mappings cannot be modified.")

    def update(self, *args: object, **kwargs: object) -> None:
        raise TypeError("Frozen mappings cannot be modified.")


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return _FrozenDict({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value
