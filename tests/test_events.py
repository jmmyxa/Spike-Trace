import unittest

from spiketrace.domain import ActionWindow
from spiketrace.events import merge_action_windows


class MergeActionWindowsTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
