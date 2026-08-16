import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import torch

from spiketrace.inference import infer_video


def _write_test_video(path: Path) -> None:
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (8, 6)
    )
    if not writer.isOpened():
        raise RuntimeError("Could not create temporary test video.")
    try:
        for frame_index in range(12):
            writer.write(np.full((6, 8, 3), frame_index * 15, dtype=np.uint8))
    finally:
        writer.release()


class _ConstantModel:
    def __call__(self, batch):
        return torch.tensor([[0.0, 2.0]], dtype=torch.float32).repeat(batch.shape[0], 1)


class InferenceTests(unittest.TestCase):
    def test_inference_uses_sequential_batches_without_a_heavy_model(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            video_path = temporary_path / "fixture.avi"
            output_dir = temporary_path / "outputs"
            _write_test_video(video_path)
            checkpoint = {
                "num_frames": 2,
                "image_size": 4,
                "window_seconds": 0.4,
                "labels": ["background", "serve"],
                "model_version": "test-v1",
            }
            with patch(
                "spiketrace.inference.load_checkpoint",
                return_value=(_ConstantModel(), checkpoint),
            ), patch(
                "spiketrace.inference.resolve_device", return_value="cpu"
            ), patch(
                "spiketrace.inference.sample_video_clip",
                side_effect=AssertionError("per-window sampler must not be used"),
                create=True,
            ):
                try:
                    result = infer_video(
                        video_path,
                        temporary_path / "checkpoint.pt",
                        output_dir,
                        stride_seconds=0.2,
                        confidence_threshold=0.0,
                        device="cpu",
                    )
                except AssertionError as exc:
                    self.fail(str(exc))

            self.assertEqual(result["window_count"], 5)
            payload = json.loads((output_dir / "events.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [(item["start_seconds"], item["end_seconds"]) for item in payload["windows"]],
                [(0.0, 0.4), (0.2, 0.6), (0.4, 0.8), (0.6, 1.0), (0.8, 1.2)],
            )
            self.assertEqual({item["action"] for item in payload["windows"]}, {"serve"})


if __name__ == "__main__":
    unittest.main()
