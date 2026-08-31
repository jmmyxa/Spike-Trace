from __future__ import annotations

import unittest

from spiketrace._active_learning_review_observations import (
    compose_observation_set,
    merge_visibility_events,
)


class ObservationCompositionTests(unittest.TestCase):
    def test_composes_source_ordered_actions_outcomes_and_participant_relations(self):
        result_set_id = "rangitoto/round-01/result-test"
        original_ref = "clip-001/action-001"
        supplemental_ref = "clip-001/supplemental-001"
        review = {
            "result_set_id": result_set_id,
            "action_observations": [
                _action(original_ref, "block", source_slot=1, source_row=4),
                _action(supplemental_ref, "free_ball"),
            ],
            "outcome_observations": [
                {
                    "outcome_ref": f"{result_set_id}/outcome-001",
                    "related_action_refs": [supplemental_ref],
                    "outcome": "point_lost",
                    "result_type": "free_ball_error",
                    "evidence_basis": "scoreboard",
                    "status": "observed_or_inferred",
                    "note": "比分确认",
                }
            ],
            "visibility_observations": [],
            "action_participants": [
                _participant(original_ref, "track-1", "confirmed"),
                _participant(original_ref, "track-2", "candidate"),
                _participant(original_ref, None, "unresolved"),
            ],
        }

        observations = compose_observation_set(review, {"video": {"video_id": "match"}})

        self.assertEqual(
            tuple(action.action_ref for action in observations.actions),
            (original_ref, supplemental_ref),
        )
        self.assertEqual(observations.actions[0].source_action_slot, 1)
        self.assertIsNone(observations.actions[1].source_action_slot)
        self.assertEqual(observations.actions[0].source_repairs, ({"cell": "A4"},))
        self.assertIsNone(observations.actions[1].raw_values)
        self.assertEqual(
            observations.outcomes[0].related_action_refs, (supplemental_ref,)
        )
        self.assertEqual(observations.outcomes[0].evidence_basis, "scoreboard")
        self.assertEqual(len(observations.actions), 2)
        self.assertEqual(len(observations.participants), 3)
        self.assertEqual(
            tuple(participant.touch_status for participant in observations.participants),
            ("no_touch", "no_touch", "no_touch"),
        )
        self.assertEqual(observations.participants[0].player_number, "10")
        self.assertIsNone(observations.participants[1].player_number)

    def test_keeps_zero_or_one_participant_block_relations_valid(self):
        action_ref = "clip-002/action-001"
        review = {
            "result_set_id": "rangitoto/round-01/result-zero",
            "action_observations": [_action(action_ref, "block", source_slot=1, source_row=16)],
            "outcome_observations": [],
            "visibility_observations": [],
            "action_participants": [],
        }

        observations = compose_observation_set(review, {"video": {"video_id": "match"}})
        one_participant = compose_observation_set(
            {**review, "action_participants": [_participant(action_ref, None, "unresolved")]},
            {"video": {"video_id": "match"}},
        )

        self.assertEqual((), observations.participants)
        self.assertEqual((action_ref,), tuple(action.action_ref for action in observations.actions))
        self.assertEqual(len(one_participant.participants), 1)
        self.assertEqual(one_participant.participants[0].participation, "block_attempt")
        self.assertEqual(one_participant.participants[0].touch_status, "no_touch")

    def test_merges_visibility_by_kind_side_and_exact_one_second_gap(self):
        result_set_id = "rangitoto/round-01/result-merge"
        observations = [
            _visibility(result_set_id, 1, "occlusion", "far", 10.0, 12.0, "a", "first"),
            _visibility(result_set_id, 2, "occlusion", "far", 11.0, 13.0, "b", "second"),
            _visibility(result_set_id, 3, "occlusion", "far", 14.0, 15.0, "c", "second"),
            _visibility(result_set_id, 4, "occlusion", "far", 16.01, 17.0, "d", "fourth"),
            _visibility(result_set_id, 5, "occlusion", "near", 10.0, 13.0, "near", "near"),
            _visibility(result_set_id, 6, "off_camera", "far", 10.0, 13.0, "off", "off camera"),
        ]

        occlusions = merge_visibility_events(observations, result_set_id, "occlusion")
        off_camera = merge_visibility_events(observations, result_set_id, "off_camera")

        self.assertEqual(len(occlusions), 3)
        self.assertEqual(
            [(event.event_ref, event.team_side, event.start_seconds, event.end_seconds)
             for event in occlusions],
            [
                (f"{result_set_id}/occlusion-001", "far", 10.0, 15.0),
                (f"{result_set_id}/occlusion-002", "far", 16.01, 17.0),
                (f"{result_set_id}/occlusion-003", "near", 10.0, 13.0),
            ],
        )
        self.assertEqual(occlusions[0].duration_seconds, 5.0)
        self.assertEqual(occlusions[0].related_action_refs, ("a", "b", "c"))
        self.assertEqual(
            occlusions[0].source_intervals,
            ((10.0, 12.0), (11.0, 13.0), (14.0, 15.0)),
        )
        self.assertEqual(occlusions[0].note, "first | second")
        self.assertEqual(len(off_camera), 1)
        self.assertEqual(off_camera[0].event_ref, f"{result_set_id}/off-camera-001")

    def test_prefers_clip_bounds_scope_when_any_merged_source_is_clip_bounded(self):
        result_set_id = "rangitoto/round-01/result-scope"
        events = merge_visibility_events(
            [
                _visibility(result_set_id, 1, "occlusion", "far", 20.0, 21.0, "a", "", "timed"),
                _visibility(result_set_id, 2, "occlusion", "far", 21.0, 22.0, "b", "clip", "clip_bounds"),
            ],
            result_set_id,
            "occlusion",
        )

        self.assertEqual(events[0].interval_scope, "clip_bounds")

    def test_composition_deeply_freezes_nested_authority_data(self):
        action_ref = "clip-003/action-001"
        action = _action(action_ref, "block", source_slot=1, source_row=28)
        action["raw_values"] = {"nested": {"values": ["raw"]}}
        action["normalized_values"] = {"nested": {"values": ["normalized"]}}
        action["source_repairs"] = [{"nested": {"values": ["repair"]}}]
        participant = _participant(action_ref, "track-1", "confirmed")
        participant["evidence"] = [{"nested": {"values": ["evidence"]}}]
        review = {
            "result_set_id": "rangitoto/round-01/result-frozen",
            "action_observations": [action],
            "outcome_observations": [],
            "visibility_observations": [],
            "action_participants": [participant],
        }

        observations = compose_observation_set(review, {"video": {"video_id": "match"}})
        action["raw_values"]["nested"]["values"].append("changed")
        action["normalized_values"]["nested"]["values"].append("changed")
        action["source_repairs"][0]["nested"]["values"].append("changed")
        participant["evidence"][0]["nested"]["values"].append("changed")

        composed_action = observations.actions[0]
        composed_participant = observations.participants[0]
        self.assertEqual(composed_action.raw_values["nested"]["values"], ("raw",))
        self.assertEqual(
            composed_action.normalized_values["nested"]["values"], ("normalized",)
        )
        self.assertEqual(
            composed_action.source_repairs[0]["nested"]["values"], ("repair",)
        )
        self.assertEqual(
            composed_participant.evidence[0]["nested"]["values"], ("evidence",)
        )
        with self.assertRaises(TypeError):
            composed_action.raw_values["new"] = "value"
        with self.assertRaises(TypeError):
            composed_action.source_repairs[0]["new"] = "value"
        with self.assertRaises(TypeError):
            composed_participant.evidence[0]["new"] = "value"
        with self.assertRaises(AttributeError):
            composed_action.raw_values["nested"]["values"].append("value")

    def test_counts_distinct_affected_actions_across_visibility_kinds(self):
        result_set_id = "rangitoto/round-01/result-summary"
        occlusion = _visibility(
            result_set_id, 1, "occlusion", "far", 10.0, 11.0, "shared", "occlusion"
        )
        occlusion["related_action_refs"] = ["shared", "occlusion-only"]
        off_camera = _visibility(
            result_set_id, 2, "off_camera", "far", 20.0, 21.0, "shared", "off-camera"
        )
        off_camera["related_action_refs"] = ["shared", "off-camera-only"]
        observations = compose_observation_set(
            {
                "result_set_id": result_set_id,
                "action_observations": [],
                "outcome_observations": [],
                "visibility_observations": [occlusion, off_camera],
                "action_participants": [],
            },
            {"video": {"video_id": "match"}},
        )
        empty = compose_observation_set(
            {
                "result_set_id": "rangitoto/round-01/result-empty",
                "action_observations": [],
                "outcome_observations": [],
                "visibility_observations": [],
                "action_participants": [],
            },
            {"video": {"video_id": "match"}},
        )

        self.assertEqual(observations.affected_action_count, 3)
        self.assertEqual(empty.affected_action_count, 0)


def _action(
    action_ref: str,
    label: str,
    *,
    source_slot: int | None = None,
    source_row: int | None = None,
) -> dict[str, object]:
    supplemental = source_slot is None
    return {
        "action_ref": action_ref,
        "clip_id": action_ref.split("/")[0],
        "source_action_slot": source_slot,
        "source_row": source_row,
        "raw_values": None if supplemental else {"review_label": label},
        "normalized_values": None if supplemental else {"review_label": label},
        "review_label": label,
        "relative_start_seconds": 1,
        "relative_end_seconds": 2,
        "start_seconds": 101.0,
        "end_seconds": 102.0,
        "team_side": "far",
        "visibility": "direct_clear",
        "evidence_basis": "direct_video",
        "interval_scope": "timed",
        "background_scope": None,
        "side_inherited": False,
        "note": "",
        "source_reason": "supplemental" if supplemental else None,
        "source_repairs": [] if supplemental else [{"cell": "A4"}],
    }


def _participant(
    action_ref: str, track_id: str | None, assignment_status: str
) -> dict[str, object]:
    confirmed = assignment_status == "confirmed"
    candidate = assignment_status == "candidate"
    return {
        "action_ref": action_ref,
        "track_id": track_id,
        "identity_ref": "rangitoto:10" if confirmed else None,
        "player_number": "10" if confirmed else None,
        "participation": "block_attempt",
        "touch_status": "no_touch",
        "assignment_status": assignment_status,
        "assignment_confidence": 1.0 if confirmed else (0.5 if candidate else None),
        "evidence": [{"kind": "manual_review", "source_ref": "review", "value": "jump", "confidence": 1.0}],
    }


def _visibility(
    result_set_id: str,
    index: int,
    event_kind: str,
    team_side: str,
    start: float,
    end: float,
    action_ref: str,
    note: str,
    interval_scope: str = "timed",
) -> dict[str, object]:
    return {
        "visibility_ref": f"{result_set_id}/{event_kind}-source-{index:03d}",
        "event_kind": event_kind,
        "clip_id": "clip-001",
        "team_side": team_side,
        "start_seconds": start,
        "end_seconds": end,
        "interval_scope": interval_scope,
        "related_action_refs": [action_ref],
        "note": note,
        "source_reason": "reviewed",
    }


if __name__ == "__main__":
    unittest.main()
