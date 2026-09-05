import unittest

from spiketrace.domain import ActionEvent
from spiketrace.identity import (
    IdentityAssignment,
    NumberObservation,
    PlayerDetection,
    Track,
    aggregate_number_candidates,
    apply_identity_assignments,
)


class IdentityModelTests(unittest.TestCase):
    def test_court_side_and_team_are_independent(self):
        detection = PlayerDetection(
            frame_index=10,
            timestamp_ms=333,
            box_xyxy=(1, 2, 30, 80),
            confidence=0.9,
            court_side="near",
            team="unknown",
        )
        self.assertEqual(detection.court_side, "near")
        self.assertEqual(detection.team, "unknown")

    def test_track_requires_monotonic_detection_times(self):
        first = PlayerDetection(1, 100, (0, 0, 10, 20), 0.8)
        second = PlayerDetection(2, 90, (0, 0, 10, 20), 0.8)
        with self.assertRaises(ValueError):
            Track("trk-1", (first, second))

    def test_temporal_number_aggregation_preserves_leading_zero(self):
        resolution = aggregate_number_candidates(
            [
                NumberObservation(100, "08", 0.9),
                NumberObservation(200, "O8", 0.8),
                NumberObservation(300, "8", 0.7),
            ],
            roster=["08", "18"],
        )
        self.assertEqual(resolution.number, "08")
        self.assertEqual(resolution.status, "confirmed")

    def test_conflicting_numbers_remain_candidate(self):
        resolution = aggregate_number_candidates(
            [NumberObservation(100, "08", 0.8), NumberObservation(200, "18", 0.8)],
            roster=["08", "18"],
        )
        self.assertIsNone(resolution.number)
        self.assertEqual(resolution.status, "candidate")


class IdentityEventAdapterTests(unittest.TestCase):
    def setUp(self):
        self.event = ActionEvent(
            video_id="match",
            event_id="evt_000001",
            start_ms=1000,
            end_ms=2000,
            action="attack",
            confidence=0.9,
            team_side=None,
            player_number=None,
            status="predicted",
            model_version="test-v1",
        )

    def assignment(self, **overrides):
        values = dict(
            track_id="trk-1",
            start_ms=1100,
            end_ms=1900,
            court_side="near",
            team="usa",
            identity_ref="usa:08",
            number="08",
            identity_status="confirmed",
            number_status="confirmed",
            assignment_confidence=0.8,
            number_confidence=0.9,
            visibility_state="back_only",
        )
        values.update(overrides)
        return IdentityAssignment(**values)

    def test_maps_only_confirmed_usa_assignment(self):
        [event] = apply_identity_assignments([self.event], [self.assignment()])
        self.assertEqual(event.team_side, "near")
        self.assertEqual(event.player_number, "08")

    def test_unknown_or_opponent_does_not_pollute_event(self):
        result = apply_identity_assignments(
            [self.event],
            [self.assignment(team="opponent"), self.assignment(track_id="trk-2", identity_status="candidate")],
        )
        self.assertIsNone(result[0].team_side)
        self.assertIsNone(result[0].player_number)

