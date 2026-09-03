import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from spiketrace.domain import VideoMetadata
from spiketrace.errors import ValidationError, VideoError
from spiketrace.validation_contract import ValidationVideoBinding
from spiketrace.validation_rallies import (
    RallyDetectionSettings,
    apply_side_map,
    complete_coverage,
    detect_rally_candidates,
    load_rally_queue,
    validate_rally_queue,
    write_rally_proxies,
    write_rally_queue,
)


def _write_motion_video(path: Path) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 2.0, (16, 12))
    if not writer.isOpened():
        raise RuntimeError("Could not create motion test video")
    try:
        for index in range(20):
            value = 255 if index in {2, 3, 10} else 0
            writer.write(np.full((12, 16, 3), value, dtype=np.uint8))
    finally:
        writer.release()


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

    def test_nonfinite_candidate_rejected(self):
        with self.assertRaises(ValidationError):
            complete_coverage(((float("nan"), 2.0),), duration_seconds=12.0, binding=self.binding)

    def test_malformed_candidate_shape_rejected(self):
        for candidates in (("bad",), ((1.0, 2.0, 3.0),)):
            with self.subTest(candidates=candidates), self.assertRaises(ValidationError):
                complete_coverage(candidates, duration_seconds=12.0, binding=self.binding)

    def test_motion_candidates_are_deterministic_and_clamped(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "motion.avi"
            _write_motion_video(source)
            settings = RallyDetectionSettings(
                sample_seconds=0.5,
                motion_threshold=20.0,
                dead_ball_seconds=1.0,
                merge_gap_seconds=0.25,
                buffer_before_seconds=3.0,
                buffer_after_seconds=3.0,
            )
            first = detect_rally_candidates(source, settings=settings)
            second = detect_rally_candidates(source, settings=settings)
            self.assertEqual(first, second)
            self.assertTrue(first)
            self.assertGreaterEqual(first[0][0], 0.0)
            self.assertLessEqual(first[-1][1], 10.0)

    def test_dead_ball_merging_precedes_buffer_expansion(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "motion.avi"
            _write_motion_video(source)
            separated = detect_rally_candidates(
                source,
                settings=RallyDetectionSettings(
                    sample_seconds=0.5,
                    motion_threshold=20.0,
                    dead_ball_seconds=1.0,
                    merge_gap_seconds=0.25,
                    buffer_before_seconds=3.0,
                    buffer_after_seconds=3.0,
                ),
            )
            merged = detect_rally_candidates(
                source,
                settings=RallyDetectionSettings(
                    sample_seconds=0.5,
                    motion_threshold=20.0,
                    dead_ball_seconds=3.0,
                    merge_gap_seconds=0.25,
                    buffer_before_seconds=3.0,
                    buffer_after_seconds=3.0,
                ),
            )
            self.assertEqual(len(separated), 2)
            self.assertEqual(len(merged), 1)

    def test_detection_rejects_malformed_settings(self):
        invalid = [
            RallyDetectionSettings(sample_seconds=0.0),
            RallyDetectionSettings(motion_threshold=float("nan")),
            RallyDetectionSettings(dead_ball_seconds=float("inf")),
            RallyDetectionSettings(buffer_after_seconds=-1.0),
            RallyDetectionSettings(sample_seconds=True),
        ]
        for settings in invalid:
            with self.subTest(settings=settings), self.assertRaises(ValidationError):
                detect_rally_candidates("missing.avi", settings=settings)

    def test_side_map_rejects_noninteger_and_malformed_crops(self):
        segment = complete_coverage(((2.0, 4.0),), duration_seconds=12.0, binding=self.binding)
        base_set = [{"set_index": 1, "start_seconds": 0.0, "end_seconds": 12.0}]
        for crop in ([0.5, 0, 100, 100], [0, 0, 100], [0, 0, "100", 100], "bad"):
            with self.subTest(crop=crop), self.assertRaises(ValidationError):
                apply_side_map(
                    segment,
                    set_intervals=base_set,
                    side_intervals=[{"set_index": 1, "start_seconds": 0.0, "end_seconds": 12.0, "team_side": "near", "crop": crop}],
                    metadata=self.metadata,
                )

    def test_side_map_rejects_missing_or_nonfinite_interval_fields(self):
        segment = complete_coverage(((2.0, 4.0),), duration_seconds=12.0, binding=self.binding)
        with self.assertRaises(ValidationError):
            apply_side_map(segment, set_intervals=[{"set_index": 1, "start_seconds": 0.0}], side_intervals=[], metadata=self.metadata)
        with self.assertRaises(ValidationError):
            apply_side_map(segment, set_intervals=[{"set_index": 1, "start_seconds": 0.0, "end_seconds": 12.0}], side_intervals=[{"set_index": 1, "start_seconds": float("nan"), "end_seconds": 12.0, "team_side": "near", "crop": [0, 0, 100, 100]}], metadata=self.metadata)

    def test_queue_round_trip_and_no_overwrite(self):
        segments = complete_coverage(((2.0, 5.0),), duration_seconds=12.0, binding=self.binding)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "queue.json"
            write_rally_queue(path, binding=self.binding, segments=segments, set_intervals=[], side_intervals=[], settings=RallyDetectionSettings(), code_sha="abc")
            self.assertEqual(load_rally_queue(path, binding=self.binding), segments)
            with self.assertRaises(ValidationError):
                write_rally_queue(path, binding=self.binding, segments=segments, set_intervals=[], side_intervals=[], settings=RallyDetectionSettings(), code_sha="abc")

    def test_queue_rejects_binding_metadata_and_shape_tampering(self):
        segments = complete_coverage(((2.0, 5.0),), duration_seconds=12.0, binding=self.binding)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "queue.json"
            write_rally_queue(path, binding=self.binding, segments=segments, set_intervals=[], side_intervals=[], settings=RallyDetectionSettings(), code_sha="abc")
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["binding"]["metadata"]["width"] = 999
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_rally_queue(path, binding=self.binding)
            payload["binding"]["metadata"] = self.binding.metadata.to_dict()
            payload["segments"][0]["crop"] = [0, 1, 2]
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_rally_queue(path, binding=self.binding)
            payload["segments"][0]["crop"] = None
            payload["segments"][0]["start_seconds"] = float("nan")
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_rally_queue(path, binding=self.binding)

    def test_proxy_manifest_metadata_and_decode_failure_rollback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "fixture.avi"
            _write_motion_video(source)
            metadata = VideoMetadata(source, 2.0, 20, 16, 12, 10.0)
            binding = ValidationVideoBinding("m", source, root, "fixture.avi", "a" * 64, metadata)
            segment = replace(complete_coverage(((1.0, 2.0),), duration_seconds=10.0, binding=binding)[1], status="pending")
            output = root / "proxies"
            with patch("spiketrace.validation_rallies.write_proxy_video") as writer:
                writer.side_effect = lambda _source, destination, *_args, **_kwargs: destination.write_bytes(b"proxy")
                manifest = write_rally_proxies((segment,), output, video_root=root, repo_root=root, binding=binding)
            self.assertEqual(manifest["proxies"][0]["start_seconds"], 1.0)
            self.assertEqual(len(manifest["proxies"][0]["sha256"]), 64)
            self.assertTrue((output / "proxy-manifest.json").is_file())
            failed = root / "failed"
            with patch("spiketrace.validation_rallies.write_proxy_video", side_effect=VideoError("decode failed")), self.assertRaises(VideoError):
                write_rally_proxies((segment,), failed, video_root=root, repo_root=root, binding=binding)
            self.assertFalse(failed.exists())

    def test_proxy_rejects_binding_root_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "fixture.avi"
            _write_motion_video(source)
            metadata = VideoMetadata(source, 2.0, 20, 16, 12, 10.0)
            binding = ValidationVideoBinding("m", source, root, "fixture.avi", "a" * 64, metadata)
            with self.assertRaises(ValidationError):
                write_rally_proxies((), root / "proxies", video_root=root / "other", repo_root=root, binding=binding)

    def test_proxy_preserves_preexisting_output_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "proxies"
            output.mkdir()
            sentinel = output / "keep.txt"
            sentinel.write_text("prior", encoding="utf-8")
            with self.assertRaises(ValidationError):
                write_rally_proxies((), output, repo_root=root, binding=self.binding)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "prior")


if __name__ == "__main__":
    unittest.main()
