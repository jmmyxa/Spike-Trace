import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from spiketrace.errors import SpikeTraceError
from spiketrace.pretrained import (
    DetectionEvidence,
    PretrainedActionDetector,
    WindowPrediction,
    aggregate_action_detections,
    evaluate_pretrained_model,
    extract_action_detections,
    normalize_external_label,
)


def detection(action: str, confidence: float) -> DetectionEvidence:
    return DetectionEvidence(
        frame_index=0,
        source_label=action,
        action=action,
        confidence=confidence,
        box_xyxy=(1.0, 2.0, 3.0, 4.0),
    )


class PretrainedLabelTests(unittest.TestCase):
    def test_normalizes_external_labels(self):
        self.assertEqual(normalize_external_label("spike"), "attack")
        self.assertEqual(normalize_external_label(" SERVE "), "serve")
        self.assertIsNone(normalize_external_label("ball"))
        self.assertIsNone(normalize_external_label("celebrate"))

    def test_selects_highest_confidence_action(self):
        prediction = aggregate_action_detections(
            [detection("set", 0.72), detection("attack", 0.91)],
            confidence_threshold=0.5,
        )

        self.assertEqual(prediction.action, "attack")
        self.assertEqual(prediction.confidence, 0.91)

    def test_falls_back_to_background_below_threshold(self):
        prediction = aggregate_action_detections(
            [detection("receive", 0.3)], confidence_threshold=0.5
        )

        self.assertEqual(prediction.action, "background")
        self.assertEqual(prediction.confidence, 0.0)
        self.assertEqual(len(prediction.evidence), 1)

    def test_extracts_supported_actions_and_ignores_ball(self):
        result = SimpleNamespace(
            names={0: "ball", 1: "spike", 2: "unknown"},
            boxes=SimpleNamespace(
                cls=[0, 1, 2],
                conf=[0.99, 0.87, 0.95],
                xyxy=[[0, 0, 1, 1], [1, 2, 3, 4], [4, 5, 6, 7]],
            ),
        )

        extracted = extract_action_detections([result])

        self.assertEqual(len(extracted), 1)
        self.assertEqual(extracted[0].source_label, "spike")
        self.assertEqual(extracted[0].action, "attack")
        self.assertEqual(extracted[0].confidence, 0.87)

    def test_missing_optional_dependency_has_install_hint(self):
        with tempfile.TemporaryDirectory() as temporary:
            weights = Path(temporary) / "actions.pt"
            weights.touch()
            with (
                mock.patch("spiketrace.pretrained.resolve_device", return_value="cpu"),
                mock.patch(
                    "spiketrace.pretrained.importlib.import_module",
                    side_effect=ModuleNotFoundError("ultralytics"),
                ),
                self.assertRaisesRegex(SpikeTraceError, r"\[pretrained\]"),
            ):
                PretrainedActionDetector(weights)

    def test_rejects_weights_without_volleyball_action_labels(self):
        class FakeYolo:
            def __init__(self, _weights):
                self.names = {0: "person", 1: "sports ball"}

        with tempfile.TemporaryDirectory() as temporary:
            weights = Path(temporary) / "actions.pt"
            weights.touch()
            with (
                mock.patch("spiketrace.pretrained.resolve_device", return_value="cpu"),
                mock.patch(
                    "spiketrace.pretrained._require_yolo_class",
                    return_value=(FakeYolo, "test"),
                ),
                self.assertRaisesRegex(SpikeTraceError, "Missing labels"),
            ):
                PretrainedActionDetector(weights)


class PretrainedEvaluationTests(unittest.TestCase):
    def test_writes_metrics_and_review_outputs_without_real_model(self):
        class FakeDetector:
            def __init__(self, weights, **_kwargs):
                self.weights = Path(weights).resolve()
                self.device = "cpu"
                self.weights_sha256 = "fake-sha256"
                self.ultralytics_version = "test"
                self.model_labels = (
                    "ball",
                    "block",
                    "receive",
                    "set",
                    "spike",
                    "serve",
                )

            def predict_window(self, _frames):
                return WindowPrediction("serve", 0.9, ())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "match.avi"
            weights = root / "actions.pt"
            video.touch()
            weights.touch()
            manifest = root / "annotations.csv"
            manifest.write_text(
                "video_path,start_seconds,end_seconds,label,split\n"
                "match.avi,0,1,serve,test\n",
                encoding="utf-8",
            )
            output = root / "output"

            with (
                mock.patch(
                    "spiketrace.pretrained.PretrainedActionDetector", FakeDetector
                ),
                mock.patch(
                    "spiketrace.pretrained.sample_video_frames",
                    return_value=np.zeros((2, 8, 8, 3), dtype=np.uint8),
                ),
            ):
                result = evaluate_pretrained_model(
                    manifest, weights, output, frames_per_window=2
                )

            self.assertEqual(result["metrics"]["accuracy"], 1.0)
            self.assertTrue((output / "pretrained_evaluation.json").is_file())
            self.assertTrue((output / "pretrained_review.csv").is_file())


class PretrainedCliTests(unittest.TestCase):
    def test_parser_accepts_evaluate_pretrained_command(self):
        from spiketrace.cli import build_parser

        args = build_parser().parse_args(
            ["evaluate-pretrained", "manifest.csv", "actions.pt", "output"]
        )

        self.assertEqual(args.command, "evaluate-pretrained")
        self.assertEqual(args.frames_per_window, 6)


if __name__ == "__main__":
    unittest.main()
