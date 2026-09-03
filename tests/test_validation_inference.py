import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import torch

from spiketrace.domain import VideoMetadata
from spiketrace.errors import ValidationError
from spiketrace.validation_contract import ValidationVideoBinding
from spiketrace.validation_inference import infer_locked_validation
from spiketrace.validation_rallies import RallySegment
from spiketrace.validation_truth import ValidationTruth
from spiketrace.video import iter_window_times_range


def _write_video(path: Path) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (8, 6))
    if not writer.isOpened():
        raise RuntimeError("Could not create test video.")
    try:
        for frame_index in range(30):
            frame = np.zeros((6, 8, 3), dtype=np.uint8)
            frame[:, :4] = frame_index
            frame[:, 4:] = 255 - frame_index
            writer.write(frame)
    finally:
        writer.release()


class _ConstantModel:
    def __init__(self, mutate_path: Path | None = None):
        self.mutate_path = mutate_path

    def __call__(self, batch):
        if self.mutate_path is not None:
            with self.mutate_path.open("ab") as handle:
                handle.write(b"changed")
            self.mutate_path = None
        return torch.tensor([[0.0, 2.0]], dtype=torch.float32).repeat(batch.shape[0], 1)


def _truth(video_path: Path, *, locked: bool = True, coverage=None, side_intervals=()) -> ValidationTruth:
    capture = cv2.VideoCapture(str(video_path))
    metadata = VideoMetadata(video_path.resolve(), float(capture.get(cv2.CAP_PROP_FPS)), int(capture.get(cv2.CAP_PROP_FRAME_COUNT)), int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)), int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) / float(capture.get(cv2.CAP_PROP_FPS)))
    capture.release()
    binding = ValidationVideoBinding("socal-fixture", video_path.resolve(), video_path.parent.resolve(), video_path.name, hashlib.sha256(video_path.read_bytes()).hexdigest(), metadata)
    segments = coverage if coverage is not None else (
        RallySegment("set-01-near", None, 1, "rally-01", 0.0, 1.0, "rally", "near", (0, 0, 4, 6), 0.0, 0.0, "manual", True, True, None),
        RallySegment("set-01-far", None, 1, "rally-02", 1.0, 2.0, "rally", "far", (4, 0, 8, 6), 0.0, 0.0, "manual", True, True, None),
        RallySegment("non-rally-01", None, None, "", 2.0, 3.0, "non_rally", None, None, 0.0, 0.0, "manual", True, True, None),
    )
    return ValidationTruth(binding, (), tuple(side_intervals), tuple(segments), (), (), "truth-v1", locked, "truth-lock", None)


def _checkpoint():
    return {"num_frames": 2, "image_size": 4, "window_seconds": 0.6, "labels": ["background", "serve"], "model_version": "test-v1"}


class ValidationInferenceTests(unittest.TestCase):
    def test_range_windows_stay_inside_half_open_interval_and_include_endpoint(self):
        self.assertEqual(list(iter_window_times_range(10.0, 11.0, window_seconds=0.6, stride_seconds=0.5)), [(10.0, 10.6), (10.5, 11.0)])
        with self.assertRaises(ValueError):
            list(iter_window_times_range(float("nan"), 1.0, window_seconds=0.5, stride_seconds=0.2))

    def test_locked_segments_keep_absolute_provenance_and_side(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video, checkpoint = root / "fixture.avi", root / "model.pt"
            _write_video(video)
            checkpoint.write_bytes(b"checkpoint")
            with patch("spiketrace.validation_inference.load_checkpoint", return_value=(_ConstantModel(), _checkpoint())), patch("spiketrace.validation_inference.resolve_device", return_value="cpu"):
                result = infer_locked_validation(video, checkpoint, _truth(video), device="cpu", stride_seconds=0.5, confidence_threshold=0.0)
            near_windows = [window for window in result.windows if window.segment_id == "set-01-near"]
            far_windows = [window for window in result.windows if window.segment_id == "set-01-far"]
            self.assertTrue(all(0.0 <= window.start_seconds and window.end_seconds <= 1.0 for window in near_windows))
            self.assertTrue(all(1.0 <= window.start_seconds and window.end_seconds <= 2.0 for window in far_windows))
            self.assertEqual(result.predictions[0].source_window_indices, (0, 1))
            self.assertEqual(result.predictions[0].team_side, "near")
            self.assertTrue(result.predictions[0].prediction_id.startswith("socal-fixture:set-01:set-01-near:"))
            self.assertEqual(len(result.settings["segments"]), 2)

    def test_requires_locked_truth_before_model_loading(self):
        with patch("spiketrace.validation_inference.load_checkpoint") as loader:
            with self.assertRaisesRegex(ValidationError, "locked"):
                infer_locked_validation("missing.avi", "missing.pt", _truth_for_missing_video(), device="cpu")
        loader.assert_not_called()

    def test_rejects_out_of_order_or_overlapping_segments_and_invalid_crop(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video, checkpoint = root / "fixture.avi", root / "model.pt"
            _write_video(video)
            checkpoint.write_bytes(b"checkpoint")
            cases = (
                ("out of order", (
                    RallySegment("late", None, 1, "r1", 1.0, 2.0, "rally", "near", (0, 0, 4, 6), 0, 0, "manual", True, True, None),
                    RallySegment("early", None, 1, "r2", 0.0, 1.0, "rally", "far", (4, 0, 8, 6), 0, 0, "manual", True, True, None),
                )),
                ("overlap", (
                    RallySegment("first", None, 1, "r1", 0.0, 1.5, "rally", "near", (0, 0, 4, 6), 0, 0, "manual", True, True, None),
                    RallySegment("second", None, 1, "r2", 1.0, 2.0, "rally", "far", (4, 0, 8, 6), 0, 0, "manual", True, True, None),
                )),
                ("Crop", (RallySegment("bad-crop", None, 1, "r1", 0.0, 1.0, "rally", "near", (0, 0, 9, 6), 0, 0, "manual", True, True, None),)),
            )
            for message, coverage in cases:
                with self.subTest(message=message), self.assertRaisesRegex(ValidationError, message):
                    infer_locked_validation(video, checkpoint, _truth(video, coverage=coverage), device="cpu")

    def test_requires_complete_non_overlapping_side_mapping(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video, checkpoint = root / "fixture.avi", root / "model.pt"
            _write_video(video)
            checkpoint.write_bytes(b"checkpoint")
            coverage = (RallySegment("rally", None, 1, "r1", 0.0, 1.0, "rally", None, None, 0, 0, "manual", True, True, None),)
            missing = ({"set_index": 1, "start_seconds": 0.0, "end_seconds": 0.4, "team_side": "near", "crop": [0, 0, 4, 6]},)
            malformed = ({"set_index": 1, "start_seconds": 0.0, "end_seconds": 1.0, "team_side": "sideways", "crop": [0, 0, 4, 6]},)
            for sides in (missing, malformed):
                with self.subTest(sides=sides), self.assertRaises(ValidationError):
                    infer_locked_validation(video, checkpoint, _truth(video, coverage=coverage, side_intervals=sides), device="cpu")
            adjacent = (
                {"set_index": 1, "start_seconds": 0.0, "end_seconds": 0.5, "team_side": "near", "crop": [0, 0, 4, 6]},
                {"set_index": 1, "start_seconds": 0.5, "end_seconds": 1.0, "team_side": "far", "crop": [4, 0, 8, 6]},
            )
            with patch("spiketrace.validation_inference.load_checkpoint", return_value=(_ConstantModel(), _checkpoint())), patch("spiketrace.validation_inference.resolve_device", return_value="cpu"):
                result = infer_locked_validation(video, checkpoint, _truth(video, coverage=coverage, side_intervals=adjacent), device="cpu", confidence_threshold=0.0)
            self.assertEqual([window.team_side for window in result.windows], ["near", "far"])

    def test_converts_public_parameter_and_pipeline_errors(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            video, checkpoint = root / "fixture.avi", root / "model.pt"
            _write_video(video)
            checkpoint.write_bytes(b"checkpoint")
            truth = _truth(video)
            for kwargs in ({"stride_seconds": 0}, {"stride_seconds": 2.0}, {"confidence_threshold": 2}, {"merge_gap_seconds": -1}, {"min_event_seconds": float("nan")}, {"batch_size": True}, {"device": ""}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                    with patch("spiketrace.validation_inference.load_checkpoint", return_value=(_ConstantModel(), _checkpoint())), patch("spiketrace.validation_inference.resolve_device", return_value="cpu"):
                        infer_locked_validation(video, checkpoint, truth, **kwargs)
            with patch("spiketrace.validation_inference.load_checkpoint", return_value=(_ConstantModel(), _checkpoint())), patch("spiketrace.validation_inference.resolve_device", return_value="cpu"), patch("spiketrace.validation_inference.iter_sequential_video_clip_batches", side_effect=ValueError("bad decoder")):
                with self.assertRaises(ValidationError):
                    infer_locked_validation(video, checkpoint, truth)

    def test_rejects_source_mutation_after_decoding_starts(self):
        for source_name in ("video", "checkpoint"):
            with self.subTest(source_name=source_name), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                video, checkpoint = root / "fixture.avi", root / "model.pt"
                _write_video(video)
                checkpoint.write_bytes(b"checkpoint")
                mutate_path = video if source_name == "video" else checkpoint
                with patch("spiketrace.validation_inference.load_checkpoint", return_value=(_ConstantModel(mutate_path), _checkpoint())), patch("spiketrace.validation_inference.resolve_device", return_value="cpu"):
                    with self.assertRaisesRegex(ValidationError, f"{source_name} changed"):
                        infer_locked_validation(video, checkpoint, _truth(video), device="cpu")


def _truth_for_missing_video() -> ValidationTruth:
    path = Path("missing.avi").resolve()
    metadata = VideoMetadata(path, 10.0, 1, 1, 1, 0.1)
    binding = ValidationVideoBinding("missing", path, path.parent, path.name, "0" * 64, metadata)
    return ValidationTruth(binding, (), (), (), (), (), "truth-v1", False, None, None)


if __name__ == "__main__":
    unittest.main()
