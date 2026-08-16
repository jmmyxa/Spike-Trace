import unittest

from spiketrace.domain import ActionWindow
from spiketrace.events import (
    merge_action_windows,
    merge_action_windows_with_provenance,
)


class MergeActionWindowsTests(unittest.TestCase):
    def test_provenance_does_not_reassign_windows_across_an_interruption(self):
        windows = [
            ActionWindow(0.0, 1.0, "attack", 0.9),
            ActionWindow(0.4, 1.4, "set", 0.8),
            ActionWindow(0.8, 1.8, "attack", 0.7),
        ]
        events, provenance = merge_action_windows_with_provenance(
            windows,
            video_id="match",
            model_version="test-v1",
            confidence_threshold=0.5,
            merge_gap_seconds=0.25,
        )
        self.assertEqual(
            [event.action for event in events], ["attack", "set", "attack"]
        )
        self.assertEqual(
            provenance,
            {"evt_000001": [0], "evt_000002": [1], "evt_000003": [2]},
        )
        self.assertEqual(
            len({index for values in provenance.values() for index in values}), 3
        )

    def test_provenance_excludes_windows_without_retained_event_membership(self):
        windows = [
            ActionWindow(0.0, 1.0, "background", 0.99),
            ActionWindow(0.0, 1.0, "serve", 0.4),
            ActionWindow(1.0, 1.0, "serve", 0.9),
            ActionWindow(2.0, 2.1, "set", 0.9),
            ActionWindow(3.0, 4.0, "serve", 0.9),
        ]
        _, provenance = merge_action_windows_with_provenance(
            windows,
            video_id="match",
            model_version="test-v1",
            confidence_threshold=0.5,
            min_event_seconds=0.2,
        )

        self.assertEqual(provenance, {"evt_000001": [4]})

    def test_filters_background_and_merges_adjacent_actions(self):
        windows = [
            ActionWindow(0.0, 1.0, "background", 0.99),
            ActionWindow(1.0, 2.0, "serve", 0.8),
            ActionWindow(1.5, 2.5, "serve", 0.6),
            ActionWindow(3.0, 4.0, "attack", 0.4),
        ]

        events = merge_action_windows(
            windows,
            video_id="match",
            model_version="test-v1",
            confidence_threshold=0.5,
            merge_gap_seconds=0.1,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].action, "serve")
        self.assertEqual(events[0].start_ms, 1000)
        self.assertEqual(events[0].end_ms, 2500)
        self.assertAlmostEqual(events[0].confidence, 0.7)
        self.assertEqual(events[0].event_id, "evt_000001")

    def test_keeps_separate_actions(self):
        windows = [
            ActionWindow(0.0, 1.0, "serve", 0.9),
            ActionWindow(1.0, 2.0, "receive", 0.8),
        ]
        events = merge_action_windows(
            windows,
            video_id="match",
            model_version="test-v1",
            confidence_threshold=0.5,
        )
        self.assertEqual([event.action for event in events], ["serve", "receive"])

    def test_uses_half_up_rounding_at_exact_half_millisecond(self):
        events, _ = merge_action_windows_with_provenance(
            [ActionWindow(0.0005, 1.0005, "serve", 0.9)],
            video_id="match",
            model_version="test-v1",
            confidence_threshold=0.5,
        )
        self.assertEqual((events[0].start_ms, events[0].end_ms), (1, 1001))


if __name__ == "__main__":
    unittest.main()
