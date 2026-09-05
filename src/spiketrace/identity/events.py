from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from ..domain import ActionEvent
from .models import IdentityAssignment


def apply_identity_assignments(
    events: Iterable[ActionEvent],
    assignments: Iterable[IdentityAssignment],
    *,
    require_confirmed: bool = True,
) -> list[ActionEvent]:
    """Copy confirmed identity fields onto overlapping action events.

    The action event remains the owner of action metadata. If several tracks
    overlap, the highest-confidence assignment is selected deterministically.
    ``team_side`` remains the near/far court position; team identity is not
    collapsed into that field.
    """
    assignment_items = tuple(assignments)
    output: list[ActionEvent] = []
    for event in events:
        overlaps = [
            item
            for item in assignment_items
            if item.start_ms < event.end_ms and item.end_ms > event.start_ms
            and (not require_confirmed or item.confirmed)
            and item.team == "usa"
        ]
        if not overlaps:
            output.append(event)
            continue
        selected = max(overlaps, key=lambda item: (item.assignment_confidence, item.track_id))
        output.append(
            replace(
                event,
                team_side=selected.court_side if selected.court_side in {"near", "far"} else None,
                player_number=selected.number,
            )
        )
    return output


# Descriptive alias for callers that prefer a join-oriented name.
map_assignments_to_events = apply_identity_assignments
