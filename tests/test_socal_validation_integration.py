import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from spiketrace.cli import build_parser, run_command
from spiketrace.validation_contract import sha256_file
from spiketrace.validation_inference import ValidationInferenceResult
from spiketrace.validation_outputs import verify_validation_outputs

REPO_ROOT = Path(__file__).resolve().parents[1]


class SoCalValidationIntegrationTests(unittest.TestCase):
    def test_committed_socal_binding_has_exact_design_constants(self):
        path = REPO_ROOT / "data" / "validation" / "socal_cup_c2_video.json"
        with path.open(encoding="utf-8") as handle:
            binding = json.load(handle)

        self.assertEqual(binding["format_version"], 1)
        self.assertEqual(binding["match_id"], "socal-cup-final-2025")
        self.assertEqual(
            binding["video_path"],
            "data/SoCal Cup Final_ MVVC 17 Red vs C2 Attack 17-1, 06_15_2025 [9ESOXojmAGI].mp4",
        )
        self.assertEqual(
            binding["sha256"],
            "b29e55cde114f5fda745349f86cc878d8abb81ba44ee430f467885bd7ce11c17",
        )
        self.assertEqual(
            binding["metadata"],
            {
                "width": 1920,
                "height": 1080,
                "fps": 60.0,
                "frame_count": 294604,
                "duration_seconds": 4910.066666666667,
            },
        )

    def test_synthetic_commands_freeze_queue_draft_lock_and_evaluate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "synthetic.avi"
            self._write_synthetic_video(video)
            binding_json = root / "binding.json"
            queue_json = root / "queue.json"
            proxy_dir = root / "proxies"
            side_map = root / "side-map.json"
            draft_json = root / "truth-draft.json"
            truth_json = root / "truth.json"
            truth_csv = root / "truth.csv"
            checkpoint = root / "fake-checkpoint.pt"
            checkpoint.write_bytes(b"fake checkpoint")
            manifest = root / "unrelated.csv"
            manifest.write_text(
                "video_path,split,match_id,video_sha256\nother.avi,train,other-match,\n",
                encoding="utf-8",
                newline="",
            )

            parser = build_parser()
            run_command(
                parser.parse_args(
                    [
                        "freeze-validation-video",
                        str(video),
                        str(binding_json),
                        "--repo-root",
                        str(root),
                        "--video-root",
                        str(root),
                        "--match-id",
                        "synthetic-match",
                        "--expected-sha256",
                        sha256_file(video),
                    ]
                )
            )
            metadata = self._read_video_metadata(video)
            side_map.write_text(
                json.dumps(
                    {
                        "set_intervals": [
                            {
                                "set_index": 1,
                                "start_seconds": 0.0,
                                "end_seconds": metadata["duration_seconds"],
                            }
                        ],
                        "side_intervals": [
                            {
                                "set_index": 1,
                                "start_seconds": 0.0,
                                "end_seconds": metadata["duration_seconds"],
                                "team_side": "near",
                                "crop": [0, 0, metadata["width"], metadata["height"]],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            run_command(
                parser.parse_args(
                    [
                        "prepare-validation-rallies",
                        str(binding_json),
                        str(queue_json),
                        str(proxy_dir),
                        "--repo-root",
                        str(root),
                        "--video-root",
                        str(root),
                        "--side-map",
                        str(side_map),
                    ]
                )
            )
            run_command(
                parser.parse_args(
                    [
                        "init-validation-truth",
                        str(queue_json),
                        str(draft_json),
                        "--code-sha",
                        "synthetic-code",
                    ]
                )
            )

            draft = json.loads(draft_json.read_text(encoding="utf-8"))
            for segment in draft["coverage"]:
                if segment["status"] == "rally":
                    segment.update(
                        coverage_confirmed=True,
                        all_c2_actions_checked=True,
                        no_c2_action=True,
                    )
            draft_json.write_text(json.dumps(draft), encoding="utf-8")
            run_command(
                parser.parse_args(
                    [
                        "validate-validation-truth",
                        str(binding_json),
                        str(draft_json),
                        "--repo-root",
                        str(root),
                        "--video-root",
                        str(root),
                    ]
                )
            )
            run_command(
                parser.parse_args(
                    [
                        "lock-validation-truth",
                        str(binding_json),
                        str(draft_json),
                        str(truth_json),
                        str(truth_csv),
                        "--repo-root",
                        str(root),
                        "--video-root",
                        str(root),
                        "--code-sha",
                        "synthetic-code",
                        "--created-at",
                        "2026-09-04T00:00:00Z",
                    ]
                )
            )
            output_dir = root / "outputs"
            fake_inference = ValidationInferenceResult(
                windows=(),
                predictions=(),
                settings={"segments": []},
                checkpoint_sha256=sha256_file(checkpoint),
                video_sha256=sha256_file(video),
            )
            with patch(
                "spiketrace.validation_inference.infer_locked_validation",
                return_value=fake_inference,
            ) as infer:
                result = run_command(
                    parser.parse_args(
                        [
                            "evaluate-validation",
                            str(video),
                            str(truth_json),
                            str(checkpoint),
                            str(output_dir),
                            "--truth-csv",
                            str(truth_csv),
                            "--repo-root",
                            str(root),
                            "--video-root",
                            str(root),
                            "--manifest",
                            str(manifest),
                            "--device",
                            "cpu",
                        ]
                    )
                )
            infer.assert_called_once()
            self.assertEqual(
                {Path(path).name for path in result.values()},
                {
                    "metrics.json",
                    "confusion_matrix.csv",
                    "predicted-events.json",
                    "predicted-events.csv",
                    "run-manifest.json",
                },
            )
            verified = verify_validation_outputs(
                output_dir,
                repo_root=root,
                video_root=root,
            )
            self.assertEqual(verified["match_id"], "synthetic-match")
            self.assertEqual(verified["prediction_count"], 0)

    @staticmethod
    def _write_synthetic_video(path: Path) -> None:
        writer = cv2.VideoWriter(
            str(path), cv2.VideoWriter_fourcc(*"MJPG"), 2.0, (16, 12)
        )
        if not writer.isOpened():
            raise RuntimeError("Could not create synthetic video")
        try:
            for index in range(8):
                value = 0 if index % 2 == 0 else 255
                writer.write(np.full((12, 16, 3), value, dtype=np.uint8))
        finally:
            writer.release()

    @staticmethod
    def _read_video_metadata(path: Path) -> dict[str, float | int]:
        capture = cv2.VideoCapture(str(path))
        try:
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        finally:
            capture.release()
        return {
            "fps": fps,
            "frame_count": frame_count,
            "width": width,
            "height": height,
            "duration_seconds": frame_count / fps,
        }


if __name__ == "__main__":
    unittest.main()
