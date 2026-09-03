import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from spiketrace.domain import VideoMetadata
from spiketrace.errors import ValidationError
from spiketrace.validation_contract import ValidationVideoBinding
from spiketrace.validation_rallies import (
    RallyDetectionSettings,
    RallySegment,
    apply_side_map,
    complete_coverage,
    validate_rally_queue,
    write_rally_proxies,
)


class RallyQueueTests(unittest.TestCase):
    def setUp(self):
        self.metadata = VideoMetadata(Path("fixture.avi"), 10.0, 120, 1920, 1080, 12.0)
        self.binding = ValidationVideoBinding("m", Path("fixture.avi"), Path("."), "fixture.avi", "a" * 64, self.metadata)

    def test_complete_coverage_and_overlap_validation(self):
        segments = complete_coverage(((2.0, 5.0), (8.0, 10.0)), duration_seconds=12.0, binding=self.binding)
        validate_rally_queue(segments, binding=self.binding, require_complete=True)
        self.assertEqual([(s.start_seconds, s.end_seconds) for s in segments], [(0.0, 2.0), (2.0, 5.0), (5.0, 8.0), (8.0, 10.0), (10.0, 12.0)])
        self.assertEqual(segments[0].status, "non_rally")
        with self.assertRaisesRegex(ValidationError, "overlap"):
            validate_rally_queue((*segments[:2], replace(segments[2], start_seconds=4.5)), binding=self.binding)

    def test_side_switch_splits_candidate(self):
        segments = complete_coverage(((2.0, 10.0),), duration_seconds=12.0, binding=self.binding)
        mapped = apply_side_map(segments, set_intervals=[{"set_index": 5, "start_seconds": 1.0, "end_seconds": 12.0}], side_intervals=[{"segment_id": "pre", "set_index": 5, "start_seconds": 1.0, "end_seconds": 6.0, "team_side": "near", "crop": [0, 500, 1920, 1080]}, {"segment_id": "post", "set_index": 5, "start_seconds": 6.0, "end_seconds": 12.0, "team_side": "far", "crop": [0, 0, 1920, 580]}], metadata=self.metadata)
        rallies = [s for s in mapped if s.status == "rally"]
        self.assertEqual([(s.start_seconds, s.end_seconds) for s in rallies], [(2.0, 6.0), (6.0, 10.0)])
        self.assertEqual(rallies[0].team_side, "near")
        self.assertEqual(rallies[1].source_segment_id, rallies[0].source_segment_id)

    def test_require_complete_rejects_internal_gap(self):
        segments = complete_coverage(((2.0, 3.0),), duration_seconds=12.0, binding=self.binding)
        with self.assertRaisesRegex(ValidationError, "incomplete"):
            validate_rally_queue((segments[0], segments[1], replace(segments[2], start_seconds=3.5)), binding=self.binding, require_complete=True)

    def test_invalid_settings_rejected(self):
        with self.assertRaises(ValidationError):
            complete_coverage(((0.0, 1.0),), duration_seconds=float("nan"), binding=self.binding)

    def test_proxy_requires_explicit_binding(self):
        with self.assertRaises(ValidationError):
            write_rally_proxies((), tempfile.mkdtemp(), repo_root=Path("."))


if __name__ == "__main__":
    unittest.main()
