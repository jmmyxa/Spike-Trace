import csv
import json
import tempfile
import unittest
from pathlib import Path

from spiketrace.domain import ActionEvent, ActionWindow, VideoMetadata
from spiketrace.outputs import write_inference_outputs


class InferenceOutputTests(unittest.TestCase):
    def setUp(self):
        self.metadata = VideoMetadata(
            path=Path("match.mp4"),
            fps=25.0,
            frame_count=100,
            width=1920,
            height=1080,
            duration_seconds=4.0,
        )
        self.event = ActionEvent(
            video_id="match",
            event_id="evt_000001",
            start_ms=0,
            end_ms=1000,
            action="serve",
            confidence=0.85,
            team_side=None,
            player_number=None,
            status="predicted",
            model_version="test-v1",
        )
        self.windows = [
            ActionWindow(0.0, 0.5, "serve", 0.9),
            ActionWindow(0.5, 1.0, "serve", 0.8),
        ]
        self.settings = {"confidence_threshold": 0.5}

    def write_outputs(self, event_window_indices):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        return write_inference_outputs(
            temporary_directory.name,
            metadata=self.metadata,
            model_version="test-v1",
            events=[self.event],
            windows=self.windows,
            settings=self.settings,
            event_window_indices=event_window_indices,
        )

    def test_serializes_v2_provenance_and_keeps_csv_event_only(self):
        json_path, csv_path = self.write_outputs({"evt_000001": [0, 1]})

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["format_version"], 2)
        self.assertEqual(payload["events"][0]["source_window_indices"], [0, 1])
        self.assertEqual(
            [window["window_index"] for window in payload["windows"]], [0, 1]
        )
        csv_text = csv_path.read_text(encoding="utf-8-sig")
        self.assertNotIn("source_window_indices", csv_text.splitlines()[0])
        self.assertEqual(len(list(csv.DictReader(csv_text.splitlines()))), 1)

    def test_rejects_missing_event_mapping(self):
        with self.assertRaises(ValueError):
            self.write_outputs({})

    def test_rejects_out_of_range_window_index(self):
        with self.assertRaises(ValueError):
            self.write_outputs({"evt_000001": [2]})

    def test_rejects_duplicate_window_member(self):
        with self.assertRaises(ValueError):
            self.write_outputs({"evt_000001": [0, 0]})

    def test_rejects_window_with_a_different_action(self):
        self.windows[1] = ActionWindow(0.5, 1.0, "set", 0.8)
        with self.assertRaises(ValueError):
            self.write_outputs({"evt_000001": [0, 1]})

    def test_rejects_window_below_the_confidence_threshold(self):
        self.windows[1] = ActionWindow(0.5, 1.0, "serve", 0.4)
        with self.assertRaises(ValueError):
            self.write_outputs({"evt_000001": [0, 1]})

    def test_rejects_one_window_assigned_to_two_events(self):
        second_event = ActionEvent(
            video_id="match",
            event_id="evt_000002",
            start_ms=1000,
            end_ms=1500,
            action="serve",
            confidence=0.8,
            team_side=None,
            player_number=None,
            status="predicted",
            model_version="test-v1",
        )
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        with self.assertRaises(ValueError):
            write_inference_outputs(
                temporary_directory.name,
                metadata=self.metadata,
                model_version="test-v1",
                events=[self.event, second_event],
                windows=self.windows,
                settings=self.settings,
                event_window_indices={
                    "evt_000001": [0],
                    "evt_000002": [0],
                },
            )

    def test_rejects_event_bounds_that_do_not_match_members(self):
        self.event = ActionEvent(
            video_id="match", event_id="evt_000001", start_ms=1, end_ms=1000,
            action="serve", confidence=0.85, team_side=None, player_number=None,
            status="predicted", model_version="test-v1",
        )
        with self.assertRaises(ValueError):
            self.write_outputs({"evt_000001": [0, 1]})

    def test_rejects_event_confidence_that_is_not_member_mean(self):
        self.event = ActionEvent(
            video_id="match", event_id="evt_000001", start_ms=0, end_ms=1000,
            action="serve", confidence=0.9, team_side=None, player_number=None,
            status="predicted", model_version="test-v1",
        )
        with self.assertRaises(ValueError):
            self.write_outputs({"evt_000001": [0, 1]})


if __name__ == "__main__":
    unittest.main()
