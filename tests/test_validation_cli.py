import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spiketrace.cli import build_parser, run_command
from spiketrace.domain import VideoMetadata
from spiketrace.errors import ValidationError
from spiketrace.validation_contract import ValidationVideoBinding
from spiketrace.validation_truth import ValidationTruth


class ValidationCliTests(unittest.TestCase):
    def test_validation_commands_are_parseable(self):
        parser = build_parser()
        commands = {
            "freeze-validation-video", "prepare-validation-rallies", "init-validation-truth",
            "validate-validation-truth", "lock-validation-truth", "verify-validation-truth",
            "verify-validation-isolation", "evaluate-validation", "verify-validation",
        }
        for command in commands:
            self.assertEqual(parser.parse_args([command] + self._args(command)).command, command)

    def _args(self, command):
        values = {
            "freeze-validation-video": ["v", "b", "--repo-root", "r", "--video-root", "vr", "--match-id", "m", "--expected-sha256", "0" * 64],
            "prepare-validation-rallies": ["b", "q", "p", "--repo-root", "r", "--video-root", "vr", "--side-map", "s"],
            "init-validation-truth": ["q", "d", "--code-sha", "s"],
            "validate-validation-truth": ["b", "d", "--repo-root", "r", "--video-root", "vr"],
            "lock-validation-truth": ["b", "d", "t", "c", "--repo-root", "r", "--video-root", "vr", "--code-sha", "s", "--created-at", "now"],
            "verify-validation-truth": ["b", "t", "c", "--repo-root", "r", "--video-root", "vr"],
            "verify-validation-isolation": ["b", "--repo-root", "r", "--video-root", "vr", "--manifest", "m"],
            "evaluate-validation": ["v", "t", "c", "o", "--truth-csv", "t.csv", "--repo-root", "r", "--video-root", "vr", "--manifest", "m"],
            "verify-validation": ["o", "--repo-root", "r", "--video-root", "vr"],
        }
        return values[command]

    def test_evaluate_draft_fails_before_inference(self):
        root = Path(tempfile.mkdtemp()); video = root / "v.mp4"; truth = root / "truth.json"; checkpoint = root / "c.pt"
        payload = {"video": {"match_id": "m", "video_path": "v.mp4", "sha256": "0" * 64, "metadata": {"fps": 1, "frame_count": 1, "width": 2, "height": 2, "duration_seconds": 1}}}
        truth.write_text(json.dumps(payload)); checkpoint.write_bytes(b"c")
        args = build_parser().parse_args(["evaluate-validation", str(video), str(truth), str(checkpoint), str(root / "out"), "--truth-csv", str(root / "truth.csv"), "--repo-root", str(root), "--video-root", str(root), "--manifest", str(root / "m.csv")])
        with patch("spiketrace.validation_truth.load_locked_truth", side_effect=ValidationError("draft truth")), patch("spiketrace.validation_inference.infer_locked_validation") as infer:
            with self.assertRaises(ValidationError): run_command(args)
            infer.assert_not_called()

    def test_evaluate_rejects_non_object_truth_before_model(self):
        root = Path(tempfile.mkdtemp())
        truth = root / "truth.json"
        truth.write_text("[]", encoding="utf-8")
        args = build_parser().parse_args(
            [
                "evaluate-validation",
                str(root / "video.mp4"),
                str(truth),
                str(root / "checkpoint.pt"),
                str(root / "out"),
                "--truth-csv",
                str(root / "truth.csv"),
                "--repo-root",
                str(root),
                "--video-root",
                str(root),
                "--manifest",
                str(root / "manifest.csv"),
            ]
        )
        with patch("spiketrace.validation_inference.infer_locked_validation") as infer:
            with self.assertRaisesRegex(ValidationError, "truth JSON"):
                run_command(args)
            infer.assert_not_called()

    def test_evaluate_rejects_explicit_video_mismatch_before_isolation(self):
        root = Path(tempfile.mkdtemp())
        bound_video = root / "bound.mp4"
        explicit_video = root / "other.mp4"
        truth_json = root / "truth.json"
        truth_csv = root / "truth.csv"
        metadata = VideoMetadata(bound_video, 1.0, 1, 2, 2, 1.0)
        binding = ValidationVideoBinding("match", bound_video, root, "bound.mp4", "a" * 64, metadata)
        truth_json.write_text(
            json.dumps(
                {
                    "video": {
                        "match_id": "match",
                        "video_path": "bound.mp4",
                        "sha256": "a" * 64,
                        "metadata": metadata.to_dict(),
                    }
                }
            ),
            encoding="utf-8",
        )
        truth_csv.write_text("", encoding="utf-8")
        truth = ValidationTruth(binding, (), (), (), (), (), "truth-v1", True, "b" * 64, "c" * 64)
        args = build_parser().parse_args(
            [
                "evaluate-validation",
                str(explicit_video),
                str(truth_json),
                str(root / "checkpoint.pt"),
                str(root / "out"),
                "--truth-csv",
                str(truth_csv),
                "--repo-root",
                str(root),
                "--video-root",
                str(root),
                "--manifest",
                str(root / "manifest.csv"),
            ]
        )
        with patch("spiketrace.validation_truth.load_locked_truth", return_value=truth), patch(
            "spiketrace.validation_contract.freeze_video_binding", return_value=binding
        ), patch("spiketrace.validation_truth.verify_truth_bundle"), patch(
            "spiketrace.validation_contract.assert_no_content_overlap"
        ) as isolation, patch("spiketrace.validation_inference.infer_locked_validation") as infer, self.assertRaisesRegex(ValidationError, "video path"):
            run_command(args)
        isolation.assert_not_called()
        infer.assert_not_called()

    def test_verify_validation_dispatches_without_model(self):
        args = build_parser().parse_args(["verify-validation", "out", "--repo-root", "root", "--video-root", "videos"])
        with patch("spiketrace.validation_outputs.verify_validation_outputs", return_value={"ok": True}) as verify:
            result = run_command(args)
        self.assertEqual(result, {"ok": True}); verify.assert_called_once()

    def test_verify_isolation_dispatches_explicit_sources(self):
        args = build_parser().parse_args(["verify-validation-isolation", "binding", "--repo-root", "root", "--video-root", "videos", "--manifest", "m.csv", "--selection-source", "s.json"])
        with patch("spiketrace.validation_contract.load_video_binding", return_value=object()), patch("spiketrace.validation_contract.assert_no_content_overlap") as overlap:
            result = run_command(args)
        self.assertTrue(result["ok"]); overlap.assert_called_once()


if __name__ == "__main__":
    unittest.main()
